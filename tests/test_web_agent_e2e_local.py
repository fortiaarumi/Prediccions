"""
tests/test_web_agent_e2e_local.py
=================================
Demostració i prova d'integració local E2E dels 10 passos sol·licitats:
1. Clicar «Buscar surebets» (Simulat via POST /api/sessions).
2. Crear una sessió simulada.
3. Fer que l'agent la reclami atòmicament.
4. Executar un cicle de cerca simulat.
5. Publicar un snapshot d'oportunitat.
6. Mostrar-lo al navegador (Simulat via GET /api/opportunities).
7. Cancel·lar la cerca (Simulat via POST /api/sessions/:id/cancel).
8. Verificar l'estat final (CANCELLED cooperativament).
9. Actualitzar els resultats automàticament.
10. Excloure estrictament les quotes de més de 30 minuts.
"""

import time
import json
import urllib.request
import threading
from datetime import datetime, timezone, timedelta
import pytest

from agent.coordinator import MockCoordinator
from agent.windows_agent import WindowsSurebetAgent
from agent.local_bridge import LocalCoordinatorServer
from agent.protocol import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED
)


@pytest.fixture
def local_e2e_env():
    coord = MockCoordinator()
    port = 8769  # Port aïllat per al test
    server = LocalCoordinatorServer(coord, port=port)
    server.start()
    agent = WindowsSurebetAgent(coordinator=coord, agent_id="Windows-PC-Local", offline=True, dry_run=True)
    
    yield {"coord": coord, "port": port, "server": server, "agent": agent}
    
    server.stop()
    agent.cleanup_resources()


def test_complete_10_step_e2e_local_flow(local_e2e_env):
    coord = local_e2e_env["coord"]
    port = local_e2e_env["port"]
    agent = local_e2e_env["agent"]
    base_url = f"http://127.0.0.1:{port}"

    # 1. Abans que l'agent enviï heartbeat: estat desconnectat
    resp = json.loads(urllib.request.urlopen(f"{base_url}/api/agent/status").read())
    assert resp["connected"] is False

    # 2. Agent envia batec de vida real
    agent.send_heartbeat(status="ONLINE")
    resp_hb = json.loads(urllib.request.urlopen(f"{base_url}/api/agent/status").read())
    assert resp_hb["connected"] is True
    assert resp_hb["status"] == "ONLINE"
    assert resp_hb["agent_id"] == "Windows-PC-Local"

    # 3. Clicar «Buscar surebets»: Web fa POST /api/sessions
    req_data = json.dumps({"duration_minutes": 10, "user_id": "user_local_web"}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=req_data,
        headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
    )
    create_resp = json.loads(urllib.request.urlopen(req).read())
    session_id = create_resp["session_id"]
    assert create_resp["status"] == STATUS_PENDING

    # 4. Agent reclama la tasca pendent a la cua
    order = coord.claim_pending_order("Windows-PC-Local")
    assert order is not None
    assert order.session_id == session_id
    assert order.status == STATUS_RUNNING

    # 5. Agent executa cicle i publica snapshot amb oportunitat recent (<= 30 minuts)
    now_utc = datetime.now(timezone.utc)
    mock_arb = {
        "competition_id": "LALIGA",
        "matchup": "Real Madrid vs Barcelona",
        "market_name": "1X2",
        "profit_pct": 3.42,
        "legs": [
            {"bookmaker": "winamax", "outcome": "1", "odd": 2.20, "captured_at": now_utc.isoformat()},
            {"bookmaker": "bet365", "outcome": "X", "odd": 3.60, "captured_at": now_utc.isoformat()},
            {"bookmaker": "betway", "outcome": "2", "odd": 3.90, "captured_at": now_utc.isoformat()}
        ]
    }
    snap_rec = agent.snapshot_store.add_opportunity(
        session_id=session_id,
        cycle_index=1,
        arb=mock_arb,
        now=now_utc
    )
    coord.publish_snapshot(session_id, snap_rec)

    # 6. Web consulta oportunitats en temps real
    opps_resp = json.loads(urllib.request.urlopen(f"{base_url}/api/opportunities").read())
    assert opps_resp["count"] == 1
    assert opps_resp["opportunities"][0]["matchup"] == "Real Madrid vs Barcelona"
    assert opps_resp["opportunities"][0]["profit_pct"] == 3.42

    # 7. Clicar «Aturar cerca»: Web fa POST /api/sessions/:id/cancel
    cancel_req = urllib.request.Request(
        f"{base_url}/api/sessions/{session_id}/cancel",
        data=json.dumps({"user_id": "user_local_web"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
    )
    cancel_resp = json.loads(urllib.request.urlopen(cancel_req).read())
    assert cancel_resp["status"] == STATUS_CANCEL_REQUESTED

    # 8. Agent detecta cancel·lació cooperativament i finalitza la sessió
    assert agent._is_cancel_requested(session_id) is True
    agent._finalize_session(session_id, final_status=STATUS_CANCELLED)

    # 9. Web verifica l'estat final de la sessió
    session_status_resp = json.loads(urllib.request.urlopen(f"{base_url}/api/sessions/{session_id}").read())
    assert session_status_resp["status"] == STATUS_CANCELLED

    # 10. Exclusió estricta de quotes de més de 30 minuts (1800 segons)
    old_time = now_utc - timedelta(seconds=1850)
    old_arb = {
        "competition_id": "PREMIER",
        "matchup": "Arsenal vs Chelsea",
        "market_name": "1X2",
        "profit_pct": 2.10,
        "legs": [
            {"bookmaker": "winamax", "outcome": "1", "odd": 1.95, "captured_at": old_time.isoformat()},
            {"bookmaker": "bet365", "outcome": "X2", "odd": 2.15, "captured_at": old_time.isoformat()}
        ]
    }
    old_snap = agent.snapshot_store.add_opportunity(
        session_id=session_id,
        cycle_index=2,
        arb=old_arb,
        now=now_utc
    )
    # Com que l'edat de old_arb supera 1800s, SnapshotStore el rebutja directament (retorna None)
    assert old_snap is None

    # Si intentem afegir un snapshot manualment antic a coord, get_active_opportunities el filtra
    from agent.protocol import SnapshotRecord
    manual_old_snap = SnapshotRecord(
        snapshot_id="snap_old_expired",
        opportunity_id="opp_old_expired",
        session_id=session_id,
        cycle_index=2,
        matchup="Arsenal vs Chelsea",
        market_name="1X2",
        profit_pct=2.10,
        freshness_tier="EXPIRED",
        legs=[],
        captured_at=old_time.isoformat()
    )
    coord.publish_snapshot(session_id, manual_old_snap)

    # get_active_opportunities filtra només <= 1800s
    active_opps = coord.get_active_opportunities(max_age_seconds=1800.0)
    assert len(active_opps) == 1
    assert active_opps[0].matchup == "Real Madrid vs Barcelona"
    assert all(o.matchup != "Arsenal vs Chelsea" for o in active_opps)


def test_local_bridge_csrf_and_security_controls(local_e2e_env):
    port = local_e2e_env["port"]
    base_url = f"http://127.0.0.1:{port}"
    valid_payload = json.dumps({"duration_minutes": 5, "user_id": "test_user"}).encode("utf-8")

    # 1. Peticions mutating sense capçalera X-Requested-With (i sense Origin permès) han de ser blocades (403)
    req_no_csrf = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=valid_payload,
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_csrf)
    assert exc_info.value.code == 403

    # 2. Origen maliciós / no permès ha de ser blocat (403)
    req_bad_origin = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=valid_payload,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "http://malicious-site.com"
        }
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_origin)
    assert exc_info.value.code == 403

    # 3. Content-Type invàlid (formulari simple o text pla) ha de ser rebutjat (415)
    req_bad_ct = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=valid_payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest"
        }
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_ct)
    assert exc_info.value.code == 415

    # 4. Sec-Fetch-Site cross-site ha de ser blocat (403)
    req_cross_site = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=valid_payload,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "cross-site"
        }
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_cross_site)
    assert exc_info.value.code == 403

    # 5. Payload superior a 4KB ha de retornar 413
    big_payload = json.dumps({"duration_minutes": 5, "padding": "x" * 5000}).encode("utf-8")
    req_large = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=big_payload,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_large)
    assert exc_info.value.code == 413

    # 6. Durada invàlida (<1 o >30) ha de retornar 400
    invalid_dur_payload = json.dumps({"duration_minutes": 60}).encode("utf-8")
    req_dur = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=invalid_dur_payload,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_dur)
    assert exc_info.value.code == 400


"""
tests/test_cost_guard.py
========================
Bateria de proves unitàries i d'integració per al sistema de vigilància de costos
i protecció econòmica OCI (CostGuard).

Comprova els 11 escenaris obligatoris:
1. Cost zero estimat per a la configuració bàsica Always Free.
2. Cost superior a 0 quan es provisionen recursos excedentaris.
3. Dades de facturació desactualitzades (> 48h).
4. Usage API no disponible (fallback transparent sense caiguda).
5. Recurs de pagament inesperat detectat (aturada preventiva immediata).
6. Aturada econòmica en superar 0.25 €.
7. Bloqueig de reinici automàtic en superar 0.50 €.
8. Reinici manual autoritzat mitjançant token administratiu.
9. Simulació progressiva de tots els llindars (0.01 €, 0.10 €, 0.25 €, 0.50 €, 1.00 €).
10. Fallada de la VM / Detecció per watchdog extern.
11. Protecció de dades sensibles: get_public_web_status() no exposa euros ni OCIDs.
"""

import pytest
import json
import tempfile
import urllib.error
from unittest.mock import patch, MagicMock
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from engine.cost_guard import (
    CostGuard,
    AUTHORIZED_BASELINE,
    THRESHOLDS,
    ALWAYS_FREE_LIMITS,
    GitHubIssueLedgerPersistenceBackend,
    InMemoryLockPersistenceBackend
)


@pytest.fixture
def tmp_cost_guard():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)
    lock_path = tmp_path.parent / f".{tmp_path.stem}_persistent_lock.json"
    lock_path.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")
    guard = CostGuard(state_path=tmp_path)
    yield guard
    if tmp_path.exists():
        tmp_path.unlink()
    if lock_path.exists():
        lock_path.unlink()


# -----------------------------------------------------------------------------
# 1. Cost Zero Estimat per a la Configuració Always Free
# -----------------------------------------------------------------------------
def test_estimated_cost_zero_for_always_free(tmp_cost_guard):
    """Verifica que la configuració autoritzada (1 OCPU, 4GB, 50GB disc, 1 IP) té cost 0.00 €."""
    cost, anomalies = tmp_cost_guard.compute_estimated_cost(
        provisioning=AUTHORIZED_BASELINE,
        uptime_hours=744.0  # 31 dies sencers
    )
    assert cost == 0.0
    assert len(anomalies) == 0

    summary = tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=744.0,
        official_data={"amount_eur": 0.0, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    )
    assert summary["status"] == "OK"
    assert summary["estimated_cost_eur"] == 0.0
    assert summary["official_cost_eur"] == 0.0
    assert summary["can_restart"] is True


# -----------------------------------------------------------------------------
# 2. Cost Superior a 0 amb Recursos Excedentaris
# -----------------------------------------------------------------------------
def test_estimated_cost_greater_than_zero(tmp_cost_guard):
    """Comprova que afegir disc addicional (>200GB) o IPs orfes genera cost teòric superior a 0."""
    leaked_provisioning = dict(AUTHORIZED_BASELINE)
    leaked_provisioning["extra_volumes_gb"] = 200.0  # Total 250 GB (supera 200 GB Always Free per 50 GB)
    leaked_provisioning["unattached_public_ips"] = 1  # 1 IP orfe no associada

    cost, anomalies = tmp_cost_guard.compute_estimated_cost(
        provisioning=leaked_provisioning,
        uptime_hours=744.0
    )
    assert cost > 0.0
    assert any("Disc supera 200GB" in a for a in anomalies)
    assert any("IP pública reservada sense associar" in a for a in anomalies)


# -----------------------------------------------------------------------------
# 3. Dades de Facturació Desactualitzades
# -----------------------------------------------------------------------------
def test_stale_billing_data_detection(tmp_cost_guard):
    """Verifica que dades d'Usage API amb més de 48 hores d'antiguitat activen l'estat WARNING."""
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
    official_data = {
        "amount_eur": 0.0,
        "timestamp_utc": old_timestamp
    }

    summary = tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=200.0,
        official_data=official_data
    )
    assert summary["stale_billing_data"] is True
    assert summary["official_data_age_hours"] >= 49.0
    # Com que les dades són antigues, no podem garantir 0.00 € i entra en WARNING
    assert summary["status"] == "WARNING"


# -----------------------------------------------------------------------------
# 4. Usage API No Disponible (Fallback)
# -----------------------------------------------------------------------------
def test_usage_api_unavailable_handling(tmp_cost_guard):
    """Comprova que si l'Usage API no respon (None), el sistema utilitza el cost teòric."""
    summary = tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=100.0,
        official_data=None
    )
    # No es trenca, usa el càlcul teòric local i senyala dades no consolidades
    assert summary["estimated_cost_eur"] == 0.0
    assert summary["stale_billing_data"] is True
    assert summary["status"] == "WARNING"


# -----------------------------------------------------------------------------
# 5. Recurs de Pagament Inesperat (Aturada Immediata)
# -----------------------------------------------------------------------------
def test_unexpected_paid_resource_detection(tmp_cost_guard):
    """Detectar una forma no autoritzada (ex: AMD E4) o NAT Gateway dispara aturada preventiva immediata."""
    rogue_provisioning = dict(AUTHORIZED_BASELINE)
    rogue_provisioning["shape"] = "VM.Standard.E4.Flex"  # Forma x86 comercial de pagament!

    summary = tmp_cost_guard.evaluate(
        current_provisioning=rogue_provisioning,
        uptime_hours=10.0
    )
    assert summary["status"] == "PREVENTIVE_SHUTDOWN"
    assert summary["service_state"] == "STOPPED_PREVENTIVE"
    assert "Forma no autoritzada de pagament" in summary["lock_reason"]
    can_run, _ = tmp_cost_guard.can_restart()
    assert can_run is False


# -----------------------------------------------------------------------------
# 6. Aturada Econòmica en Superar 0.25 €
# -----------------------------------------------------------------------------
def test_preventive_economic_shutdown_at_0_25(tmp_cost_guard):
    """Un cost acumulat de 0.26 € activa PREVENTIVE_SHUTDOWN."""
    summary = tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=50.0,
        official_data={
            "amount_eur": 0.26,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
    )
    assert summary["status"] == "PREVENTIVE_SHUTDOWN"
    assert summary["service_state"] == "STOPPED_PREVENTIVE"
    can_restart, reason = tmp_cost_guard.can_restart()
    assert can_restart is False
    assert "Aturada preventiva activada" in reason


# -----------------------------------------------------------------------------
# 7. Bloqueig de Reinici en Superar 0.50 €
# -----------------------------------------------------------------------------
def test_restart_lock_at_0_50(tmp_cost_guard):
    """Un cost acumulat de 0.52 € activa RESTART_LOCKED."""
    summary = tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=100.0,
        official_data={
            "amount_eur": 0.52,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
    )
    assert summary["status"] == "RESTART_LOCKED"
    assert summary["service_state"] == "STOPPED_ECONOMIC_LOCK"
    can_restart, reason = tmp_cost_guard.can_restart()
    assert can_restart is False
    assert "Bloqueig econòmic actiu" in reason


# -----------------------------------------------------------------------------
# 8. Reinici Manual Autoritzat
# -----------------------------------------------------------------------------
def test_manual_authorized_restart(tmp_cost_guard):
    """Comprova que només amb el token exacte d'autorització manual es desbloqueja el servei."""
    # Simulem bloqueig previ
    tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=100.0,
        official_data={"amount_eur": 0.60, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    )
    assert tmp_cost_guard.can_restart()[0] is False

    # Intent amb token incorrecte
    assert tmp_cost_guard.authorize_manual_restart("token_fals") is False
    assert tmp_cost_guard.can_restart()[0] is False

    # Intent amb token legítim
    assert tmp_cost_guard.authorize_manual_restart("AUTORITZO_REINICI_SENSE_DESPESA") is True
    can_restart, _ = tmp_cost_guard.can_restart()
    assert can_restart is True
    assert tmp_cost_guard.state["service_state"] == "ACTIVE"


# -----------------------------------------------------------------------------
# 9. Simulació Progressiva de Llindars (0.01, 0.10, 0.25, 0.50, 1.00)
# -----------------------------------------------------------------------------
def test_threshold_progression(tmp_cost_guard):
    """Verifica les transicions d'estat a cadascun dels llindars establerts."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # Nivell 0: 0.00 € -> OK
    s0 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 0.00, "timestamp_utc": now_iso})
    assert s0["status"] == "OK"

    # Nivell 1: 0.01 € -> NOTIFICATION
    s1 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 0.015, "timestamp_utc": now_iso})
    assert s1["status"] == "NOTIFICATION"

    # Nivell 2: 0.10 € -> WARNING
    s2 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 0.12, "timestamp_utc": now_iso})
    assert s2["status"] == "WARNING"

    # Nivell 3: 0.25 € -> PREVENTIVE_SHUTDOWN
    s3 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 0.26, "timestamp_utc": now_iso})
    assert s3["status"] == "PREVENTIVE_SHUTDOWN"

    # Nivell 4: 0.50 € -> RESTART_LOCKED
    s4 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 0.55, "timestamp_utc": now_iso})
    assert s4["status"] == "RESTART_LOCKED"

    # Nivell 5: 1.00 € (Sostre desitjat) -> Manté RESTART_LOCKED
    s5 = tmp_cost_guard.evaluate(AUTHORIZED_BASELINE, 10.0, {"amount_eur": 1.05, "timestamp_utc": now_iso})
    assert s5["status"] == "RESTART_LOCKED"


# -----------------------------------------------------------------------------
# 10. Watchdog Extern i Fallada de la VM
# -----------------------------------------------------------------------------
def test_external_watchdog_vm_failure_handling(tmp_cost_guard):
    """Simula el comportament del watchdog extern davant d'una VM aturada."""
    # Si la VM està aturada per motius no econòmics, can_restart ha de ser True
    tmp_cost_guard.state["economic_shutdown_triggered"] = False
    tmp_cost_guard.state["restart_locked"] = False
    can_restart, _ = tmp_cost_guard.can_restart()
    assert can_restart is True

    # Si està aturada per motiu econòmic, can_restart ha de ser False
    tmp_cost_guard.state["economic_shutdown_triggered"] = True
    can_restart, reason = tmp_cost_guard.can_restart()
    assert can_restart is False


# -----------------------------------------------------------------------------
# 11. Protecció de Dades Sensibles per a la Web Pública
# -----------------------------------------------------------------------------
def test_public_web_status_sanitization(tmp_cost_guard):
    """Comprova que get_public_web_status() no conté imports econòmics, OCIDs ni dades d'usuari."""
    tmp_cost_guard.evaluate(
        current_provisioning=AUTHORIZED_BASELINE,
        uptime_hours=50.0,
        official_data={"amount_eur": 0.05, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    )
    pub = tmp_cost_guard.get_public_web_status()

    # Camps permesos
    assert "service_status" in pub
    assert "is_active" in pub
    assert "last_verified_utc" in pub
    assert "mode" in pub

    # Camps prohibits estrictament (privadesa econòmica)
    serialized = json.dumps(pub)
    assert "eur" not in serialized.lower()
    assert "cost" not in serialized.lower()
    assert "ocid" not in serialized.lower()
    assert "tenancy" not in serialized.lower()
    assert "0.05" not in serialized


# -----------------------------------------------------------------------------
# 12. Persistència de Costos d'Emmagatzematge i IPs amb VM Aturada (STOPPED)
# -----------------------------------------------------------------------------
def test_storage_and_ip_costs_continue_when_vm_is_stopped(tmp_cost_guard):
    """
    Comprova que si la VM està aturada (is_running=False), la CPU/RAM no factura,
    però el disc excedentari (>200GB) i les IPs no associades CONTINUEN facturant.
    """
    stopped_provisioning_with_leak = dict(AUTHORIZED_BASELINE)
    stopped_provisioning_with_leak["is_running"] = False
    stopped_provisioning_with_leak["extra_volumes_gb"] = 100.0  # Total 150GB disc (dins Always Free)
    stopped_provisioning_with_leak["unattached_public_ips"] = 2  # 2 IPs orfes

    cost, anomalies = tmp_cost_guard.compute_estimated_cost(
        provisioning=stopped_provisioning_with_leak,
        uptime_hours=744.0
    )
    # Les 2 IPs orfes han de facturar fins i tot amb VM aturada
    assert cost > 0.0
    assert any("IP pública reservada sense associar" in a for a in anomalies)


# -----------------------------------------------------------------------------
# 13. Execució Multimode del Vigilant Extern (scripts/oci_external_watchdog.py)
# -----------------------------------------------------------------------------
def test_watchdog_multimode_execution(tmp_path):
    """Valida que les tres modalitats del vigilant extern (fast, hourly, daily) funcionen sense error."""
    from scripts.oci_external_watchdog import run_watchdog

    def make_isolated_guard():
        return CostGuard(
            state_path=tmp_path / "cg_state.json",
            persistence_backend=InMemoryLockPersistenceBackend(initial_state={"locked": False})
        )

    # Mode Fast
    res_fast = run_watchdog(mode="fast", dry_run=True, mock_provisioning=AUTHORIZED_BASELINE, guard=make_isolated_guard())
    assert res_fast is not None
    assert "cost_guard_summary" in res_fast

    # Mode Hourly
    res_hourly = run_watchdog(mode="hourly", dry_run=True, mock_provisioning=AUTHORIZED_BASELINE, guard=make_isolated_guard())
    assert res_hourly is not None
    assert "cost_guard_summary" in res_hourly

    # Mode Daily
    res_daily = run_watchdog(mode="daily", dry_run=True, mock_provisioning=AUTHORIZED_BASELINE, guard=make_isolated_guard())
    assert res_daily is not None
    assert "cost_guard_summary" in res_daily


# -----------------------------------------------------------------------------
# 14. Bateria de Proves GitHubIssueLedgerPersistenceBackend (Fase 1)
# -----------------------------------------------------------------------------

class MockHttpResponse:
    def __init__(self, status: int, data: Any, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self._data = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._data

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def make_event_comment(
    comment_id: int,
    event_id: str,
    event_type: str = "LOCKED",
    sequence: int = 1,
    prev_hash: str = "GENESIS",
    lock_reason: str = "Bloqueig test",
    created_at: str = "2026-11-03T00:00:00Z",
    updated_at: str = "2026-11-03T00:00:00Z",
    override_hash: Optional[str] = None,
    raw_body: Optional[str] = None,
    resolves_lock_id: Optional[str] = None,
    actor: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    unlock_reason: Optional[str] = None,
    **extra_fields: Any
) -> Dict[str, Any]:
    if raw_body is not None:
        return {
            "id": comment_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "body": raw_body
        }

    event_payload = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": event_type,
        "sequence": sequence,
        "timestamp_utc": created_at,
        "prev_hash": prev_hash
    }
    if event_type == "LOCKED":
        event_payload["lock_reason"] = lock_reason
    elif event_type == "UNLOCKED":
        if resolves_lock_id is not None:
            event_payload["resolves_lock_id"] = resolves_lock_id
        if actor is not None:
            event_payload["actor"] = actor
        if workflow_run_id is not None:
            event_payload["workflow_run_id"] = workflow_run_id
        if unlock_reason is not None:
            event_payload["unlock_reason"] = unlock_reason

    event_payload.update(extra_fields)
    event_hash = override_hash or GitHubIssueLedgerPersistenceBackend._compute_event_hash(event_payload)
    event_payload["hash"] = event_hash

    body = f"<!-- COST_GUARD_EVENT -->\n```json\n{json.dumps(event_payload, indent=2)}\n```"
    return {
        "id": comment_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "body": body
    }


def test_issue_ledger_pagination_over_100_comments():
    """Valida la lectura completa i paginació (>100 comentaris) des del GitHub Issue."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    all_comments = []
    prev_h = "GENESIS"
    for i in range(1, 111):
        c = make_event_comment(
            comment_id=i,
            event_id=f"lock-evt-{i:03d}",
            sequence=i,
            prev_hash=prev_h,
            lock_reason=f"Motiu bloqueig {i}"
        )
        body = c["body"]
        payload_str = body.split("```json")[1].split("```")[0].strip()
        data = json.loads(payload_str)
        prev_h = data["hash"]
        all_comments.append(c)

    page1 = all_comments[:100]
    page2 = all_comments[100:]

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "&page=2" in url:
            return MockHttpResponse(200, page2)
        if "&page=1" in url:
            link = '<https://api.github.com/repos/owner/repo/issues/1/comments?per_page=100&page=2>; rel="next"'
            return MockHttpResponse(200, page1, headers={"Link": link})
        raise ValueError(f"URL inesperada: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        state, err = backend.get_state()
        assert err is None
        assert state is not None
        assert state["locked"] is True
        assert state["event_id"] == "lock-evt-110"
        assert state["lock_reason"] == "Motiu bloqueig 110"


def test_issue_ledger_issue_absent():
    """Valida que un Issue absent (HTTP 404) retorna ABSENT i provoca fallada segura (fail-closed)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=999)

    def fake_urlopen(req, timeout=10):
        raise urllib.error.HTTPError(url="http://api", code=404, msg="Not Found", hdrs={}, fp=None)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        state, err = backend.get_state()
        assert state is None
        assert err == "ABSENT"

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "ABSENT"
            assert guard.state["status"] == "PERSISTENCE_ABSENT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_issue_closed():
    """Valida que un Issue tancat és tractat com a invàlid / malformat i denega el reinici."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_urlopen(req, timeout=10):
        return MockHttpResponse(200, {"state": "closed", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "tancat" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_http_404_and_5xx_errors():
    """Valida la gestió d'errors HTTP 404 i 5xx (500, 502, 503) garantint fail-closed."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. Error HTTP 500 consultant Issue
    def fake_500(req, timeout=10):
        raise urllib.error.HTTPError(url="http://api", code=500, msg="Internal Server Error", hdrs={}, fp=None)

    with patch("urllib.request.urlopen", side_effect=fake_500):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and err.startswith("READ_ERROR") and "500" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "ERROR"
            assert guard.state["status"] == "PERSISTENCE_ERROR"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # 2. Error HTTP 502 consultant comentaris
    def fake_502(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            raise urllib.error.HTTPError(url="http://api", code=502, msg="Bad Gateway", hdrs={}, fp=None)
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_502):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and err.startswith("READ_ERROR") and "502" in err

    # 3. HTTP 404 consultant comentaris -> tractat com a ABSENT
    def fake_comments_404(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            raise urllib.error.HTTPError(url="http://api", code=404, msg="Not Found", hdrs={}, fp=None)
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_comments_404):
        state, err = backend.get_state()
        assert state is None
        assert err == "ABSENT"


def test_issue_ledger_timeout():
    """Valida que un timeout de xarxa o d'API no permet mai autoritzar un reinici."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_timeout(req, timeout=10):
        raise TimeoutError("The read operation timed out")

    with patch("urllib.request.urlopen", side_effect=fake_timeout):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and err.startswith("READ_ERROR")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "ERROR"
            assert guard.state["status"] == "PERSISTENCE_ERROR"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_malformed_json():
    """Valida la detecció de JSON malformat tant en la resposta HTTP com al cos del comentari."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. Resposta de l'API amb JSON corrupte
    def fake_corrupt_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, b"{ malformed_json: [ unclosed bracket ...")
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_corrupt_api):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and err.startswith("MALFORMED")

    # 2. Comentari amb cos d'esdeveniment corrupte
    bad_comment = make_event_comment(1, "lock-1", raw_body="<!-- COST_GUARD_EVENT -->\n{ bad json syntax")

    def fake_corrupt_body(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [bad_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_corrupt_body):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and err.startswith("MALFORMED")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_modified_comment_detection():
    """Valida la detecció de comentaris modificats (created_at != updated_at o alteració de hash)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. Comentari modificat a GitHub UI (created_at != updated_at)
    edited_comment = make_event_comment(
        comment_id=1,
        event_id="lock-edited",
        created_at="2026-11-03T00:00:00Z",
        updated_at="2026-11-03T00:10:00Z"
    )

    def fake_edited(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [edited_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_edited):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "modificat" in err

    # 2. Contingut alterat manualment mantenint dates (hash no coincideix)
    tampered_comment = make_event_comment(
        comment_id=2,
        event_id="lock-tampered",
        override_hash="a" * 64
    )

    def fake_tampered(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [tampered_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_tampered):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Hash invàlid" in err


def test_issue_ledger_invalid_events():
    """Valida el rebuig d'esdeveniments invàlids o UNLOCKED en Fase 1."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. Esdeveniment amb UNLOCKED (no permès en Fase 1)
    unlocked_comment = make_event_comment(1, "unlock-1", event_type="UNLOCKED")

    def fake_unlocked(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [unlocked_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_unlocked):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Camp obligatori d'auditoria" in err

    # 2. Tipus desconegut
    unknown_comment = make_event_comment(2, "unknown-1", event_type="CUSTOM_EVENT")

    def fake_unknown(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [unknown_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_unknown):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Tipus d'esdeveniment desconegut" in err

    # 3. Manca camp obligatori
    missing_field_comment = make_event_comment(
        3, "missing-1",
        raw_body='<!-- COST_GUARD_EVENT -->\n```json\n{"event_type": "LOCKED", "event_id": "m1"}\n```'
    )

    def fake_missing(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [missing_field_comment])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_missing):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Camp obligatori" in err


def test_issue_ledger_duplicate_identifiers():
    """Valida la detecció d'identificadors duplicats (event_id) en el registre."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-dup-id", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-dup-id", sequence=2, prev_hash=h1)

    def fake_dup(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_dup):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Identificador duplicat" in err


def test_issue_ledger_broken_hash_chain():
    """Valida la detecció de cadenes de hash trencades entre esdeveniments consecutius."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-01", sequence=1, prev_hash="GENESIS")
    c2 = make_event_comment(2, "lock-02", sequence=2, prev_hash="b" * 64)

    def fake_broken(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_broken):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Cadena de hash trencada" in err


def test_issue_ledger_deleted_intermediate_comment():
    """Valida la detecció de l'eliminació d'un comentari intermedi de la seqüència."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-01", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(2, "lock-02", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    c3 = make_event_comment(3, "lock-03", sequence=3, prev_hash=h2)

    # c2 eliminat de GitHub: només queden c1 i c3
    def fake_deleted(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c3])
        return MockHttpResponse(200, {"state": "open", "number": 1})

    with patch("urllib.request.urlopen", side_effect=fake_deleted):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Cadena de hash trencada" in err


def test_issue_ledger_read_after_write_verification():
    """Valida tant l'èxit com els errors en la verificació immediata read-after-write."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. Error de verificació: el GET immediat retorna 500
    def fake_raw_500(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url and method == "GET":
            return MockHttpResponse(200, [])
        if "/issues/1/comments" in url and method == "POST":
            return MockHttpResponse(201, {"id": 501})
        if "/issues/comments/501" in url:
            raise urllib.error.HTTPError(url="http://api", code=500, msg="Server Error", hdrs={}, fp=None)
        raise ValueError(f"Inesperat: {url} {method}")

    with patch("urllib.request.urlopen", side_effect=fake_raw_500):
        ok, err = backend.set_state({"locked": True, "lock_reason": "Test raw 500"})
        assert ok is False
        assert err is not None and "read-after-write" in err

    # 2. Error de verificació: el comentari llegit està alterat o no té el hash esperat
    def fake_raw_altered(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url and method == "GET":
            return MockHttpResponse(200, [])
        if "/issues/1/comments" in url and method == "POST":
            return MockHttpResponse(201, {"id": 502})
        if "/issues/comments/502" in url:
            altered = make_event_comment(502, "lock-different-id", sequence=1, prev_hash="GENESIS")
            return MockHttpResponse(200, altered)
        raise ValueError(f"Inesperat: {url} {method}")

    with patch("urllib.request.urlopen", side_effect=fake_raw_altered):
        ok, err = backend.set_state({"locked": True, "lock_reason": "Test raw altered"})
        assert ok is False
        assert err is not None and "read-after-write" in err

    # 3. Èxit complet en read-after-write
    posted_payload = {}

    def fake_raw_success(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url and method == "GET":
            return MockHttpResponse(200, [])
        if "/issues/1/comments" in url and method == "POST":
            raw_body = json.loads(req.data.decode("utf-8"))["body"]
            posted_payload["body"] = raw_body
            return MockHttpResponse(201, {"id": 503})
        if "/issues/comments/503" in url:
            return MockHttpResponse(200, {
                "id": 503,
                "created_at": "2026-11-03T00:00:00Z",
                "updated_at": "2026-11-03T00:00:00Z",
                "body": posted_payload["body"]
            })
        raise ValueError(f"Inesperat: {url} {method}")

    with patch("urllib.request.urlopen", side_effect=fake_raw_success):
        ok, err = backend.set_state({"locked": True, "lock_reason": "Test raw success"})
        assert ok is True
        assert err is None


def test_issue_ledger_rejects_unlocked_in_phase_1():
    """Valida que no es permet publicar cap esdeveniment UNLOCKED sense metadades d'auditoria."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    ok, err = backend.set_state({"locked": False, "lock_reason": "Desbloqueig no permès"})
    assert ok is False
    assert err is not None and "Escriptura d'UNLOCKED rebutjada" in err


def test_cost_guard_resolves_issue_ledger_in_github_actions(monkeypatch):
    """Valida la resolució de backend a GitHub Actions cap a GitHubIssueLedgerPersistenceBackend."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "mock_gh_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
    monkeypatch.setenv("COST_GUARD_ISSUE_NUMBER", "42")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/42"):
            return MockHttpResponse(200, {"state": "open", "number": 42})
        if "/issues/42/comments" in url:
            c = make_event_comment(1, "lock-init", sequence=1, prev_hash="GENESIS")
            return MockHttpResponse(200, [c])
        raise ValueError(f"URL: {url}")

    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            guard = CostGuard(state_path=tmp_path)
            assert isinstance(guard.persistence_backend, GitHubIssueLedgerPersistenceBackend)
            assert guard.persistence_backend.issue_number == 42
            assert guard.persistence_backend.repository == "test-org/test-repo"
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def test_cost_guard_fails_closed_when_issue_number_missing_in_actions(monkeypatch):
    """Valida que si falta COST_GUARD_ISSUE_NUMBER a GitHub Actions, CostGuard falla en mode segur."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "mock_gh_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
    monkeypatch.delenv("COST_GUARD_ISSUE_NUMBER", raising=False)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path)
        assert isinstance(guard.persistence_backend, GitHubIssueLedgerPersistenceBackend)
        assert guard.persistence_status == "ERROR"
        assert guard.state["status"] == "PERSISTENCE_ERROR"
        assert guard.can_restart()[0] is False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# 15. Proves addicionals de robustesa (Fase 1.1)
# -----------------------------------------------------------------------------

def test_issue_ledger_pagination_exceeds_max_pages_aborts_fail_closed():
    """Valida que si l'historial supera max_pages (100 pàgines) sense esgotar-se, es rebutja l'historial incomplet."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_infinite_pages(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        comments = [{"id": i, "body": "discussió ordinària"} for i in range(1, 101)]
        link = '<https://api.github.com/repos/owner/repo/issues/1/comments?per_page=100&page=999>; rel="next"'
        return MockHttpResponse(200, comments, headers={"Link": link})

    with patch("urllib.request.urlopen", side_effect=fake_infinite_pages):
        state, err = backend.get_state()
        assert state is None
        assert err is not None
        assert err.startswith("READ_ERROR")
        assert "superat el límit màxim de paginació" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "ERROR"
            assert guard.state["status"] == "PERSISTENCE_ERROR"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_validates_comment_is_dict():
    """Valida que si un element de comentari no és un diccionari, falla en mode segur."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [12345])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "diccionari" in err


def test_issue_ledger_validates_comment_body_is_str():
    """Valida que si el camp body no és una cadena de text, falla en mode segur."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [{"id": 1, "body": None}])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "body" in err


def test_issue_ledger_validates_event_timestamps_present_and_valid():
    """Valida que created_at i updated_at siguin presents i tinguin format datetime vàlid."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # 1. created_at absent o buit
    c_no_created = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    c_no_created["created_at"] = ""

    def fake_no_created(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c_no_created])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_no_created):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "created_at" in err

    # 2. updated_at amb format no analitzable
    c_bad_ts = make_event_comment(2, "lock-2", sequence=1, prev_hash="GENESIS")
    c_bad_ts["created_at"] = "invalid-date-string"
    c_bad_ts["updated_at"] = "invalid-date-string"

    def fake_bad_ts(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c_bad_ts])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_bad_ts):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "invàlid" in err


def test_issue_ledger_sequence_positive_int():
    """Valida que 'sequence' hagi de ser un enter positiu (>0)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    bad_seq_comment = make_event_comment(1, "lock-1", sequence=0, prev_hash="GENESIS")

    def fake_seq_zero(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [bad_seq_comment])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_seq_zero):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "sequence" in err


def test_issue_ledger_valid_consecutive_sequence_1_2_3():
    """Valida un registre amb seqüència consecutiva correcta 1 -> 2 -> 3."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    c3 = make_event_comment(3, "lock-3", sequence=3, prev_hash=h2)

    def fake_valid_seq(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_valid_seq):
        state, err = backend.get_state()
        assert err is None
        assert state is not None
        assert state["locked"] is True
        assert state["event_id"] == "lock-3"

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            # Reinici impedit per bloqueig persistent legítim
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_first_event_sequence_not_one():
    """Valida el rebuig si el primer esdeveniment comença amb sequence = 2 (ha de ser 1)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c_seq2 = make_event_comment(1, "lock-start-2", sequence=2, prev_hash="GENESIS")

    def fake_seq2(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c_seq2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_seq2):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "sequence=1" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_sequence_jump_1_to_3():
    """Valida el rebuig davant d'un salt de seqüència 1 -> 3 (falta sequence=2)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    # c2 té sequence=3 (salt!)
    c2 = make_event_comment(2, "lock-3", sequence=3, prev_hash=h1)

    def fake_jump(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_jump):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "consecutiva" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_sequence_jump_1_to_2_to_5():
    """Valida el rebuig davant d'un salt no consecutiu 1 -> 2 -> 5 (falten 3 i 4)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    # c3 té sequence=5 (salt!)
    c3 = make_event_comment(3, "lock-5", sequence=5, prev_hash=h2)

    def fake_jump_5(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_jump_5):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "consecutiva" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_sequence_duplicate():
    """Valida el rebuig davant de seqüència duplicada 1 -> 2 -> 2."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    # c3 té sequence=2 (duplicat de c2!)
    c3 = make_event_comment(3, "lock-dup-seq", sequence=2, prev_hash=h2)

    def fake_dup_seq(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_dup_seq):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "consecutiva" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_sequence_decreasing():
    """Valida el rebuig davant d'una seqüència decreixent 1 -> 2 -> 1."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    # c3 té sequence=1 (decreixent!)
    c3 = make_event_comment(3, "lock-dec-seq", sequence=1, prev_hash=h2)

    def fake_dec_seq(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_dec_seq):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "consecutiva" in err

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_issue_ledger_set_state_generates_consecutive_sequences():
    """Valida que crides successives a set_state generen seqüències consecutives 1 -> 2."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    stored_comments = []
    comment_counter = 0

    def fake_api(req, timeout=10):
        nonlocal comment_counter
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"

        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})

        if "/issues/1/comments" in url and method == "GET":
            return MockHttpResponse(200, list(stored_comments))

        if "/issues/1/comments" in url and method == "POST":
            comment_counter += 1
            body_text = json.loads(req.data.decode("utf-8"))["body"]
            new_c = {
                "id": comment_counter,
                "created_at": "2026-11-03T00:00:00Z",
                "updated_at": "2026-11-03T00:00:00Z",
                "body": body_text
            }
            stored_comments.append(new_c)
            return MockHttpResponse(201, {"id": comment_counter})

        if "/issues/comments/" in url:
            cid = int(url.split("/")[-1])
            target = next(c for c in stored_comments if c["id"] == cid)
            return MockHttpResponse(200, target)

        raise ValueError(f"Inesperat: {url} {method}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        # 1. Primer esdeveniment
        ok1, err1 = backend.set_state({"locked": True, "lock_reason": "Lock 1"})
        assert ok1 is True and err1 is None
        ev1 = json.loads(stored_comments[0]["body"].split("```json")[1].split("```")[0])
        assert ev1["sequence"] == 1
        assert ev1["prev_hash"] == "GENESIS"

        # 2. Segon esdeveniment consecutiu
        ok2, err2 = backend.set_state({"locked": True, "lock_reason": "Lock 2"})
        assert ok2 is True and err2 is None
        ev2 = json.loads(stored_comments[1]["body"].split("```json")[1].split("```")[0])
        assert ev2["sequence"] == 2
        assert ev2["prev_hash"] == ev1["hash"]

        # 3. get_state valida amb èxit la seqüència consecutiva
        state, err = backend.get_state()
        assert err is None
        assert state["locked"] is True
        assert state["event_id"] == ev2["event_id"]


def test_issue_ledger_validates_schema_version_supported():
    """Valida que 'schema_version' sigui una versió admesa (ex: '1.0')."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    raw_payload = {
        "schema_version": "99.0",
        "event_id": "lock-bad-schema",
        "event_type": "LOCKED",
        "sequence": 1,
        "timestamp_utc": "2026-11-03T00:00:00Z",
        "lock_reason": "Test schema",
        "prev_hash": "GENESIS"
    }
    raw_payload["hash"] = GitHubIssueLedgerPersistenceBackend._compute_event_hash(raw_payload)
    bad_schema_comment = {
        "id": 1,
        "created_at": "2026-11-03T00:00:00Z",
        "updated_at": "2026-11-03T00:00:00Z",
        "body": f"<!-- COST_GUARD_EVENT -->\n```json\n{json.dumps(raw_payload)}\n```"
    }

    def fake_bad_schema(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [bad_schema_comment])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_bad_schema):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err and "Versió d'esquema" in err


def test_issue_ledger_unhandled_exception_resilience():
    """Valida que estructures anòmales o corruptes no provoquin excepcions no controlades."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    crazy_comment = {
        "id": 1,
        "created_at": "2026-11-03T00:00:00Z",
        "updated_at": "2026-11-03T00:00:00Z",
        "body": "<!-- COST_GUARD_EVENT -->\n```json\nnull\n```"
    }

    def fake_crazy(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [crazy_comment])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_crazy):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err


# -----------------------------------------------------------------------------
# 16. Proves de la Segona Àncora OCI (Fase 2)
# -----------------------------------------------------------------------------

def test_anchor_coherence_valid_synchronized():
    """Valida la coherència perfecta entre GitHub Ledger i Àncora OCI amb bloqueig actiu."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    oci_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1",
        "lock_reason": "Bloqueig OCI coherent"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "Bloqueig econòmic actiu" in reason
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_tail_truncation_detected():
    """Valida que si s'elimina el darrer comentari a GitHub (S_gh < S_oci), es detecta truncació."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    # A GitHub només queda c1 (sequence=1)
    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # L'àncora OCI té confirmada sequence=2
    oci_anchor = {
        "economic_lock": True,
        "ledger_sequence": 2,
        "ledger_hash": "b" * 64,
        "ledger_event_id": "lock-2",
        "lock_reason": "Bloqueig a seq 2"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert "Truncació de cua detectada" in guard.persistence_error
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "PERSISTENCE_CORRUPT" in guard.state["status"]
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_total_truncation_detected():
    """Valida que si s'eliminen tots els comentaris a GitHub (ABSENT), l'àncora OCI impedeix el reinici."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [])  # Sense comentaris
        raise ValueError(f"URL: {url}")

    oci_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": "c" * 64,
        "ledger_event_id": "lock-total-trunc",
        "lock_reason": "Bloqueig actiu"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert "Truncació total detectada" in guard.persistence_error
            can_restart, reason = guard.can_restart()
            assert can_restart is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_intermediate_deletion_detected():
    """Valida la detecció de l'eliminació d'un comentari intermedi quan hi ha àncora OCI."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]
    c3 = make_event_comment(3, "lock-3", sequence=3, prev_hash=h2)
    h3 = json.loads(c3["body"].split("```json")[1].split("```")[0])["hash"]

    # Comentari 2 eliminat: només queden c1 i c3
    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c3])
        raise ValueError(f"URL: {url}")

    oci_anchor = {
        "economic_lock": True,
        "ledger_sequence": 3,
        "ledger_hash": h3,
        "ledger_event_id": "lock-3"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_anchor_absent():
    """Valida que l'absència d'àncora OCI amb ledger existent falla en mode segur (fail-closed)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            # Àncora buida (tags no existents a OCI) amb un ledger existent
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor={})
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert "Absència d'àncora OCI" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_anchor_malformed_sequence():
    """Valida que una àncora OCI amb sequence invàlida (no numèrica o <=0) fa fallar en mode segur."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    bad_seq_anchor = {
        "economic_lock": True,
        "ledger_sequence": "invalid_sequence_string",
        "ledger_hash": "a" * 64,
        "ledger_event_id": "lock-bad"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=bad_seq_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "invàlid" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_anchor_malformed_hash():
    """Valida que una àncora OCI amb hash invàlid fa fallar en mode segur."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    bad_hash_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": "hash_short",
        "ledger_event_id": "lock-bad-hash"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=bad_hash_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "Format de 'ledger_hash'" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_hash_mismatch_same_sequence():
    """Valida la detecció de discrepància de hash a la mateixa seqüència."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    mismatch_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": "f" * 64,  # Hash diferent!
        "ledger_event_id": "lock-1"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=mismatch_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "Discrepància de hash a seqüència 1" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_fork_when_github_ahead():
    """Valida que si GitHub té seq=2 però el seu ancestre seq=1 no coincideix amb l'àncora OCI, es rebutja."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    # L'àncora OCI té seq=1 però un hash diferent del c1 de GitHub
    fork_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": "e" * 64,
        "ledger_event_id": "lock-ancestre-diferent"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=fork_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "Historial bifurcat" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_github_ahead_valid_ancestor():
    """Valida que si GitHub ha avançat (seq=2 > seq=1) i conté l'ancestre legítim, s'accepta."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    valid_prev_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=valid_prev_anchor)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_does_not_permit_unlock_in_phase_2():
    """Valida que cap combinació d'àncora o manipulació permet desbloquejar la VM en Fase 2."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)

    # Àncora que intenta fingir economic_lock=False
    fake_unlock_anchor = {
        "economic_lock": False,
        "ledger_sequence": 1,
        "ledger_hash": "a" * 64,
        "ledger_event_id": "fake-unlock"
    }

    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=fake_unlock_anchor)
            # Rebutjat per discrepància de hash o locked
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_missing_event_id():
    """Valida el rebuig si economic_lock=True però manca ledger_event_id."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # Manca ledger_event_id
    bad_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=bad_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "manca ledger_sequence, ledger_hash o ledger_event_id" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_bool_sequence():
    """Valida el rebuig taxatiu de seqüència de tipus booleà (bool no és enter vàlid)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # ledger_sequence és True (booleà)
    bad_anchor = {
        "economic_lock": True,
        "ledger_sequence": True,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=bad_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "booleà" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_partial_fields_rejected():
    """Valida el rebuig si l'àncora té camps parcials o incompatibles."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # economic_lock=False amb camps parcials (només hash)
    partial_anchor = {
        "economic_lock": False,
        "ledger_hash": "a" * 64
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=partial_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_economic_lock_false_with_locked_ledger():
    """Valida el rebuig immediat si l'àncora indica economic_lock=False però el ledger té un bloqueig LOCKED."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # Àncora indica falsament False quan el ledger està LOCKED
    incoherent_anchor = {
        "economic_lock": False
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=incoherent_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "Incoherència" in guard.persistence_error and "LOCKED" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_non_bool_economic_lock():
    """Valida el comportament de tipus per economic_lock: accepta bool o 'true'/'false', rebutja altres."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    # 1. Valor no booleà invàlid (ex: 'maybe' o número)
    invalid_anchor = {
        "economic_lock": "not_a_boolean",
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1"
    }

    # 2. Cadena 'true' (vàlida segons l'especificació d'etiquetes OCI)
    valid_string_anchor = {
        "economic_lock": "true",
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            # Rebuig de tipus no admès
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=invalid_anchor)
            assert guard.persistence_status == "MALFORMED"
            assert "Camp 'economic_lock' ha de ser un booleà o cadena" in guard.persistence_error
            assert guard.can_restart()[0] is False

            # Acceptació de cadena 'true'
            guard2 = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=valid_string_anchor)
            assert guard2.persistence_status == "OK"
            assert guard2.state["status"] == "RESTART_LOCKED"
            assert guard2.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


# ---------------------------------------------------------------------------
# FASE 2.3: Tests d'eliminació del bootstrap implícit i validació d'ancestre
# ---------------------------------------------------------------------------

def test_stop_oci_instance_rejects_when_anchor_completely_absent(monkeypatch):
    """Objectiu 3.1: Valida que si l'àncora OCI és completament absent (sense ledger_sequence),
    stop_oci_instance() avorta l'actualització d'etiquetes i NO crida update_instance()."""
    from scripts.oci_external_watchdog import stop_oci_instance

    class MockInstanceData:
        def __init__(self):
            # Cap tag d'àncora: només tags aliens preexistents
            self.freeform_tags = {"Project": "PreddicionsLliga", "Env": "Production"}

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-initial-no-anchor"}

    class MockResponse200:
        status = 200

    update_called = []
    action_called = []

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            update_called.append((ocid, update_details, if_match))
            return MockResponse200()

        def instance_action(self, ocid, action):
            action_called.append((ocid, action))
            return MockResponse200()

    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta = {"sequence": 1, "hash": "a" * 64, "event_id": "lock-1"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Intent de bootstrap implícit en operació ordinària",
        dry_run=False,
        anchor_metadata=meta
    )

    # Rebutjat perquè l'àncora no existia a OCI (bootstrap no permès en operació ordinària)
    assert success is False
    assert len(update_called) == 0, "update_instance NO ha de ser cridada quan l'àncora és absent!"
    # L'ordre d'aturada d'emergència STOP sí que s'ha d'enviar per seguretat pressupostària
    assert len(action_called) == 1
    assert action_called[0][1] == "STOP"


def test_stop_oci_instance_rejects_when_anchor_partial(monkeypatch):
    """Objectiu 3.2: Valida que si l'àncora OCI és parcial (falta ledger_hash o ledger_event_id),
    l'operació d'actualització és rebutjada i NO es crida update_instance()."""
    from scripts.oci_external_watchdog import stop_oci_instance

    class MockInstanceData:
        def __init__(self):
            # Té sequence però falten hash i event_id
            self.freeform_tags = {"ledger_sequence": "1"}

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-partial"}

    class MockResponse200:
        status = 200

    update_called = []
    action_called = []

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            update_called.append((ocid, update_details, if_match))
            return MockResponse200()

        def instance_action(self, ocid, action):
            action_called.append((ocid, action))
            return MockResponse200()

    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta = {"sequence": 2, "hash": "b" * 64, "event_id": "lock-2"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Intent d'actualització sobre àncora parcial",
        dry_run=False,
        anchor_metadata=meta
    )

    assert success is False
    assert len(update_called) == 0, "update_instance NO ha de ser cridada quan l'àncora és parcial!"
    assert len(action_called) == 1
    assert action_called[0][1] == "STOP"


def test_stop_oci_instance_succeeds_when_anchor_valid(monkeypatch):
    """Objectiu 3.3: Valida que amb una àncora OCI vàlida i un avanç monòton,
    stop_oci_instance() actualitza les etiquetes preservant tags aliens i executa STOP."""
    from scripts.oci_external_watchdog import stop_oci_instance

    applied_tags = {}

    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": "1",
                "ledger_hash": "a" * 64,
                "ledger_event_id": "lock-1",
                "Department": "Analytics"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-valid-1"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            assert if_match == "etag-valid-1"
            applied_tags.update(update_details.freeform_tags)
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta = {"sequence": 2, "hash": "b" * 64, "event_id": "lock-2"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Avanç legítim de seq 1 a 2",
        dry_run=False,
        anchor_metadata=meta
    )

    assert success is True
    assert applied_tags["ledger_sequence"] == "2"
    assert applied_tags["ledger_hash"] == "b" * 64
    assert applied_tags["ledger_event_id"] == "lock-2"
    assert applied_tags["Department"] == "Analytics", "Tag alien ha de ser preservat!"
    assert applied_tags["economic_lock"] == "true"


def test_anchor_coherence_ancestor_hash_mismatch():
    """Objectiu 3.5: Valida que si GitHub està per davant (seq 2 > seq 1) però l'ancestre
    a seqüència 1 té un hash diferent de l'àncora OCI, CostGuard declara MALFORMED (Historial bifurcat)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    oci_anchor_bad_hash = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": "f" * 64,  # Hash incorrecte respecte a c1
        "ledger_event_id": "lock-1"
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor_bad_hash)
            assert guard.persistence_status == "MALFORMED"
            assert "Historial bifurcat" in guard.persistence_error
            assert "hash" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_ancestor_event_id_mismatch():
    """Objectiu 3.6: Valida que si GitHub està per davant (seq 2 > seq 1) i el hash coincideix
    però l'event_id de l'ancestre no coincideix amb l'àncora OCI, CostGuard declara MALFORMED."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    oci_anchor_bad_event_id = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-impostor"  # event_id incorrecte respecte a c1 ('lock-1')
    }

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_anchor_bad_event_id)
            assert guard.persistence_status == "MALFORMED"
            assert "Historial bifurcat" in guard.persistence_error
            assert "event_id" in guard.persistence_error
            assert "'lock-1'" in guard.persistence_error
            assert "'lock-impostor'" in guard.persistence_error
            assert guard.can_restart()[0] is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_interrupted_write_ledger_ahead_watchdog_reconciliation(monkeypatch):
    """Objectiu 3.4 i 3.7: Simula una escriptura interrompuda entre el GitHub Issue Ledger i OCI.
    1. GitHub té seqüència 2 (escriptura completada a GitHub).
    2. OCI encara està a seqüència 1 (escriptura a OCI interrompuda per fallada de xarxa o procés).
    3. CostGuard comprova la coherència: detecta que OCI seq 1 és un ancestre legítim de seq 2 -> estat OK.
    4. El Watchdog extern llegeix les metadades de l'últim esdeveniment (seq 2) i reconcilia l'àncora OCI monòtonament.
    5. Després de la reconciliació, l'àncora OCI queda sincronitzada a seq 2."""
    from scripts.oci_external_watchdog import stop_oci_instance

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "lock-2", sequence=2, prev_hash=h1)
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    # Estat OCI desfasat (interrupció post-GitHub): seq=1
    oci_tags = {
        "economic_lock": "true",
        "ledger_sequence": "1",
        "ledger_hash": h1,
        "ledger_event_id": "lock-1",
        "Environment": "Production"
    }

    # Pas 1: Verificació de coherència per CostGuard
    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_tags)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            # Extreure metadades de l'últim esdeveniment a GitHub (seq 2)
            meta = guard.get_latest_ledger_metadata()
            assert meta["sequence"] == 2
            assert meta["hash"] == h2
            assert meta["event_id"] == "lock-2"
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # Pas 2: El Watchdog extern reconcilia l'àncora OCI a seq 2
    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = dict(oci_tags)

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-interrupted-1"}

    class MockResponse200:
        status = 200

    applied_tags = {}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            assert if_match == "etag-interrupted-1"
            applied_tags.update(update_details.freeform_tags)
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    reconcile_success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Reconciliació automàtica post-interrupció",
        dry_run=False,
        anchor_metadata=meta
    )

    assert reconcile_success is True
    # Àncora OCI ara està actualitzada monòtonament a seq 2
    assert applied_tags["ledger_sequence"] == "2"
    assert applied_tags["ledger_hash"] == h2
    assert applied_tags["ledger_event_id"] == "lock-2"
    assert applied_tags["Environment"] == "Production"  # tag alien preservat
    assert applied_tags["economic_lock"] == "true"


# -----------------------------------------------------------------------------
# FASE 3.1: Suport d'esdeveniments UNLOCKED i coherència d'àncora
# -----------------------------------------------------------------------------

def test_issue_ledger_valid_lock_then_unlock_transition():
    """Valida la transició nominal seq 1 LOCKED -> seq 2 UNLOCKED amb metadades d'auditoria."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED", lock_reason="Aturada 1")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin-user", workflow_run_id="run-12345", unlock_reason="Verificació completada"
    )

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        state, err = backend.get_state()
        assert err is None
        assert state is not None
        assert state["locked"] is False
        assert state["sequence"] == 2
        assert state["event_type"] == "UNLOCKED"
        assert state["resolves_lock_id"] == "lock-1"
        assert state["actor"] == "admin-user"
        assert state["workflow_run_id"] == "run-12345"
        assert state["unlock_reason"] == "Verificació completada"
        assert state["lock_reason"] is None


def test_issue_ledger_unlock_as_first_event_rejected():
    """Valida que UNLOCKED com a primer esdeveniment (sequence=1) és rebutjat com a MALFORMED."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(
        1, "unlock-1", sequence=1, prev_hash="GENESIS", event_type="UNLOCKED",
        resolves_lock_id="non-existent", actor="admin", workflow_run_id="run-1", unlock_reason="Intent il·legítim"
    )

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err
        assert "primer esdeveniment del ledger (sequence=1) no pot ser UNLOCKED" in err


def test_issue_ledger_double_unlock_rejected():
    """Valida que dos esdeveniments UNLOCKED consecutius (seq 1 LOCKED -> seq 2 UNLOCKED -> seq 3 UNLOCKED) són rebutjats."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-1", unlock_reason="Primer desbloqueig"
    )
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    c3 = make_event_comment(
        3, "unlock-3", sequence=3, prev_hash=h2, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-2", unlock_reason="Segon desbloqueig redundant"
    )

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err
        assert "l'estat previ no és LOCKED" in err


def test_issue_ledger_unlock_wrong_resolves_lock_id_rejected():
    """Valida que resolves_lock_id no coincident amb el darrer LOCKED és rebutjat en lectura i en escriptura."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-actual", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    # 1. En lectura
    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-equivocat", actor="admin", workflow_run_id="run-1", unlock_reason="Desbloqueig erroni"
    )

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        state, err = backend.get_state()
        assert state is None
        assert err is not None and "MALFORMED" in err
        assert "no coincideix amb l'event_id del bloqueig actiu" in err

    # 2. En escriptura
    def fake_api_write(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api_write):
        ok, err = backend.set_state({
            "locked": False,
            "resolves_lock_id": "lock-equivocat",
            "actor": "admin",
            "workflow_run_id": "run-1",
            "unlock_reason": "Desbloqueig erroni"
        })
        assert ok is False
        assert "no coincideix amb l'últim bloqueig del ledger" in err


def test_issue_ledger_unlock_missing_audit_fields_rejected():
    """Valida que un UNLOCKED sense camps d'auditoria complets és rebutjat en lectura i en escriptura."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")

    # 1. En escriptura
    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        # Manca actor
        ok, err = backend.set_state({
            "locked": False,
            "resolves_lock_id": "lock-1",
            "workflow_run_id": "run-1",
            "unlock_reason": "Sense actor"
        })
        assert ok is False
        assert "falten metadades d'auditoria administrativa obligatòries" in err

        # Manca workflow_run_id
        ok, err = backend.set_state({
            "locked": False,
            "resolves_lock_id": "lock-1",
            "actor": "admin",
            "unlock_reason": "Sense workflow"
        })
        assert ok is False
        assert "falten metadades d'auditoria administrativa obligatòries" in err

        # Manca unlock_reason
        ok, err = backend.set_state({
            "locked": False,
            "resolves_lock_id": "lock-1",
            "actor": "admin",
            "workflow_run_id": "run-1",
            "unlock_reason": "   "
        })
        assert ok is False
        assert "falten metadades d'auditoria administrativa obligatòries" in err


def test_issue_ledger_relock_after_unlock_valid():
    """Valida la seqüència seq 1 LOCKED -> seq 2 UNLOCKED -> seq 3 LOCKED."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-1", unlock_reason="Desbloquejat"
    )
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    c3 = make_event_comment(3, "lock-3", sequence=3, prev_hash=h2, event_type="LOCKED", lock_reason="Nou bloqueig")

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        state, err = backend.get_state()
        assert err is None
        assert state is not None
        assert state["locked"] is True
        assert state["sequence"] == 3
        assert state["event_type"] == "LOCKED"
        assert state["lock_reason"] == "Nou bloqueig"


def test_anchor_coherence_unlocked_synchronized_permits_restart():
    """Valida que ledger seq 2 UNLOCKED i àncora OCI seq 2 amb economic_lock=False permeten reiniciar (can_restart=True)."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-1", unlock_reason="Autoritzat"
    )
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    oci_unlocked_anchor = {
        "economic_lock": False,
        "ledger_sequence": 2,
        "ledger_hash": h2,
        "ledger_event_id": "unlock-2"
    }

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_unlocked_anchor)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is False
            can_restart, reason = guard.can_restart()
            assert can_restart is True
            assert reason is None
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_unlocked_ledger_ahead_of_locked_oci_keeps_lock():
    """Valida que si ledger està a seq 2 UNLOCKED però àncora OCI encara està a seq 1 LOCKED, el bloqueig es manté."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-1", unlock_reason="Autoritzat"
    )

    oci_locked_ancestor_anchor = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-1"
    }

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_locked_ancestor_anchor)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            assert "Desbloqueig pendent de sincronització" in guard.state["lock_reason"]
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "Desbloqueig pendent de sincronització" in reason
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_anchor_coherence_locked_ledger_ahead_of_unlocked_oci_valid_ancestor():
    """Valida que seq 1 LOCKED -> seq 2 UNLOCKED (sincronitzat a OCI) -> seq 3 LOCKED a GitHub manté la VM bloquejada."""
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_xyz", issue_number=1)
    c1 = make_event_comment(1, "lock-1", sequence=1, prev_hash="GENESIS", event_type="LOCKED")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2, "unlock-2", sequence=2, prev_hash=h1, event_type="UNLOCKED",
        resolves_lock_id="lock-1", actor="admin", workflow_run_id="run-1", unlock_reason="Autoritzat"
    )
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]

    c3 = make_event_comment(3, "lock-3", sequence=3, prev_hash=h2, event_type="LOCKED", lock_reason="Nou límit superat")

    # Àncora OCI a seq 2 (UNLOCKED, economic_lock=False)
    oci_unlocked_ancestor_anchor = {
        "economic_lock": False,
        "ledger_sequence": 2,
        "ledger_hash": h2,
        "ledger_event_id": "unlock-2"
    }

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, [c1, c2, c3])
        raise ValueError(f"URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend, oci_anchor=oci_unlocked_ancestor_anchor)
            assert guard.persistence_status == "OK"
            assert guard.state["restart_locked"] is True
            can_restart, reason = guard.can_restart()
            assert can_restart is False
        finally:
            if tmp_path.exists():
                tmp_path.unlink()






# -----------------------------------------------------------------------------
# FASE 3.2: Tests del mètode CostGuard.execute_administrative_recovery
# -----------------------------------------------------------------------------
def test_guard_execute_administrative_recovery_success():
    """
    Verifica que el helper guard.execute_administrative_recovery desbloqueja correctament
    un ledger en LOCKED actiu i actualitza l'estat local quan l'inventari és net.
    """
    c1 = make_event_comment(1, event_id="lock-cg-1", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    backend = GitHubIssueLedgerPersistenceBackend(repository="owner/repo", token="ghp_test", issue_number=1)

    comments_store = [c1]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url and method == "GET":
            return MockHttpResponse(200, list(comments_store))
        if "/issues/1/comments" in url and method == "POST":
            data = json.loads(req.data.decode("utf-8"))
            comment_obj = {"id": 102, "body": data["body"], "created_at": "2026-11-03T12:00:00Z", "updated_at": "2026-11-03T12:00:00Z"}
            comments_store.append(comment_obj)
            return MockHttpResponse(201, comment_obj)
        if "/issues/comments/102" in url:
            return MockHttpResponse(200, comments_store[-1])
        raise ValueError(f"URL no esperada: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.state["restart_locked"] is True
            assert guard.can_restart()[0] is False

            ok, msg = guard.execute_administrative_recovery(
                actor="super_admin",
                workflow_run_id="run-cg-100",
                unlock_reason="Alliberament manual auditat",
                current_provisioning=dict(AUTHORIZED_BASELINE)
            )

            assert ok is True
            assert "completada amb èxit" in msg
            assert guard.state["restart_locked"] is False
            assert guard.state["status"] == "OK"
            assert guard.can_restart()[0] is True

            # Comprovar l'esdeveniment creat al ledger
            latest_ev = guard.latest_ledger_event
            assert latest_ev["sequence"] == 2
            assert latest_ev["event_type"] == "UNLOCKED"
            assert latest_ev["resolves_lock_id"] == "lock-cg-1"
            assert latest_ev["actor"] == "super_admin"
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def test_guard_execute_administrative_recovery_denied_rogue_resources():
    """
    Verifica que guard.execute_administrative_recovery és rebutjada immediatament
    si hi ha recursos no autoritzats presents a l'inventari.
    """
    c1 = make_event_comment(1, event_id="lock-cg-rogue", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    backend = GitHubIssueLedgerPersistenceBackend(repository="owner/repo", token="ghp_test", issue_number=1)

    comments_store = [c1]

    def fake_api(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/1"):
            return MockHttpResponse(200, {"state": "open", "number": 1})
        if "/issues/1/comments" in url:
            return MockHttpResponse(200, list(comments_store))
        raise ValueError(f"URL no esperada: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_api):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            rogue_prov = dict(AUTHORIZED_BASELINE)
            rogue_prov["nat_gateways"] = 1

            ok, msg = guard.execute_administrative_recovery(
                actor="admin",
                workflow_run_id="run-1",
                unlock_reason="Intent invàlid",
                current_provisioning=rogue_prov
            )

            assert ok is False
            assert "l'inventari OCI conté recursos no autoritzats" in msg
            assert guard.state["restart_locked"] is True
            assert len(comments_store) == 1
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

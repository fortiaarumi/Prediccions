"""
tests/test_supabase_coordinator_integration.py
==============================================
Proves d'integració per a `SupabaseCoordinator` contra un servidor HTTP simulat
que emula el protocol REST i RPC de Supabase.

Valida:
1. Autenticació mitjançant apikey i Bearer token (rebuig amb 401).
2. Reclamació atòmica de comandes (`claim_next_pending_session`).
3. Emissió de batecs de cor periòdics i detecció de cancel·lació cooperativa (`send_session_heartbeat`).
4. Publicació de snapshots i oportunitats accionables (`publish_session_snapshot`).
5. Consulta de la vista d'oportunitats actives (`v_active_surebets`).
6. Mecanismes de reintent davant errors transitoris de xarxa (HTTP 503).
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pytest

from agent.supabase_coordinator import (
    SupabaseCoordinator,
    SupabaseCoordinatorError,
    SupabaseAuthenticationError
)
from agent.protocol import (
    SnapshotRecord,
    STATUS_RUNNING
)


class MockSupabaseServerHandler(BaseHTTPRequestHandler):
    """Manejador HTTP que emula els endpoints de Supabase RPC i REST."""

    # Estat en memòria compartit per a la simulació
    pending_queue = []
    active_sessions = {}
    snapshots = []
    opportunities = []
    flaky_counter = 0

    def log_message(self, format, *args):
        pass  # Silenciar logs a la sortida de pytest

    def do_POST(self):
        auth_header = self.headers.get("Authorization", "")
        apikey_header = self.headers.get("apikey", "")

        # 1. Comprovació d'autenticació
        if "invalid-key" in auth_header or apikey_header == "invalid-key":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid API Key"}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        payload = json.loads(body) if body else {}

        # 2. Emulació d'error transitori per provar el reintent
        if self.path == "/rest/v1/rpc/flaky_rpc":
            MockSupabaseServerHandler.flaky_counter += 1
            if MockSupabaseServerHandler.flaky_counter == 1:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Service Temporarily Unavailable"}')
                return
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success": true}')
                return

        # 3. RPC claim_next_pending_session
        if self.path == "/rest/v1/rpc/claim_next_pending_session":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            if MockSupabaseServerHandler.pending_queue:
                sess = MockSupabaseServerHandler.pending_queue.pop(0)
                sess_id = sess["id"]
                MockSupabaseServerHandler.active_sessions[sess_id] = {
                    "id": sess_id,
                    "status": "RUNNING",
                    "cancel_requested": False
                }
                resp = [{
                    "session_id": sess_id,
                    "duration_sec": sess.get("duration_seconds", 600),
                    "assigned_agent_id": "agent-win-001"
                }]
                self.wfile.write(json.dumps(resp).encode("utf-8"))
            else:
                self.wfile.write(b"[]")
            return

        # 4. RPC send_session_heartbeat
        if self.path == "/rest/v1/rpc/send_session_heartbeat":
            s_id = payload.get("p_session_id")
            sess = MockSupabaseServerHandler.active_sessions.get(s_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            if sess:
                resp = [{"alive": True, "cancel_requested": sess.get("cancel_requested", False)}]
            else:
                resp = [{"alive": False, "cancel_requested": False}]
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        # 5. RPC complete_session
        if self.path == "/rest/v1/rpc/complete_session":
            s_id = payload.get("p_session_id")
            if s_id in MockSupabaseServerHandler.active_sessions:
                MockSupabaseServerHandler.active_sessions[s_id]["status"] = "COMPLETED"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"true")
            return

        # 6. RPC fail_session
        if self.path == "/rest/v1/rpc/fail_session":
            s_id = payload.get("p_session_id")
            if s_id in MockSupabaseServerHandler.active_sessions:
                MockSupabaseServerHandler.active_sessions[s_id]["status"] = "FAILED"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"true")
            return

        # 7. RPC request_session_cancellation
        if self.path == "/rest/v1/rpc/request_session_cancellation":
            s_id = payload.get("p_session_id")
            if s_id in MockSupabaseServerHandler.active_sessions:
                MockSupabaseServerHandler.active_sessions[s_id]["cancel_requested"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"true")
            return

        # 8. RPC publish_session_snapshot
        if self.path == "/rest/v1/rpc/publish_session_snapshot":
            snap_id = f"snap-{len(MockSupabaseServerHandler.snapshots) + 1}"
            MockSupabaseServerHandler.snapshots.append(payload)
            opps = payload.get("p_opportunities") or []
            for op in opps:
                MockSupabaseServerHandler.opportunities.append(op)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(snap_id).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        auth_header = self.headers.get("Authorization", "")
        if "invalid-key" in auth_header:
            self.send_response(401)
            self.end_headers()
            return

        if self.path.startswith("/rest/v1/v_active_surebets"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(MockSupabaseServerHandler.opportunities).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def mock_supabase_server():
    """Inicia un servidor HTTP de prova en un fil separat."""
    server = HTTPServer(("127.0.0.1", 0), MockSupabaseServerHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reinicia l'estat del servidor simulat abans de cada test."""
    MockSupabaseServerHandler.pending_queue.clear()
    MockSupabaseServerHandler.active_sessions.clear()
    MockSupabaseServerHandler.snapshots.clear()
    MockSupabaseServerHandler.opportunities.clear()
    MockSupabaseServerHandler.flaky_counter = 0


def test_supabase_coordinator_authentication_failure(mock_supabase_server):
    """Comprova que credencials no vàlides generen SupabaseAuthenticationError (HTTP 401)."""
    coord = SupabaseCoordinator(
        supabase_url=mock_supabase_server,
        supabase_key="invalid-key",
        timeout_seconds=2.0
    )
    with pytest.raises(SupabaseAuthenticationError) as exc_info:
        coord.claim_pending_order()
    assert "401" in str(exc_info.value)


def test_supabase_coordinator_claim_pending_order(mock_supabase_server):
    """Verifica la reclamació atòmica d'ordres pendents a Supabase."""
    coord = SupabaseCoordinator(
        supabase_url=mock_supabase_server,
        supabase_key="valid-agent-key",
        timeout_seconds=2.0
    )

    # 1. Sense comandes a la cua
    order = coord.claim_pending_order()
    assert order is None

    # 2. Afegir una comanda a la cua simulada
    MockSupabaseServerHandler.pending_queue.append({
        "id": "sess-test-99",
        "duration_seconds": 900
    })

    # 3. Reclamar comanda
    order = coord.claim_pending_order()
    assert order is not None
    assert order.session_id == "sess-test-99"
    assert order.requested_duration_minutes == 15
    assert order.status == STATUS_RUNNING
    assert order.assigned_agent_id == "agent-win-001"

    # 4. Cua buida després de reclamar
    assert coord.claim_pending_order() is None


def test_supabase_coordinator_heartbeat_and_cooperative_cancellation(mock_supabase_server):
    """Verifica el batec de cor i la detecció de cancel·lació cooperativa des de la web."""
    coord = SupabaseCoordinator(
        supabase_url=mock_supabase_server,
        supabase_key="valid-agent-key",
        timeout_seconds=2.0
    )

    # Preparar sessió activa
    MockSupabaseServerHandler.active_sessions["sess-hb-01"] = {
        "id": "sess-hb-01",
        "status": "RUNNING",
        "cancel_requested": False
    }

    # Batec ordinari
    alive, cancel_req = coord.send_heartbeat("sess-hb-01")
    assert alive is True
    assert cancel_req is False

    # L'usuari sol·licita cancel·lació des del panell web
    coord.request_cancellation("sess-hb-01")

    # El següent batec detecta cancel_requested cooperatiu
    alive, cancel_req = coord.send_heartbeat("sess-hb-01")
    assert alive is True
    assert cancel_req is True


def test_supabase_coordinator_publish_snapshot_and_query_active(mock_supabase_server):
    """Verifica la publicació de snapshots de quotes i la consulta de surebets actives."""
    coord = SupabaseCoordinator(
        supabase_url=mock_supabase_server,
        supabase_key="valid-agent-key",
        timeout_seconds=2.0
    )

    snapshot = SnapshotRecord(
        snapshot_id="snap_123",
        opportunity_id="opp_456",
        session_id="sess-snap-10",
        cycle_index=1,
        matchup="Valencia vs Betis",
        market_name="1X2",
        profit_pct=3.45,
        freshness_tier="FRESH",
        legs=[{"bookmaker": "Winamax", "odd": 2.15}],
        age_max_seconds=120.0
    )

    opportunities = [
        {
            "opportunity_type": "SUREBET",
            "matchup": "Valencia vs Betis",
            "market_key": "1X2",
            "net_roi_pct": 3.45,
            "gross_margin_pct": 4.10,
            "commission_deducted_pct": 0.65,
            "bookmakers": [{"bookie": "Winamax", "odd": 2.15}]
        }
    ]

    snap_id = coord.publish_snapshot(snapshot, opportunities)
    assert snap_id == "snap-1"

    # Consultar oportunitats actives
    active_opps = coord.get_active_opportunities()
    assert len(active_opps) == 1
    assert active_opps[0]["matchup"] == "Valencia vs Betis"
    assert active_opps[0]["net_roi_pct"] == 3.45


def test_supabase_coordinator_retry_backoff_on_transient_error(mock_supabase_server):
    """Verifica que el coordinador reintenta davant un error 503 i té èxit."""
    coord = SupabaseCoordinator(
        supabase_url=mock_supabase_server,
        supabase_key="valid-agent-key",
        timeout_seconds=2.0,
        max_retries=3,
        backoff_factor=0.05
    )

    # La primera crida retornarà 503, la segona 200
    res = coord.call_rpc("flaky_rpc", {})
    assert res == {"success": True}
    assert MockSupabaseServerHandler.flaky_counter == 2

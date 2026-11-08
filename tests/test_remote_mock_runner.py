"""
tests/test_remote_mock_runner.py
================================
Verifica l'execució del flux de prova remota simulada de `agent/remote_mock_runner.py`.
Comprova que s'executin en seqüència:
1. Autenticació d'agent i operador
2. Creació de sessió
3. Bloqueig de concurrència (23505)
4. Reclamació de sessió (PENDING -> RUNNING)
5. Bloqueig d'operacions no autoritzades (42501)
6. Batec de sessió
7. Publicació de snapshot i comprovació de la constraint 23514
8. Idempotència de reintent
9. Finalització de sessió (RUNNING -> COMPLETED)
10. Gestió estricta de fallada: si falla la publicació, cridar fail_session(),
    marcar la sessió com a FAILED, no marcar com a COMPLETED i retornar success=False.
"""

import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pytest

import cli
from agent.remote_mock_runner import run_remote_mock_flow
from agent.supabase_coordinator import SupabaseCoordinator


class MockSupabaseFlowHandler(BaseHTTPRequestHandler):
    sessions = {}
    snapshots = {}
    agent_heartbeats = []
    force_snapshot_error = False

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        apikey = self.headers.get("apikey", "")
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}

        # 1. Login
        if self.path.startswith("/auth/v1/token"):
            email = body.get("email", "")
            role = "operator" if "operator" in email else "agent"
            token = f"jwt_mock_token_for_{role}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "access_token": token,
                "user": {"id": f"uuid_for_{role}", "email": email}
            }).encode("utf-8"))
            return

        # 2. register_agent_heartbeat
        if self.path == "/rest/v1/rpc/register_agent_heartbeat":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(True).encode("utf-8"))
            return

        # 3. create_search_session
        if self.path == "/rest/v1/rpc/create_search_session":
            # Si ja n'hi ha una PENDING o RUNNING, rebutjar per concurrència 23505
            has_active = any(s["status"] in ("PENDING", "RUNNING") for s in self.sessions.values())
            if has_active:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "code": "23505",
                    "message": "Ja existeix una sessió de cerca activa o en cua"
                }).encode("utf-8"))
                return

            sess_id = "test-session-uuid-001"
            self.sessions[sess_id] = {
                "id": sess_id,
                "status": "PENDING",
                "duration_seconds": body.get("p_duration_seconds", 120),
                "claimed_by_agent_id": None
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(sess_id).encode("utf-8"))
            return

        # 4. claim_next_pending_session
        if self.path == "/rest/v1/rpc/claim_next_pending_session":
            pending = [s for s in self.sessions.values() if s["status"] == "PENDING"]
            if pending:
                target = pending[0]
                target["status"] = "RUNNING"
                target["claimed_by_agent_id"] = "agent-daemon-uuid"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps([{
                    "session_id": target["id"],
                    "duration_sec": target["duration_seconds"],
                    "assigned_agent_id": "agent-daemon-uuid"
                }]).encode("utf-8"))
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps([]).encode("utf-8"))
            return

        # 5. send_session_heartbeat
        if self.path == "/rest/v1/rpc/send_session_heartbeat":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"alive": True, "cancel_requested": False}]).encode("utf-8"))
            return

        # 6. publish_session_snapshot
        if self.path == "/rest/v1/rpc/publish_session_snapshot":
            if MockSupabaseFlowHandler.force_snapshot_error:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Simulated internal DB failure"}).encode("utf-8"))
                return

            # Si crida l'operador, rebutjar amb 42501
            if "operator" in auth:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "code": "42501",
                    "message": "Accés denegat: Crida no autoritzada"
                }).encode("utf-8"))
                return

            # Validació estricta de la constraint 23514 de PostgreSQL:
            # CHECK (opportunity_type IN ('SUREBET', 'NEAR_ARB'))
            opps = body.get("p_opportunities", [])
            for opp in opps:
                if opp.get("opportunity_type") not in ("SUREBET", "NEAR_ARB"):
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "code": "23514",
                        "message": "check_violation: surebet_opportunities_opportunity_type_check"
                    }).encode("utf-8"))
                    return

            snap_id = body.get("p_snapshot_id", "snap-uuid-1")
            self.snapshots[snap_id] = body
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(snap_id).encode("utf-8"))
            return

        # 7. fail_session
        if self.path == "/rest/v1/rpc/fail_session":
            sess_id = body.get("p_session_id")
            if sess_id in self.sessions:
                self.sessions[sess_id]["status"] = "FAILED"
                self.sessions[sess_id]["error_message"] = body.get("p_error_message")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(True).encode("utf-8"))
            return

        # 8. complete_session
        if self.path == "/rest/v1/rpc/complete_session":
            sess_id = body.get("p_session_id")
            if sess_id in self.sessions:
                self.sessions[sess_id]["status"] = "COMPLETED"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(True).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/rest/v1/surebet_sessions"):
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            eq_id = qs.get("id", [""])[0].replace("eq.", "")
            if eq_id in self.sessions:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps([self.sessions[eq_id]]).encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([]).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def mock_supabase_server():
    server = HTTPServer(("127.0.0.1", 0), MockSupabaseFlowHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_full_remote_mock_flow_success(mock_supabase_server):
    """Verifica que el flux complet acaba amb èxit (success=True, status=COMPLETED) i no viola 23514."""
    MockSupabaseFlowHandler.sessions.clear()
    MockSupabaseFlowHandler.snapshots.clear()
    MockSupabaseFlowHandler.force_snapshot_error = False

    res = run_remote_mock_flow(
        supabase_url=mock_supabase_server,
        publishable_key="sb_publishable_test_key_12345",
        agent_email="agent@prediccions.test",
        agent_password="AgentPassword123!",
        operator_email="operator@prediccions.test",
        operator_password="OperatorPassword123!",
        agent_name="Windows-PC-Local"
    )

    assert res["success"] is True
    assert res["agent_auth"] is True
    assert res["agent_heartbeat"] is True
    assert res["operator_auth"] is True
    assert res["session_created"] is True
    assert res["concurrency_blocked"] is True
    assert res["session_claimed"] is True
    assert res["unauthorized_blocked"] is True
    assert res["session_heartbeat"] is True
    assert res["snapshot_published"] is True
    assert res["snapshot_idempotent"] is True
    assert res["session_completed"] is True
    assert res["final_status"] == "COMPLETED"
    assert len(res["errors"]) == 0

    # Comprovar que el snapshot inserit respecta la constraint SQL ('SUREBET' o 'NEAR_ARB')
    assert len(MockSupabaseFlowHandler.snapshots) == 1
    snap_body = list(MockSupabaseFlowHandler.snapshots.values())[0]
    opps = snap_body.get("p_opportunities", [])
    assert len(opps) == 1
    assert opps[0]["opportunity_type"] == "SUREBET"
    assert "[TEST SINTÈTIC]" in opps[0]["matchup"]


def test_coordinator_rejects_invalid_opportunity_type(mock_supabase_server):
    """Verifica que SupabaseCoordinator rebutja de forma estricta qualsevol type invàlid
    amb ValueError per evitar fallades 23514 a PostgreSQL, sense conversió silenciosa."""
    coord = SupabaseCoordinator(supabase_url=mock_supabase_server, publishable_key="sb_publishable_test_key_12345")
    coord.set_agent_jwt("mock_agent_jwt")

    with pytest.raises(ValueError, match="Tipus d'oportunitat desconegut"):
        coord.publish_snapshot(
            snapshot={"session_id": "test-session-uuid-001", "odds_age_seconds": 1, "market_type": "1X2"},
            opportunities=[{"opportunity_type": "TEST_SUREBET", "matchup": "Test", "market_key": "1X2"}],
            snapshot_uuid="test-uuid-norm-01"
        )


def test_publication_failure_marks_session_failed(mock_supabase_server):
    """Verifica que si la publicació de snapshot falla:
    1. No es fa la prova d'idempotència.
    2. No es marca la sessió com a COMPLETED.
    3. Es crida a fail_session() i la sessió queda com a FAILED.
    4. El resultat global 'success' és False.
    """
    MockSupabaseFlowHandler.sessions.clear()
    MockSupabaseFlowHandler.snapshots.clear()
    MockSupabaseFlowHandler.force_snapshot_error = True

    try:
        res = run_remote_mock_flow(
            supabase_url=mock_supabase_server,
            publishable_key="sb_publishable_test_key_12345",
            agent_email="agent@prediccions.test",
            agent_password="AgentPassword123!",
            operator_email="operator@prediccions.test",
            operator_password="OperatorPassword123!",
            agent_name="Windows-PC-Local"
        )

        assert res["success"] is False
        assert res["snapshot_published"] is False
        assert res["snapshot_idempotent"] is False
        assert res["session_completed"] is False
        assert res["final_status"] == "FAILED"
        assert len(res["errors"]) > 0
        assert "Simulated internal DB failure" in str(res["errors"])
    finally:
        MockSupabaseFlowHandler.force_snapshot_error = False


def test_cli_remote_mock_exits_nonzero_on_failure(monkeypatch):
    """Verifica que cli.py invoca sys.exit(1) si run_remote_mock_flow reporta success=False."""
    exited = []
    monkeypatch.setattr(sys, "exit", lambda code: exited.append(code))
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "test_key")
    monkeypatch.setenv("SUREBET_AGENT_EMAIL", "agent@test.com")
    monkeypatch.setenv("SUREBET_AGENT_PASSWORD", "pwd")
    monkeypatch.setattr("agent.remote_mock_runner.run_remote_mock_flow", lambda **kw: {"success": False, "errors": ["err"]})

    cli.cmd_surebet_agent(dry_run=False, remote_mock_test=True)

    assert exited == [1]


def test_cli_remote_mock_exits_zero_on_success(monkeypatch):
    """Verifica que cli.py NO invoca sys.exit(1) si run_remote_mock_flow té èxit."""
    exited = []
    monkeypatch.setattr(sys, "exit", lambda code: exited.append(code))
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "test_key")
    monkeypatch.setenv("SUREBET_AGENT_EMAIL", "agent@test.com")
    monkeypatch.setenv("SUREBET_AGENT_PASSWORD", "pwd")
    monkeypatch.setattr("agent.remote_mock_runner.run_remote_mock_flow", lambda **kw: {"success": True, "errors": []})

    cli.cmd_surebet_agent(dry_run=False, remote_mock_test=True)

    assert 1 not in exited




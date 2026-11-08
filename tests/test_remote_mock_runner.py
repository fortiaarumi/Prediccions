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
7. Publicació de snapshot i idempotència
8. Finalització de sessió
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pytest

from agent.remote_mock_runner import run_remote_mock_flow


class MockSupabaseFlowHandler(BaseHTTPRequestHandler):
    sessions = {}
    snapshots = {}
    agent_heartbeats = []

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

            snap_id = body.get("p_snapshot_id", "snap-uuid-1")
            self.snapshots[snap_id] = body
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(snap_id).encode("utf-8"))
            return

        # 7. complete_session
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
            # Obtenir id del path: ?id=eq.<id>&select=*
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


def test_full_remote_mock_flow(mock_supabase_server):
    MockSupabaseFlowHandler.sessions.clear()
    MockSupabaseFlowHandler.snapshots.clear()

    res = run_remote_mock_flow(
        supabase_url=mock_supabase_server,
        publishable_key="sb_publishable_test_key_12345",
        agent_email="agent@prediccions.test",
        agent_password="AgentPassword123!",
        operator_email="operator@prediccions.test",
        operator_password="OperatorPassword123!",
        agent_name="Windows-PC-Local"
    )

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

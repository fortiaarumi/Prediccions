"""
agent/local_bridge.py
=====================
Servidor pont HTTP local en loopback (127.0.0.1:8765) per comunicar
la interfície web amb el MockCoordinator i l'Agent Windows durant
proves de desenvolupament local, sense ports externs ni serveis al núvol.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from agent.coordinator import MockCoordinator
from agent.protocol import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED
)


class LocalCoordinatorHTTPHandler(BaseHTTPRequestHandler):
    coordinator: Optional[MockCoordinator] = None
    agent_ref = None

    def _set_headers(self, status: int = 200, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(204)

    def do_GET(self):
        coord = self.coordinator
        if not coord:
            self._set_headers(500)
            self.wfile.write(b'{"error": "Coordinator not initialized"}')
            return

        if self.path == "/api/agent/status":
            agent_online = False
            last_hb = None
            active_sess = None
            st = "OFFLINE"

            # Comprovar si l'agent ha enviat batec en els darrers 60s
            for a_id, pres in coord.agents.items():
                if pres.status in ("ONLINE", "BUSY"):
                    agent_online = True
                    last_hb = pres.last_heartbeat
                    active_sess = pres.active_session_id
                    st = pres.status
                    break

            payload = {
                "connected": agent_online,
                "agent_id": "Windows-PC-Local" if agent_online else None,
                "status": st,
                "last_heartbeat": last_hb,
                "active_session_id": active_sess,
                "version": "2.1.0"
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        elif self.path.startswith("/api/sessions/"):
            session_id = self.path.split("/")[-1].split("?")[0]
            order = coord.orders.get(session_id)
            if not order:
                self._set_headers(404)
                self.wfile.write(b'{"error": "Session not found"}')
                return

            snaps = coord.snapshots.get(session_id, [])
            payload = {
                "session_id": order.session_id,
                "status": order.status,
                "requested_duration_minutes": order.requested_duration_minutes,
                "assigned_agent_id": order.assigned_agent_id,
                "created_at": order.created_at,
                "snapshots_count": len(snaps),
                "snapshots": [s.to_dict() for s in snaps[-50:]]
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        elif self.path == "/api/opportunities":
            opps = coord.get_active_opportunities(max_age_seconds=1800.0)
            payload = {
                "count": len(opps),
                "max_age_seconds": 1800.0,
                "opportunities": [s.to_dict() for s in opps]
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "Not found"}')

    def do_POST(self):
        coord = self.coordinator
        if not coord:
            self._set_headers(500)
            self.wfile.write(b'{"error": "Coordinator not initialized"}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if self.path == "/api/sessions":
            duration = int(data.get("duration_minutes", 10))
            user_id = data.get("user_id", "user_local_web")
            order = coord.create_order(user_id=user_id, duration_minutes=duration)
            payload = {
                "session_id": order.session_id,
                "status": order.status,
                "duration_minutes": order.requested_duration_minutes,
                "created_at": order.created_at
            }
            self._set_headers(201)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        elif self.path.startswith("/api/sessions/") and self.path.endswith("/cancel"):
            parts = self.path.split("/")
            session_id = parts[3]
            user_id = data.get("user_id", "user_local_web")
            try:
                order = coord.request_cancellation(session_id, requesting_user_id=user_id)
                self._set_headers(200)
                self.wfile.write(json.dumps({"session_id": session_id, "status": order.status}).encode("utf-8"))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "Endpoint not found"}')

    def log_message(self, format, *args):
        # Silenciador de logs d'accés per no embrutar la consola
        pass


class LocalCoordinatorServer:
    """Aixeca el servidor HTTP en un fil separat sobre 127.0.0.1:8765."""

    def __init__(self, coordinator: MockCoordinator, port: int = 8765):
        self.coordinator = coordinator
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        handler_class = LocalCoordinatorHTTPHandler
        handler_class.coordinator = self.coordinator
        self.server = HTTPServer(("127.0.0.1", self.port), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[*] Pont HTTP local actiu a http://127.0.0.1:{self.port} per a la connexió web.")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        print("[*] Pont HTTP local aturat.")

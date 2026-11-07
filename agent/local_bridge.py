"""
agent/local_bridge.py
=====================
Servidor pont HTTP local en loopback (127.0.0.1:8765) per comunicar
la interfície web amb el MockCoordinator i l'Agent Windows durant
proves de desenvolupament local, sense ports externs ni serveis al núvol.

AVÍS DE SEGURETAT I DISSENY:
1. Ús exclusiu de desenvolupament local: Aquest pont està restringit a loopback (127.0.0.1).
   En producció, la interfície web es comunica directament amb Supabase REST/PostgreSQL RLS.
2. Sense secrets exposats: Cap clau d'administració ni credencial privilegiada viatja pel pont.
3. Protecció DNS Rebinding i CORS: Es valida Host i Origin per bloquejar peticions d'origen remot.
4. Validació estricta de càrrega útil: Límits estrictes de mida de cos (max 4 KB), durada (1 a 30 minuts)
   i caràcters alfanumèrics a identificadors.
5. Prevenció de duplicats: Es rebutja crear una nova sessió si ja n'hi ha una en curs (409 Conflict).
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import re
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

ALLOWED_ORIGINS = {
    "http://localhost",
    "http://127.0.0.1",
    "null"  # file:// origins
}


class LocalCoordinatorHTTPHandler(BaseHTTPRequestHandler):
    coordinator: Optional[MockCoordinator] = None
    agent_ref = None

    def _is_origin_allowed(self, is_mutating: bool = False) -> bool:
        """Valida que l'Origin i el Host pertanyin estrictament a loopback local amb protecció CSRF."""
        host = self.headers.get("Host", "")
        # Validació DNS rebinding: el Host ha de ser localhost o 127.0.0.1
        if not (host.startswith("localhost") or host.startswith("127.0.0.1")):
            return False

        # Protecció contra atacs CSRF i navegació creuada (Sec-Fetch-Site i Sec-Fetch-Mode)
        sec_fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
        if sec_fetch_site in ("cross-site",):
            return False
        sec_fetch_mode = self.headers.get("Sec-Fetch-Mode", "").lower()
        if sec_fetch_mode in ("navigate", "nested-navigate"):
            return False

        origin = self.headers.get("Origin")
        if not origin:
            # Per a accions sensibles (POST), rebutgem peticions amb origen absent tret que
            # incorporin la capçalera explícita anti-CSRF X-Requested-With (que un formulari HTML no pot enviar)
            if is_mutating:
                csrf_header = self.headers.get("X-Requested-With", "")
                if csrf_header != "XMLHttpRequest":
                    return False
            return True

        origin_clean = origin.split(":", 2)[0] + ":" + origin.split(":", 2)[1] if origin.count(":") >= 2 else origin
        return origin in ALLOWED_ORIGINS or origin_clean in ALLOWED_ORIGINS

    def _set_headers(self, status: int = 200, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        origin = self.headers.get("Origin", "*")
        if self._is_origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin if origin != "null" else "*")
        else:
            self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def do_OPTIONS(self):
        if not self._is_origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self._set_headers(204)

    def do_GET(self):
        if not self._is_origin_allowed(is_mutating=False):
            self._set_headers(403)
            self.wfile.write(b'{"error": "Forbidden: Peticio d\'origen no autoritzat"}')
            return

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
            raw_id = self.path.split("/")[-1].split("?")[0]
            if not re.match(r'^[a-zA-Z0-9_\-]+$', raw_id):
                self._set_headers(400)
                self.wfile.write(b'{"error": "Identificador de sessio invalid"}')
                return

            order = coord.orders.get(raw_id)
            if not order:
                self._set_headers(404)
                self.wfile.write(b'{"error": "Session not found"}')
                return

            snaps = coord.snapshots.get(raw_id, [])
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
        # 1. Protecció CSRF i validació d'origen per a accions sensibles
        if not self._is_origin_allowed(is_mutating=True):
            self._set_headers(403)
            self.wfile.write(b'{"error": "Forbidden: Peticio d\'origen no autoritzat o sense capcalera de seguretat"}')
            return

        # 2. Bloqueig d'enviaments de formularis HTML simples (evita CSRF de formulari)
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self._set_headers(415)
            self.wfile.write(b'{"error": "Unsupported Media Type: Es requereix Content-Type application/json"}')
            return

        coord = self.coordinator
        if not coord:
            self._set_headers(500)
            self.wfile.write(b'{"error": "Coordinator not initialized"}')
            return

        # Protecció de memòria: màxim 4096 bytes per petició
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 4096:
            self._set_headers(413)
            self.wfile.write(b'{"error": "Payload massa gran (limit 4KB)"}')
            return

        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if self.path == "/api/sessions":
            # 1. Protecció davant execucions duplicades concurrents
            with coord._lock:
                for s_id, ord_item in coord.orders.items():
                    if ord_item.status in (STATUS_PENDING, STATUS_RUNNING, STATUS_CANCEL_REQUESTED):
                        self._set_headers(409)
                        self.wfile.write(json.dumps({
                            "error": "Ja hi ha una sessio de cerca en curs",
                            "active_session_id": s_id,
                            "status": ord_item.status
                        }).encode("utf-8"))
                        return

            # 2. Validació estricta de durada (1 a 30 minuts)
            raw_duration = data.get("duration_minutes", 10)
            try:
                duration = int(raw_duration)
            except (ValueError, TypeError):
                duration = 10

            if duration < 1 or duration > 30:
                self._set_headers(400)
                self.wfile.write(b'{"error": "La durada ha d\'estar compresa estrictament entre 1 i 30 minuts"}')
                return

            user_id = str(data.get("user_id", "user_local_web"))[:64]
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
            raw_id = parts[3]
            if not re.match(r'^[a-zA-Z0-9_\-]+$', raw_id):
                self._set_headers(400)
                self.wfile.write(b'{"error": "Identificador de sessio invalid"}')
                return

            user_id = str(data.get("user_id", "user_local_web"))[:64]
            try:
                order = coord.request_cancellation(raw_id, requesting_user_id=user_id)
                self._set_headers(200)
                self.wfile.write(json.dumps({"session_id": raw_id, "status": order.status}).encode("utf-8"))
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
    """Aixeca el servidor HTTP en un fil separat sobre 127.0.0.1:8777."""

    def __init__(self, coordinator: MockCoordinator, port: int = 8777):
        self.coordinator = coordinator
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        handler_class = LocalCoordinatorHTTPHandler
        handler_class.coordinator = self.coordinator
        # Bind estrictament a 127.0.0.1 (mai 0.0.0.0)
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

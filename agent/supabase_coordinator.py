"""
agent/supabase_coordinator.py
=============================
Coordinador de comunicació asíncrona per al Surebet Monitor basat en Supabase.

Gestiona les peticions HTTPS/REST i crides RPC autenticades cap a Supabase:
1. Reclamació atòmica de sessions pendents mitjançant `claim_next_pending_session`.
2. Emissió de batecs de cor periòdics (`send_session_heartbeat`) amb detecció cooperativa de cancel·lació.
3. Finalització i notificació d'errors de sessions (`complete_session`, `fail_session`).
4. Publicació de snapshots i oportunitats accionables (`publish_session_snapshot`).
5. Consulta d'oportunitats actives filtrades a menys de 30 minuts (`v_active_surebets`).
6. Resiliència de xarxa amb reintents i retrocés exponencial (Exponential Backoff).
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Tuple
from agent.protocol import (
    SearchOrder,
    SnapshotRecord,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_TIMEOUT
)


class SupabaseCoordinatorError(Exception):
    """Excepció base per a errors de comunicació amb Supabase."""
    pass


class SupabaseAuthenticationError(SupabaseCoordinatorError):
    """Excepció per a errors d'autenticació o permisos insuficients (HTTP 401/403)."""
    pass


class SupabaseCoordinator:
    """
    Client de coordinació per a l'agent Windows cap a l'API REST de Supabase.
    No requereix cap llibreria externa addicional (funciona amb urllib natiu).
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        agent_name: str = "WindowsAgent",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5
    ):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        self.agent_name = agent_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self._headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"Prediccions-Agent/{self.agent_name}"
        }

    # -----------------------------------------------------------------
    # Mètodes de baix nivell amb gestió d'errors i reintents
    # -----------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        prefer_header: Optional[str] = None
    ) -> Any:
        url = f"{self.supabase_url}{path}"
        headers = dict(self._headers)
        if prefer_header:
            headers["Prefer"] = prefer_header

        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    resp_body = resp.read().decode("utf-8")
                    if not resp_body:
                        return None
                    return json.loads(resp_body)

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                if e.code in (401, 403):
                    raise SupabaseAuthenticationError(
                        f"Error d'autenticació o permisos a Supabase ({e.code}): {err_body}"
                    )
                # Reintentar només en errors transitoris de servidor (502, 503, 504) o 429
                if e.code in (429, 502, 503, 504) and attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                    time.sleep(sleep_time)
                    continue

                raise SupabaseCoordinatorError(f"HTTPError {e.code} a {path}: {err_body}")

            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = e
                if attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                    time.sleep(sleep_time)
                    continue
                raise SupabaseCoordinatorError(f"Error de xarxa connectant amb {url}: {e}")

        if last_error:
            raise SupabaseCoordinatorError(f"Fallada després de {self.max_retries} intents: {last_error}")

    def call_rpc(self, rpc_name: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """Executa una funció RPC a /rest/v1/rpc/<nom>."""
        return self._request("POST", f"/rest/v1/rpc/{rpc_name}", payload or {})

    # -----------------------------------------------------------------
    # Cicle de vida de la Cua de Feines (Job Queue)
    # -----------------------------------------------------------------

    def claim_pending_order(self) -> Optional[SearchOrder]:
        """
        Reclama de forma atòmica la següent ordre de cerca pendent a Supabase.
        L'agent s'identifica automàticament mitjançant auth.uid() al servidor.
        """
        res = self.call_rpc("claim_next_pending_session", {})
        if not res or not isinstance(res, list) or len(res) == 0:
            return None

        row = res[0]
        s_id = row.get("session_id")
        duration_sec = row.get("duration_sec", 600)
        agent_id = row.get("assigned_agent_id", "")

        return SearchOrder(
            session_id=str(s_id),
            user_id="server_assigned",
            status=STATUS_RUNNING,
            requested_duration_minutes=int(duration_sec // 60),
            assigned_agent_id=str(agent_id),
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )

    def send_heartbeat(self, session_id: str) -> Tuple[bool, bool]:
        """
        Envia un batec de cor a Supabase per a la sessió en curs.
        Retorna:
            (is_alive: bool, is_cancel_requested: bool)
        Si cancel_requested és True, l'agent ha d'avortar la cerca cooperativament.
        """
        res = self.call_rpc("send_session_heartbeat", {"p_session_id": session_id})
        if not res or not isinstance(res, list) or len(res) == 0:
            return False, False

        row = res[0]
        return bool(row.get("alive", False)), bool(row.get("cancel_requested", False))

    def complete_order(self, session_id: str, summary: Optional[Dict[str, Any]] = None) -> bool:
        """Marca la sessió com a completada amb el seu resum executiu."""
        res = self.call_rpc("complete_session", {
            "p_session_id": session_id,
            "p_summary": summary or {}
        })
        return bool(res)

    def fail_order(self, session_id: str, error_message: str) -> bool:
        """Notifica una fallada d'execució a la sessió."""
        res = self.call_rpc("fail_session", {
            "p_session_id": session_id,
            "p_error_message": error_message[:1000]
        })
        return bool(res)

    def request_cancellation(self, session_id: str) -> bool:
        """Sol·licita la cancel·lació d'una sessió."""
        res = self.call_rpc("request_session_cancellation", {
            "p_session_id": session_id
        })
        return bool(res)

    # -----------------------------------------------------------------
    # Publicació de Snapshots i Consulta d'Oportunitats
    # -----------------------------------------------------------------

    def publish_snapshot(
        self,
        snapshot: SnapshotRecord,
        opportunities: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
        """
        Publica un snapshot de quotes fresques i les oportunitats accionables detectades.
        """
        payload = {
            "p_session_id": snapshot.session_id,
            "p_odds_age_seconds": int(snapshot.age_max_seconds),
            "p_market_type": str(snapshot.market_name),
            "p_match_id": str(snapshot.opportunity_id),
            "p_payload": snapshot.to_dict(),
            "p_opportunities": opportunities or []
        }
        res = self.call_rpc("publish_session_snapshot", payload)
        if res:
            return str(res)
        return None

    def get_active_opportunities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Consulta les oportunitats matemàtiques actives detectades fa menys de 30 minuts.
        """
        path = f"/rest/v1/v_active_surebets?select=*&limit={limit}"
        res = self._request("GET", path)
        if isinstance(res, list):
            return res
        return []

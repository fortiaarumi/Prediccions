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


class HeartbeatResult(tuple):
    """Resultat del batec de vida compatible amb desempaquetament de tupla, atributs i mètode .get()."""
    def __new__(cls, alive: bool, cancel_requested: bool):
        return super().__new__(cls, (alive, cancel_requested))

    @property
    def alive(self) -> bool:
        return self[0]

    @property
    def cancel_requested(self) -> bool:
        return self[1]

    def get(self, key: str, default: Any = None) -> Any:
        if key == "alive":
            return self[0]
        if key == "cancel_requested":
            return self[1]
        return default


class SupabaseCoordinator:
    """
    Client de coordinació per a l'agent Windows cap a l'API REST de Supabase.
    No requereix cap llibreria externa addicional (funciona amb urllib natiu).
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_anon_key: Optional[str] = None,
        agent_token: Optional[str] = None,
        agent_name: str = "WindowsAgent",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        token_provider: Optional[Any] = None,
        supabase_key: Optional[str] = None,  # Compatibilitat amb crides prèvies
        publishable_key: Optional[str] = None, # Suport explícit al format modern de Supabase
        agent_email: Optional[str] = None,
        agent_password: Optional[str] = None,
    ):
        pub_key = publishable_key or supabase_anon_key or supabase_key
        if not pub_key:
            raise ValueError("Cal proporcionar la clau pública de l'aplicació (sb_publishable_... o anon_key).")

        self.supabase_url = supabase_url.rstrip("/")
        self.publishable_key = pub_key
        self.supabase_anon_key = pub_key  # Àlies de compatibilitat retroactiva
        self.agent_token = agent_token
        self.agent_name = agent_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.token_provider = token_provider

        # Validació de seguretat estricta (Mínim Privilegi):
        # L'agent NO pot tenir sota cap concepte una clau privilegiada 'service_role' o 'sb_secret_'.
        for k in (self.publishable_key, self.agent_token):
            if k and any(bad in k.lower() for bad in ("service_role", "sb_secret_", "secret", "master_key")):
                raise ValueError(
                    "Seguretat: L'agent local no admet claus privilegiades ('sb_secret_' o 'service_role'). "
                    "Ha d'operar exclusivament amb la clau pública (sb_publishable_... o anon) i autenticació d'usuari dedicada (JWT)."
                )

        self._headers = {
            "apikey": self.publishable_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"Prediccions-Agent/{self.agent_name}"
        }
        self._update_auth_header()

        if agent_email and agent_password and not self.agent_token:
            self.authenticate_agent_user(agent_email, agent_password)

    def _update_auth_header(self) -> None:
        """Configura la capçalera Authorization: només s'envia Bearer JWT si hi ha sessió d'usuari o clau JWT."""
        if self.agent_token:
            # Sessió d'usuari/agent activa: el JWT s'envia a Authorization: Bearer
            self._headers["Authorization"] = f"Bearer {self.agent_token}"
        elif self.publishable_key.startswith("sb_publishable_"):
            # Les claus modernes sb_publishable_ són opaques (NO són JWTs).
            # NO s'han d'enviar com a Bearer token per evitar errors '401 Invalid JWT' a Supabase.
            self._headers.pop("Authorization", None)
        elif self.publishable_key.startswith("eyJ"):
            # Clau anon clàssica basada en JWT: suport de retrocompatibilitat anònima
            self._headers["Authorization"] = f"Bearer {self.publishable_key}"
        else:
            self._headers.pop("Authorization", None)

    def authenticate_agent_user(self, email: str, password: str) -> str:
        """
        Autentica l'agent com a usuari dedicat amb rol 'agent' via Supabase Auth
        (POST /auth/v1/token?grant_type=password).
        Retorna l'access_token (JWT) i l'assigna a les capçaleres Authorization.
        """
        payload = {
            "email": email,
            "password": password
        }
        res = self._request("POST", "/auth/v1/token?grant_type=password", payload)
        if not res or "access_token" not in res:
            raise SupabaseAuthenticationError("No s'ha pogut obtenir el JWT d'autenticació de l'agent")

        token = res["access_token"]
        self.set_agent_jwt(token)
        return token

    def set_agent_jwt(self, jwt_token: str) -> None:
        """Estableix o renova el JWT limitat de l'agent per a peticions autenticades."""
        if any(bad in jwt_token.lower() for bad in ("service_role", "sb_secret_", "secret", "master_key")):
            raise ValueError("No es permet assignar una clau de servei privilegiada ('service_role' o 'sb_secret_') com a token d'agent")
        self.agent_token = jwt_token
        self._headers["Authorization"] = f"Bearer {jwt_token}"

    def refresh_auth_token(self, new_token: str) -> None:
        """Mètode de compatibilitat: renova el JWT d'agent o la clau d'accés en calent."""
        if any(bad in new_token.lower() for bad in ("service_role", "sb_secret_", "secret", "master_key")):
            raise ValueError("No es permet assignar una clau privilegiada ('service_role' o 'sb_secret_') com a token")
        if new_token.startswith("sb_publishable_"):
            self.publishable_key = new_token
            self.supabase_anon_key = new_token
            self._headers["apikey"] = new_token
            self._update_auth_header()
        elif new_token.startswith("eyJ"):
            # Token JWT (d'usuari agent autenticat)
            self.set_agent_jwt(new_token)
        else:
            # Clau genèrica per a entorns de prova o simuladors
            self.publishable_key = new_token
            self.supabase_anon_key = new_token
            self.agent_token = new_token
            self._headers["apikey"] = new_token
            self._headers["Authorization"] = f"Bearer {new_token}"

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

    def create_search_session(self, duration_seconds: int = 600) -> str:
        """
        Crea una sessió de cerca a Supabase mitjançant l'RPC 'create_search_session'.
        L'usuari s'identifica a través del seu JWT (auth.uid()).
        """
        res = self.call_rpc("create_search_session", {"p_duration_seconds": int(duration_seconds)})
        if not res:
            raise SupabaseCoordinatorError("No s'ha pogut obtenir l'identificador de sessió creada")
        return str(res)

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

    def send_heartbeat(self, *args: Any, session_id: Optional[str] = None) -> HeartbeatResult:
        """
        Envia un batec de cor a Supabase:
        - Si es passa session_id (com a argument únic, segon argument posicional o kwarg),
          crida a send_session_heartbeat per a la sessió en curs.
        - Si no hi ha sessió activa (agent en repòs), crida a register_agent_heartbeat
          per renovar daemon_agents.last_heartbeat.
        Retorna HeartbeatResult(alive: bool, cancel_requested: bool).
        """
        target_session = session_id
        if not target_session and args:
            if len(args) == 1:
                target_session = args[0]
            elif len(args) >= 2:
                target_session = args[1]

        if target_session:
            res = self.call_rpc("send_session_heartbeat", {"p_session_id": str(target_session)})
            if not res or not isinstance(res, list) or len(res) == 0:
                return HeartbeatResult(False, False)
            row = res[0]
            return HeartbeatResult(bool(row.get("alive", False)), bool(row.get("cancel_requested", False)))
        else:
            res = self.call_rpc("register_agent_heartbeat", {})
            return HeartbeatResult(bool(res), False)

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
        opportunities: Optional[List[Dict[str, Any]]] = None,
        snapshot_uuid: Optional[str] = None
    ) -> Optional[str]:
        """
        Publica un snapshot de quotes fresques i les oportunitats accionables detectades.
        Admet un snapshot_uuid determinista per garantir la idempotència de reintents.
        """
        payload = {
            "p_session_id": snapshot.session_id,
            "p_odds_age_seconds": int(snapshot.age_max_seconds),
            "p_market_type": str(snapshot.market_name),
            "p_match_id": str(snapshot.opportunity_id),
            "p_payload": snapshot.to_dict(),
            "p_opportunities": opportunities or []
        }
        if snapshot_uuid:
            payload["p_snapshot_id"] = snapshot_uuid

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

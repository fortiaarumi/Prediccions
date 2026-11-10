"""
agent/coordinator.py
====================
Coordinador de senyalització i cua de feines per al Surebet Monitor.

Proporciona:
1. `MockCoordinator`: Implementació local thread-safe per a l'Entrega A.
   - Aplica simulació de Row Level Security (RLS) aïllant usuaris.
   - Assignació atòmica de tasques (bloqueig de concurrència per evitar execucions dobles).
   - Validació estricta de la màquina d'estats.
   - Seguiment de presència i heartbeat de l'agent Windows.
   - Proves negatives de seguretat (rebuig d'accés no autoritzat).
"""

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from agent.protocol import (
    SearchOrder,
    AgentPresence,
    SnapshotRecord,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_TIMEOUT,
    STATUS_FAILED,
    validate_transition
)


class MockCoordinator:
    """Coordinador simulat en memòria per a proves d'integració local i de seguretat."""

    def __init__(self, heartbeat_timeout_seconds: float = 60.0):
        self._lock = threading.RLock()
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.orders: Dict[str, SearchOrder] = {}
        self.agents: Dict[str, AgentPresence] = {}
        self.snapshots: Dict[str, List[SnapshotRecord]] = {}  # session_id -> List[SnapshotRecord]

    # ----- Gestió d'Ordres i Sessions (Web / Usuaris) -----

    def create_order(
        self,
        user_id: str,
        duration_minutes: int = 15,
        session_id: Optional[str] = None
    ) -> SearchOrder:
        """Crea una nova petició de cerca des del panell web associada a un usuari concret."""
        with self._lock:
            if duration_minutes < 1 or duration_minutes > 120:
                raise ValueError("La durada de la cerca ha d'estar compresa entre 1 i 120 minuts")

            s_id = session_id or f"sess_{int(time.time()*1000)}"
            order = SearchOrder(
                session_id=s_id,
                user_id=user_id,
                status=STATUS_PENDING,
                requested_duration_minutes=duration_minutes,
                created_at=datetime.now(timezone.utc).isoformat()
            )
            self.orders[s_id] = order
            self.snapshots[s_id] = []
            return order

    def request_cancellation(self, session_id: str, requesting_user_id: Optional[str] = None) -> SearchOrder:
        """
        Sol·licita la cancel·lació d'una sessió.
        Seguretat (RLS): Si s'especifica un usuari, només el propietari pot cancel·lar.
        """
        with self._lock:
            order = self.orders.get(session_id)
            if not order:
                raise KeyError(f"Sessió {session_id} no trobada")

            # Comprovació de privilegis d'usuari (Simulació RLS)
            if requesting_user_id and order.user_id != requesting_user_id:
                raise PermissionError(
                    f"Accés denegat: L'usuari '{requesting_user_id}' no té permís per cancel·lar la sessió de '{order.user_id}'"
                )

            if order.status in (STATUS_CANCELLED, STATUS_COMPLETED, STATUS_TIMEOUT, STATUS_FAILED):
                return order  # Ja està finalitzada

            if not validate_transition(order.status, STATUS_CANCEL_REQUESTED):
                raise ValueError(f"Transició no permesa: {order.status} -> {STATUS_CANCEL_REQUESTED}")

            order.status = STATUS_CANCEL_REQUESTED
            return order

    def get_order(self, session_id: str, requesting_user_id: Optional[str] = None) -> Optional[SearchOrder]:
        """Recupera una sessió aplicant aïllament d'usuari si s'especifica requesting_user_id."""
        with self._lock:
            order = self.orders.get(session_id)
            if not order:
                return None
            if requesting_user_id and order.user_id != requesting_user_id:
                return None  # RLS oculta registres aliens
            return order

    def get_user_orders(self, user_id: str) -> List[SearchOrder]:
        """Retorna totes les sessions que pertanyen a l'usuari (RLS)."""
        with self._lock:
            return [o for o in self.orders.values() if o.user_id == user_id]

    # ----- Gestió de l'Agent Windows (Treballador Local) -----

    def register_heartbeat(
        self,
        agent_id: str,
        status: str = "ONLINE",
        version: str = "2.1.0",
        active_session_id: Optional[str] = None
    ) -> AgentPresence:
        """Registra la presència i el pols periòdic de l'agent local Windows."""
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            presence = AgentPresence(
                agent_id=agent_id,
                status=status,
                last_heartbeat=now_iso,
                version=version,
                active_session_id=active_session_id
            )
            self.agents[agent_id] = presence

            # Si hi ha una sessió activa, actualitzar el last_heartbeat de la sessió
            if active_session_id and active_session_id in self.orders:
                self.orders[active_session_id].last_heartbeat = now_iso

            return presence

    def claim_pending_order(self, agent_id: str) -> Optional[SearchOrder]:
        """
        Assignació atòmica de la següent feina PENDING a l'agent Windows.
        Garanteix amb bloqueig que dues crides simultànies no assignin la mateixa feina.
        """
        with self._lock:
            # 1. Neteja preventiva de sessions caducades per timeout
            self.check_timeouts()

            for order in self.orders.values():
                if order.status == STATUS_PENDING:
                    # Assignar de forma atòmica
                    order.status = STATUS_RUNNING
                    order.assigned_agent_id = agent_id
                    now_iso = datetime.now(timezone.utc).isoformat()
                    order.started_at = now_iso
                    order.last_heartbeat = now_iso

                    # Actualitzar estat de l'agent a BUSY
                    if agent_id in self.agents:
                        self.agents[agent_id].status = "BUSY"
                        self.agents[agent_id].active_session_id = order.session_id

                    return order
            return None

    def poll_order_status(self, session_id: str) -> Optional[str]:
        """L'agent comprova periòdicament l'estat de la feina (per detectar cancel·lació web)."""
        with self._lock:
            order = self.orders.get(session_id)
            return order.status if order else None

    def update_session_state(
        self,
        session_id: str,
        new_status: str,
        agent_id: str,
        error_message: Optional[str] = None
    ) -> SearchOrder:
        """L'agent Windows actualitza l'estat de la sessió validant la màquina d'estats."""
        with self._lock:
            order = self.orders.get(session_id)
            if not order:
                raise KeyError(f"Sessió {session_id} no trobada")

            if order.assigned_agent_id and order.assigned_agent_id != agent_id:
                raise PermissionError(f"L'agent '{agent_id}' no té autorització per modificar la sessió assignada a '{order.assigned_agent_id}'")

            if not validate_transition(order.status, new_status):
                raise ValueError(f"Transició d'estat il·legal: {order.status} -> {new_status}")

            order.status = new_status
            if error_message:
                order.error_message = error_message
            if new_status in (STATUS_CANCELLED, STATUS_COMPLETED, STATUS_TIMEOUT, STATUS_FAILED):
                order.ended_at = datetime.now(timezone.utc).isoformat()
                if agent_id in self.agents:
                    self.agents[agent_id].status = "ONLINE"
                    self.agents[agent_id].active_session_id = None

            return order

    def publish_snapshot(self, session_id: str, snapshot: SnapshotRecord) -> None:
        """Publica un snapshot d'oportunitats per a la sessió activa."""
        with self._lock:
            if session_id not in self.snapshots:
                self.snapshots[session_id] = []
            self.snapshots[session_id].append(snapshot)

    def get_session_snapshots(self, session_id: str, requesting_user_id: Optional[str] = None) -> List[SnapshotRecord]:
        """Recupera els snapshots d'una sessió protegits per RLS."""
        with self._lock:
            order = self.orders.get(session_id)
            if not order:
                return []
            if requesting_user_id and order.user_id != requesting_user_id:
                return []
            return list(self.snapshots.get(session_id, []))

    def get_active_opportunities(self, max_age_seconds: float = 1800.0) -> List[SnapshotRecord]:
        """Recupera totes les oportunitats registrades en snapshots amb antiguitat <= max_age_seconds (30 minuts)."""
        with self._lock:
            now = datetime.now(timezone.utc)
            active = []
            for sess_id, snaps in self.snapshots.items():
                for s in snaps:
                    try:
                        c_dt = datetime.fromisoformat(s.captured_at)
                        if c_dt.tzinfo is None:
                            c_dt = c_dt.replace(tzinfo=timezone.utc)
                        age = (now - c_dt).total_seconds()
                    except Exception:
                        age = 0.0
                    if age <= max_age_seconds:
                        active.append(s)
            active.sort(key=lambda x: x.captured_at, reverse=True)
            return active

    def check_timeouts(self) -> List[str]:
        """
        Detecta sessions en RUNNING sense heartbeat recent i les marca com a TIMEOUT.
        També marca agents desconnectats com a OFFLINE.
        """
        with self._lock:
            timed_out = []
            now = datetime.now(timezone.utc)

            # 1. Comprovar agents inactius
            for a_id, agent in self.agents.items():
                hb_dt = datetime.fromisoformat(agent.last_heartbeat)
                if (now - hb_dt).total_seconds() > self.heartbeat_timeout_seconds:
                    agent.status = "OFFLINE"

            # 2. Comprovar sessions penjades
            for s_id, order in self.orders.items():
                if order.status == STATUS_RUNNING and order.last_heartbeat:
                    hb_dt = datetime.fromisoformat(order.last_heartbeat)
                    if (now - hb_dt).total_seconds() > self.heartbeat_timeout_seconds:
                        order.status = STATUS_TIMEOUT
                        order.ended_at = now.isoformat()
                        order.error_message = "Sessió expirada: L'agent Windows no ha respost al heartbeat."
                        timed_out.append(s_id)

            return timed_out


"""
agent/windows_agent.py
======================
Agent local per a Windows que executa cerques de Surebets sota demanda.

Característiques clau:
1. Connexió sortint pura (Pull-based via HTTPS/REST, zero ports oberts al router).
2. Reutilització directa de `SurebetDaemon` i `ArbitrageEngine` existents sense duplicació.
3. Cicle de vida cooperatiu: cancel·lació immediata en rebre ordre des de la web.
4. Historial de 30 minuts gestionat amb `SnapshotStore`.
5. Netetja garantida de subprocessos i recursos en tancar.
6. Suport per a mode simulat (offline=True) sense peticions reals a cases d'apostes.
"""

import os
import sys
import time
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from agent.protocol import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED
)
from agent.coordinator import MockCoordinator
from agent.snapshot_store import SnapshotStore
from surebet_daemon import SurebetDaemon


class WindowsSurebetAgent:
    def __init__(
        self,
        coordinator: Any,
        agent_id: str = "agent-win-local-01",
        heartbeat_interval_seconds: float = 5.0,
        offline: bool = True,
        dry_run: bool = True
    ):
        self.coordinator = coordinator
        self.agent_id = agent_id
        self.heartbeat_interval = heartbeat_interval_seconds
        self.offline = offline
        self.dry_run = dry_run

        self.running = False
        self.active_session_id: Optional[str] = None
        self.current_daemon: Optional[SurebetDaemon] = None
        self.snapshot_store = SnapshotStore(max_snapshots=500, max_age_seconds=1800.0)

        self._last_heartbeat_time = 0.0

    def send_heartbeat(self, status: Optional[str] = None) -> bool:
        """Envia un pols periòdic de vida al coordinador. Retorna True si s'ha sol·licitat cancel·lació."""
        self._last_heartbeat_time = time.time()
        if hasattr(self.coordinator, "send_heartbeat"):
            # SupabaseCoordinator (PostgreSQL RPC)
            res = self.coordinator.send_heartbeat(self.agent_id, self.active_session_id)
            return bool(res.get("cancel_requested", False))
        elif hasattr(self.coordinator, "register_heartbeat"):
            # MockCoordinator (Memòria / Proves locals)
            st = status or ("BUSY" if self.active_session_id else "ONLINE")
            self.coordinator.register_heartbeat(
                agent_id=self.agent_id,
                status=st,
                version="2.1.0",
                active_session_id=self.active_session_id
            )
            return False
        return False

    def _is_cancel_requested(self, session_id: str) -> bool:
        """Comprova cooperativament si cal cancel·lar la sessió."""
        if self.send_heartbeat(status="BUSY"):
            return True
        if hasattr(self.coordinator, "poll_order_status"):
            st = self.coordinator.poll_order_status(session_id)
            if st == STATUS_CANCEL_REQUESTED:
                return True
        return False

    def _finalize_session(
        self,
        session_id: str,
        final_status: str,
        error_message: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None
    ) -> None:
        """Tanca la sessió atòmicament al coordinador de forma compatible."""
        if final_status == STATUS_COMPLETED:
            if hasattr(self.coordinator, "complete_order"):
                self.coordinator.complete_order(session_id, summary=summary or {})
            elif hasattr(self.coordinator, "update_session_state"):
                self.coordinator.update_session_state(session_id, STATUS_COMPLETED, self.agent_id)
        elif final_status == STATUS_CANCELLED:
            if hasattr(self.coordinator, "cancel_order"):
                self.coordinator.cancel_order(session_id)
            elif hasattr(self.coordinator, "update_session_state"):
                self.coordinator.update_session_state(session_id, STATUS_CANCELLED, self.agent_id)
        else:
            if hasattr(self.coordinator, "fail_order"):
                self.coordinator.fail_order(session_id, error_message=error_message or "Error d'execució")
            elif hasattr(self.coordinator, "update_session_state"):
                self.coordinator.update_session_state(session_id, STATUS_FAILED, self.agent_id, error_message=error_message)

    def process_one_order(self, order) -> str:
        """
        Executa una comanda de cerca de Surebets cooperativament.
        Retorna l'estat final ('COMPLETED', 'CANCELLED' o 'FAILED').
        """
        session_id = order.session_id
        max_duration_seconds = order.requested_duration_minutes * 60
        self.active_session_id = session_id
        print(f"\n🚀 [AGENT] Iniciant sessió {session_id} (Durada demanada: {order.requested_duration_minutes} min)...")

        # Instanciar el motor SurebetDaemon existent sense duplicar-lo
        daemon = SurebetDaemon(
            enforce_strict_freshness=False,
            offline=self.offline,
            dry_run=self.dry_run
        )
        self.current_daemon = daemon
        daemon.start_session(session_id, max_duration_seconds=max_duration_seconds)

        start_time = time.time()
        cycle_count = 0
        final_status = STATUS_COMPLETED
        err_msg = None

        try:
            while True:
                # 1. Comprovar cancel·lació cooperativa
                if self._is_cancel_requested(session_id) or daemon.cancel_requested:
                    print(f"🛑 [AGENT] Cancel·lació sol·licitada pel web per a {session_id}!")
                    daemon.request_cancellation()
                    daemon.check_cancellation()
                    final_status = STATUS_CANCELLED
                    break

                # 2. Comprovar si s'ha superat el temps de cerca demanat
                elapsed = time.time() - start_time
                if elapsed >= max_duration_seconds:
                    print(f"🏁 [AGENT] Temps màxim de sessió assolit ({elapsed:.1f}s >= {max_duration_seconds}s).")
                    final_status = STATUS_COMPLETED
                    break

                # 3. Executar un cicle de cerca amb SurebetDaemon
                cycle_count += 1
                cycle_res = daemon.run_cycle()
                surebets = cycle_res.get("surebets", [])

                # 4. Publicar snapshots a l'SnapshotStore (amb historial de 30 minuts)
                now_utc = datetime.now(timezone.utc)
                for arb in surebets:
                    snap_record = self.snapshot_store.add_opportunity(
                        session_id=session_id,
                        cycle_index=cycle_count,
                        arb=arb,
                        now=now_utc
                    )
                    if snap_record:
                        self.coordinator.publish_snapshot(session_id, snap_record)

                print(f"   [Cicle #{cycle_count}] Oportunitats registrades a l'historial de 30 min: {len(surebets)}.")

                # Pauses curtes atenent cancel·lacions ràpides
                sleep_target = min(5, int(max_duration_seconds - elapsed))
                if sleep_target <= 0:
                    break

                for _ in range(sleep_target):
                    if self._is_cancel_requested(session_id) or daemon.cancel_requested:
                        daemon.request_cancellation()
                        daemon.check_cancellation()
                        final_status = STATUS_CANCELLED
                        break
                    time.sleep(1)

                if final_status == STATUS_CANCELLED:
                    break

        except Exception as e:
            print(f"⚠️  [AGENT] Error durant l'execució de la sessió: {e}")
            final_status = STATUS_FAILED
            err_msg = str(e)

        # 5. Finalitzar la sessió atòmicament al coordinador
        print(f"✅ [AGENT] Sessió {session_id} finalitzada amb estat: {final_status}.")
        self._finalize_session(
            session_id=session_id,
            final_status=final_status,
            error_message=err_msg,
            summary={"cycles": cycle_count, "completed_at": datetime.now(timezone.utc).isoformat()}
        )
        self.cleanup_resources()
        self.active_session_id = None
        self.send_heartbeat(status="ONLINE")
        return final_status

    def cleanup_resources(self) -> None:
        """Allibera subprocessos, scrapers i memòria en sortir o cancel·lar."""
        if self.current_daemon:
            self.current_daemon.running = False
            self.current_daemon = None
        print("🧹 [AGENT] Recursos i processos locals alliberats correctament.")

    def run_poll_loop(self, max_idle_cycles: Optional[int] = None) -> None:
        """Bucle principal de l'agent: escolta feines a la cua i envia pols periòdic."""
        self.running = True
        print(f"[*] Agent Windows '{self.agent_id}' actiu i connectat al coordinador.")
        print("[*] Connexió sortint segura establerta (Pull-based HTTPS). Esperant ordres...")

        idle_cycles = 0
        while self.running:
            try:
                # 1. Enviar heartbeat periòdic
                now_s = time.time()
                if now_s - self._last_heartbeat_time >= self.heartbeat_interval:
                    self.send_heartbeat()

                # 2. Intentar reservar una ordre pendent de forma atòmica
                order = self.coordinator.claim_pending_order(self.agent_id)
                if order:
                    idle_cycles = 0
                    self.process_one_order(order)
                else:
                    idle_cycles += 1
                    if max_idle_cycles and idle_cycles >= max_idle_cycles:
                        print("[*] Assolit el límit de cicles d'inactivitat per a prova. Aturant.")
                        break
                    time.sleep(1)

            except KeyboardInterrupt:
                print("\n[!] Aturada manual de l'Agent Windows.")
                self.cleanup_resources()
                self.running = False
                break
            except Exception as e:
                print(f"[!] Error inesperat al bucle de l'agent: {e}")
                time.sleep(2)


def main():
    """Punt d'entrada interactiu per al script .bat de Windows."""
    print("=" * 65)
    print("      PREDICCIONS LLIGA — SUREBET LOCAL MONITOR (AGENT WINDOWS)")
    print("=" * 65)
    print("Versió: 2.1.0")
    print("Estat: Mode local simulat (Entrega A: Offline / Segur)")
    print("Port d'entrada: Cap (Zero ports oberts al router)")
    print("=" * 65)

    coordinator = MockCoordinator()
    agent = WindowsSurebetAgent(coordinator=coordinator, offline=True, dry_run=True)

    # Iniciar el pont HTTP local loopback per permetre la connexió des del navegador web
    from agent.local_bridge import LocalCoordinatorServer
    bridge = LocalCoordinatorServer(coordinator=coordinator, port=8777)
    bridge.start()

    # Registrar gestors de senyals per neteja neta
    def handle_exit(sig, frame):
        print("\nRebuda senyal de finalització. Tancant l'agent nètament...")
        bridge.stop()
        agent.cleanup_resources()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    try:
        agent.run_poll_loop()
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()


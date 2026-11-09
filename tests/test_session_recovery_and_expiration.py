"""
tests/test_session_recovery_and_expiration.py
Test suite per a la recuperació de sessions, gestió de caducitats (timeouts),
interrupcions manuals (Ctrl+C / KeyboardInterrupt) i prevenció de deadlocks
en el cicle de vida del Surebet Monitor.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from agent.protocol import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_TIMEOUT,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from agent.coordinator import MockCoordinator
from agent.windows_agent import WindowsSurebetAgent
from agent.supabase_coordinator import SupabaseCoordinator


class TestSessionRecoveryAndExpiration:
    """Proves unitàries i d'integració per al cicle de vida i recuperació de sessions."""

    def test_mock_coordinator_timeout_cleanup_on_claim(self):
        """
        Verifica que quan un agent intenta reclamar una feina, el coordinador
        fa neteja preventiva de les sessions caducades (TIMEOUT) abans de
        comprovar la concurrència, evitant el bloqueig d'ordres pendents.
        """
        coordinator = MockCoordinator(heartbeat_timeout_seconds=1.0)

        order_stale = coordinator.create_order(user_id="user_1", duration_minutes=1, session_id="sess_stale_01")
        claimed_stale = coordinator.claim_pending_order("agent-001")
        assert claimed_stale is not None
        assert claimed_stale.session_id == "sess_stale_01"
        assert claimed_stale.status == STATUS_RUNNING

        order_pending = coordinator.create_order(user_id="user_2", duration_minutes=5, session_id="sess_pending_02")
        assert order_pending.status == STATUS_PENDING

        time.sleep(1.1)

        claimed_new = coordinator.claim_pending_order("agent-001")
        assert claimed_new is not None
        assert claimed_new.session_id == "sess_pending_02"
        assert claimed_new.status == STATUS_RUNNING

        assert coordinator.get_order("sess_stale_01").status == STATUS_TIMEOUT

    def test_windows_agent_keyboard_interrupt_graceful_cancellation(self):
        """
        Verifica que quan l'agent Windows rep un KeyboardInterrupt (Ctrl+C)
        durant l'execució d'una sessió, la captura, notifica al coordinador
        l'estat CANCELLED amb el resum adient, allibera recursos i rellança l'excepció.
        """
        coordinator = MockCoordinator()
        agent = WindowsSurebetAgent(
            coordinator=coordinator,
            agent_id="test-agent-win",
            heartbeat_interval_seconds=0.2,
            offline=True,
            dry_run=True
        )

        order = coordinator.create_order(user_id="user_1", duration_minutes=10, session_id="sess_interrupt_01")
        claimed = coordinator.claim_pending_order(agent.agent_id)
        assert claimed is not None
        agent.active_session_id = claimed.session_id

        # Simulem que durant la comprovació del cicle es llença KeyboardInterrupt (Ctrl+C)
        with patch.object(agent, "_is_cancel_requested", side_effect=KeyboardInterrupt("Simulated Ctrl+C")):
            with pytest.raises(KeyboardInterrupt):
                agent.process_one_order(claimed)

        # Comprovem que l'agent ha notificat la cancel·lació cooperativa
        assert agent.active_session_id is None
        final_order = coordinator.get_order("sess_interrupt_01")
        assert final_order.status == STATUS_CANCELLED
        assert "Ctrl+C" in (final_order.error_message or "")

    def test_windows_agent_startup_recovers_stale_sessions(self):
        """
        Verifica que en iniciar-se l'agent Windows, executa recover_stale_sessions()
        per desbloquejar sessions que haguessin quedat penjades d'execucions anteriors.
        """
        coordinator = MockCoordinator(heartbeat_timeout_seconds=0.5)
        agent = WindowsSurebetAgent(
            coordinator=coordinator,
            agent_id="test-agent-win",
            heartbeat_interval_seconds=0.2,
            offline=True,
            dry_run=True
        )

        order = coordinator.create_order(user_id="user_1", duration_minutes=5, session_id="sess_orphan_01")
        coordinator.claim_pending_order(agent.agent_id)
        time.sleep(0.6)

        agent.recover_stale_sessions()

        assert coordinator.get_order("sess_orphan_01").status == STATUS_TIMEOUT

    def test_supabase_coordinator_recover_orphaned_sessions(self):
        """
        Verifica que recover_orphaned_sessions en SupabaseCoordinator:
        1. Invoca claim_next_pending_session RPC (on la migració neteja zombis).
        2. Consulta taula surebet_sessions per identificar sessions RUNNING expirades.
        3. Invoca fail_order per a cadascuna d'elles.
        """
        coord = SupabaseCoordinator(
            supabase_url="https://mock-test.supabase.co",
            publishable_key="mock-anon-key",
            agent_token="mock-agent-jwt"
        )

        expired_session_id = "da6fa2de-3742-4faf-b9aa-bff8fd2b1455"

        with patch.object(coord, "call_rpc") as mock_rpc, \
             patch.object(coord, "_request") as mock_req, \
             patch.object(coord, "fail_order") as mock_fail:

            mock_rpc.return_value = None
            mock_req.return_value = [{"id": expired_session_id}]
            mock_fail.return_value = True

            recovered = coord.recover_orphaned_sessions("agent-test")

            assert expired_session_id in recovered
            mock_rpc.assert_called_once_with("claim_next_pending_session", {})
            mock_fail.assert_called_once()
            args, _ = mock_fail.call_args
            assert args[0] == expired_session_id

    def test_cancellation_request_atomic_transition(self):
        """
        Verifica que si una sessió està en RUNNING i se sol·licita cancel·lació,
        passa a CANCEL_REQUESTED, i en confirmar-se passa a CANCELLED.
        """
        coordinator = MockCoordinator()
        order = coordinator.create_order(user_id="user_1", duration_minutes=15, session_id="sess_cancel_test_01")
        claimed = coordinator.claim_pending_order("agent-001")
        assert claimed.status == STATUS_RUNNING

        # Sol·licitar cancel·lació des de la web
        updated_order = coordinator.request_cancellation("sess_cancel_test_01", requesting_user_id="user_1")
        assert updated_order is not None
        assert updated_order.status == STATUS_CANCEL_REQUESTED
        assert coordinator.get_order("sess_cancel_test_01").status == STATUS_CANCEL_REQUESTED

        # L'agent la cancel·la cooperativament
        agent = WindowsSurebetAgent(coordinator=coordinator, agent_id="agent-001", offline=True, dry_run=True)
        agent._finalize_session("sess_cancel_test_01", STATUS_CANCELLED, "Cancel·lat per usuari", {"cancelled": True})

        assert coordinator.get_order("sess_cancel_test_01").status == STATUS_CANCELLED

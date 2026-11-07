"""
tests/test_windows_agent_and_coordinator.py
===========================================
Suite de proves per a l'Entrega A: Agent Windows, Coordinador, Protocol
de Seguretat (RLS), Concurrència Atòmica i Historial de 30 minuts.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
import pytest

from agent.protocol import (
    SearchOrder,
    AgentPresence,
    generate_opportunity_id,
    generate_snapshot_id,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_TIMEOUT,
    STATUS_FAILED,
    validate_transition
)
from agent.coordinator import MockCoordinator
from agent.snapshot_store import SnapshotStore
from agent.windows_agent import WindowsSurebetAgent


@pytest.fixture
def coordinator():
    return MockCoordinator(heartbeat_timeout_seconds=2.0)


@pytest.fixture
def mock_agent(coordinator):
    return WindowsSurebetAgent(
        coordinator=coordinator,
        agent_id="test-agent-win-01",
        heartbeat_interval_seconds=0.5,
        offline=True,
        dry_run=True
    )


# =============================================================================
# 1. PROVES DE CICLE DE VIDA I EXECUCIÓ DE CERCA
# =============================================================================

def test_full_search_execution_until_completion(coordinator, mock_agent):
    """
    Verifica el cicle complet d'una cerca:
    1. Usuari crea comanda (PENDING).
    2. Agent reserva la comanda (RUNNING).
    3. Execució d'un cicle de cerca simulat.
    4. En complir el temps de cerca, transita a COMPLETED.
    """
    order = coordinator.create_order(user_id="user_alfa", duration_minutes=1, session_id="sess_exec_01")
    assert order.status == STATUS_PENDING

    # Agent reserva la feina
    claimed = coordinator.claim_pending_order(mock_agent.agent_id)
    assert claimed is not None
    assert claimed.session_id == "sess_exec_01"
    assert claimed.status == STATUS_RUNNING

    # Simular temps transcorregut instantani per provar finalització
    def fast_process(order):
        # Override durada a 0.1s
        order.requested_duration_minutes = 0.002
        return mock_agent.process_one_order(order)

    status = fast_process(claimed)
    assert status == STATUS_COMPLETED

    # Comprovar estat final al coordinador
    final_order = coordinator.get_order("sess_exec_01")
    assert final_order.status == STATUS_COMPLETED
    assert final_order.ended_at is not None
    assert mock_agent.active_session_id is None


def test_cooperative_cancellation_flow(coordinator, mock_agent):
    """
    Verifica la seqüència estricta de cancel·lació cooperativa:
    RUNNING -> CANCEL_REQUESTED -> CANCELLED.
    Comprova que una sessió cancel·lada MAI no acaba en COMPLETED.
    """
    order = coordinator.create_order(user_id="user_beta", duration_minutes=15, session_id="sess_cancel_01")
    claimed = coordinator.claim_pending_order(mock_agent.agent_id)
    assert claimed.status == STATUS_RUNNING

    # Simular fil d'execució de l'agent i cancel·lació des de la web
    cancellation_triggered = threading.Event()

    def cancel_after_start():
        time.sleep(0.2)
        coordinator.request_cancellation("sess_cancel_01", requesting_user_id="user_beta")
        cancellation_triggered.set()

    t_cancel = threading.Thread(target=cancel_after_start)
    t_cancel.start()

    status = mock_agent.process_one_order(claimed)
    t_cancel.join()

    assert status == STATUS_CANCELLED
    final_order = coordinator.get_order("sess_cancel_01")
    assert final_order.status == STATUS_CANCELLED
    assert final_order.status != STATUS_COMPLETED
    assert mock_agent.current_daemon is None


# =============================================================================
# 2. PROVES DE SEGURETAT I RLS (PROVES NEGATIVES)
# =============================================================================

def test_negative_security_unauthorized_user_cannot_cancel_other_session(coordinator):
    """
    PROVA NEGATIVA RLS:
    L'usuari B intenta cancel·lar la sessió de l'usuari A.
    El coordinador ha de denegar l'acció amb PermissionError.
    """
    coordinator.create_order(user_id="user_legitim", duration_minutes=10, session_id="sess_protegida")

    with pytest.raises(PermissionError) as exc_info:
        coordinator.request_cancellation("sess_protegida", requesting_user_id="user_atacant")

    assert "Accés denegat" in str(exc_info.value)
    order = coordinator.get_order("sess_protegida")
    assert order.status == STATUS_PENDING


def test_negative_security_rls_data_isolation(coordinator):
    """
    PROVA NEGATIVA RLS:
    L'usuari B intenta llegir sessions i snapshots que pertanyen a l'usuari A.
    El coordinador retorna None o llista buida.
    """
    coordinator.create_order(user_id="user_A", duration_minutes=15, session_id="sess_privada_A")

    # Usuari B intenta obtenir la sessió
    res_b = coordinator.get_order("sess_privada_A", requesting_user_id="user_B")
    assert res_b is None

    # Usuari A sí que pot obtenir-la
    res_a = coordinator.get_order("sess_privada_A", requesting_user_id="user_A")
    assert res_a is not None
    assert res_a.session_id == "sess_privada_A"


def test_state_machine_invalid_transition_rejection(coordinator):
    """
    Verifica que les transicions il·legals són rebutjades:
    Exemple: CANCELLED -> RUNNING o COMPLETED -> CANCELLED.
    """
    assert validate_transition(STATUS_PENDING, STATUS_RUNNING) is True
    assert validate_transition(STATUS_RUNNING, STATUS_CANCEL_REQUESTED) is True
    assert validate_transition(STATUS_CANCEL_REQUESTED, STATUS_CANCELLED) is True

    # Transicions il·legals
    assert validate_transition(STATUS_CANCELLED, STATUS_RUNNING) is False
    assert validate_transition(STATUS_COMPLETED, STATUS_CANCEL_REQUESTED) is False
    assert validate_transition(STATUS_TIMEOUT, STATUS_RUNNING) is False


# =============================================================================
# 3. PROVES DE CONCURRÈNCIA ATÒMICA
# =============================================================================

def test_atomic_job_claiming_prevents_double_execution(coordinator):
    """
    Verifica que dos agents intentant reclamar la mateixa feina PENDING
    alhora de forma concurrent no puguin duplicar l'execució.
    Exactament un agent guanya i l'altre rep None.
    """
    coordinator.create_order(user_id="user_multi", duration_minutes=15, session_id="sess_concurrencia")

    results = []

    def claim_job(agent_id):
        order = coordinator.claim_pending_order(agent_id)
        if order:
            results.append((agent_id, order.session_id))

    t1 = threading.Thread(target=claim_job, args=("agent_1",))
    t2 = threading.Thread(target=claim_job, args=("agent_2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactament una reserva atòmica amb èxit
    assert len(results) == 1
    winner_agent, assigned_session = results[0]
    assert assigned_session == "sess_concurrencia"
    assert winner_agent in ("agent_1", "agent_2")


# =============================================================================
# 4. PROVES DE HEARTBEAT I TIMEOUT
# =============================================================================

def test_heartbeat_timeout_detection(coordinator):
    """
    Verifica que una sessió RUNNING sense pols de vida durant més temps
    que el timeout és marcada com a TIMEOUT, i l'agent queda OFFLINE.
    """
    coordinator.create_order(user_id="user_hb", duration_minutes=10, session_id="sess_hb")
    coordinator.register_heartbeat("agent_hb_01", status="BUSY", active_session_id="sess_hb")
    claimed = coordinator.claim_pending_order("agent_hb_01")
    assert claimed.status == STATUS_RUNNING

    # Simular silenci durant 3 segons (heartbeat_timeout = 2.0s)
    claimed.last_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    coordinator.agents["agent_hb_01"].last_heartbeat = claimed.last_heartbeat

    timed_out = coordinator.check_timeouts()
    assert "sess_hb" in timed_out

    order = coordinator.get_order("sess_hb")
    assert order.status == STATUS_TIMEOUT
    assert "Sessió expirada" in order.error_message
    assert coordinator.agents["agent_hb_01"].status == "OFFLINE"


# =============================================================================
# 5. PROVES D'HISTORIAL DE 30 MINUTS I DEDUPLICACIÓ DE SNAPSHOTS
# =============================================================================

def test_20_minute_old_opportunity_retained_in_recent_historical():
    """
    Verifica la directriu essencial:
    Una oportunitat detectada fa 20 minuts (1200s) CONTINUA DISPONIBLE
    com a 'RECENT_HISTORICAL' fins i tot després de 40 cicles posteriors.
    """
    store = SnapshotStore(max_snapshots=500, max_age_seconds=1800.0)
    now = datetime(2026, 11, 7, 20, 0, 0, tzinfo=timezone.utc)

    # 1. Registrar oportunitat fa 20 minuts (t_cap = now - 20 minuts = 1200 segons)
    t_20m_ago = now - timedelta(minutes=20)
    arb_old = {
        "match_id": "M_20M_AGO",
        "matchup": "Valencia vs Villarreal",
        "market_name": "1X vs 2",
        "profit_pct": 2.15,
        "legs": [
            {"bookmaker": "Winamax", "selection": "1X", "odd": 1.95, "captured_at": t_20m_ago.isoformat()},
            {"bookmaker": "Bet365", "selection": "2", "odd": 2.15, "captured_at": t_20m_ago.isoformat()}
        ]
    }
    rec_old = store.add_opportunity("sess_hist", cycle_index=1, arb=arb_old, now=t_20m_ago)
    assert rec_old is not None

    # 2. Simular 40 cicles posteriors amb altres oportunitats més recents
    for c in range(2, 42):
        t_cycle = t_20m_ago + timedelta(seconds=c * 25)  # fins a 1000s després
        arb_recent = {
            "match_id": f"M_CYCLE_{c}",
            "matchup": f"Team A vs Team B {c}",
            "market_name": "Over/Under",
            "profit_pct": 1.5,
            "legs": [
                {"bookmaker": "Winamax", "selection": "Over", "odd": 2.05, "captured_at": t_cycle.isoformat()},
                {"bookmaker": "Bet365", "selection": "Under", "odd": 2.05, "captured_at": t_cycle.isoformat()}
            ]
        }
        store.add_opportunity("sess_hist", cycle_index=c, arb=arb_recent, now=t_cycle)

    # 3. Consultar l'estat actual en el temps 'now' (fa exactament 20 minuts de la primera)
    report = store.get_active_snapshots(now=now)
    recent_historical = report["recent_historical"]

    # Comprovar que l'oportunitat de fa 20 minuts segueix viva a recent_historical
    found = [s for s in recent_historical if s["matchup"] == "Valencia vs Villarreal"]
    assert len(found) == 1, "L'oportunitat de fa 20 minuts ha de romandre disponible a recent_historical"
    assert found[0]["freshness_tier"] == "RECENT_HISTORICAL"
    assert 1190.0 <= found[0]["current_age_seconds"] <= 1210.0


def test_snapshots_older_than_30_minutes_pruned():
    """
    Verifica que les oportunitats que superen els 1800 segons (30 minuts)
    són automàticament purgades i NO es mostren a la llista activa.
    """
    store = SnapshotStore(max_snapshots=500, max_age_seconds=1800.0)
    now = datetime(2026, 11, 7, 20, 0, 0, tzinfo=timezone.utc)

    # Oportunitat de fa 31 minuts (1860s)
    t_31m_ago = now - timedelta(minutes=31)
    arb_too_old = {
        "match_id": "M_EXPIRED",
        "matchup": "Old Match",
        "market_name": "1X vs 2",
        "profit_pct": 3.0,
        "legs": [
            {"bookmaker": "Winamax", "selection": "1X", "odd": 2.0, "captured_at": t_31m_ago.isoformat()},
            {"bookmaker": "Bet365", "selection": "2", "odd": 2.1, "captured_at": t_31m_ago.isoformat()}
        ]
    }
    rec = store.add_opportunity("sess_expire", cycle_index=1, arb=arb_too_old, now=now)
    assert rec is None  # Rebutjada directament per edat > 1800s

    report = store.get_active_snapshots(now=now)
    assert len(report["all_active"]) == 0


def test_snapshot_deduplication_and_odd_change():
    """
    Verifica que:
    1. Si les quotes i timestamps són idèntics, no es duplica el snapshot.
    2. Si una quota canvia, es genera un nou snapshot mantenint l'anterior.
    """
    store = SnapshotStore(max_snapshots=500, max_age_seconds=1800.0)
    now = datetime(2026, 11, 7, 20, 0, 0, tzinfo=timezone.utc)
    t1 = now - timedelta(seconds=100)

    arb1 = {
        "match_id": "M_DEDUP",
        "matchup": "Girona vs Espanyol",
        "market_name": "1X vs 2",
        "profit_pct": 1.8,
        "legs": [
            {"bookmaker": "Winamax", "selection": "1X", "odd": 1.95, "captured_at": t1.isoformat()},
            {"bookmaker": "Bet365", "selection": "2", "odd": 2.15, "captured_at": t1.isoformat()}
        ]
    }

    # Primer afegit
    rec1 = store.add_opportunity("sess_dedup", 1, arb1, now=now)
    assert rec1 is not None

    # Segon afegit idèntic (deduplicat)
    rec2 = store.add_opportunity("sess_dedup", 2, arb1, now=now)
    assert rec1.snapshot_id == rec2.snapshot_id
    assert len(store._snapshots) == 1

    # Canvi de quota: Bet365 passa de 2.15 a 2.20
    t2 = now - timedelta(seconds=20)
    arb2 = {
        "match_id": "M_DEDUP",
        "matchup": "Girona vs Espanyol",
        "market_name": "1X vs 2",
        "profit_pct": 2.4,
        "legs": [
            {"bookmaker": "Winamax", "selection": "1X", "odd": 1.95, "captured_at": t1.isoformat()},
            {"bookmaker": "Bet365", "selection": "2", "odd": 2.20, "captured_at": t2.isoformat()}
        ]
    }
    rec3 = store.add_opportunity("sess_dedup", 3, arb2, now=now)
    assert rec3.snapshot_id != rec1.snapshot_id
    assert len(store._snapshots) == 2  # Ambdós snapshots conservats a l'historial


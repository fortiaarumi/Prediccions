"""
tests/test_arbitrage_30min_policy.py
====================================
Suite exhaustiva de proves unitàries i de regressió per a la política de 30 minuts
i la classificació visual en 4 nivells de frescor de Surebets:

Nivells d'oportunitat:
1. VERIFIED_FRESH (<= 60.0s, skew <= 30.0s, profit net > 0)
2. POTENTIALLY_STALE (60.0s < age_max <= 300.0s O skew > 30.0s, age_max <= 300.0s)
3. RECENT_HISTORICAL (300.0s < age_max <= 1800.0s)
4. EXCLUDED (> 1800.0s, timestamps invàlids/futurs o partit en joc)

També valida:
- Immutabilitat dels timestamps (prohibició estricta de rejoveniment en re-exportacions).
- Separació estricta entre Surebets i Near-Arbs.
- Màquina d'estats de cancel·lació cooperativa al daemon:
  PENDING -> RUNNING -> CANCEL_REQUESTED -> CANCELLED (mai COMPLETED si cancel·lat).
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
import pytest

from engine.arbitrage_engine import ArbitrageEngine
from surebet_daemon import SurebetDaemon


@pytest.fixture
def ref_now() -> datetime:
    return datetime(2026, 11, 7, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def future_match_info(ref_now: datetime) -> Dict[str, Any]:
    return {
        "matchup": "Real Madrid vs FC Barcelona",
        "date": (ref_now + timedelta(hours=3)).isoformat(),
        "date_time": (ref_now + timedelta(hours=3)).isoformat(),
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports"
    }


@pytest.fixture
def arb_engine() -> ArbitrageEngine:
    return ArbitrageEngine(
        min_profit_threshold=0.0,
        near_arb_threshold=-2.5,
        max_odds_age_seconds=60.0,
        max_skew_seconds=30.0,
        enforce_strict_freshness=False
    )


# =============================================================================
# 1. PROVES DE CLASSIFICACIÓ TEMPORAL I CASOS LÍMIT EXACTES
# =============================================================================

def test_tier_verified_fresh_at_45s_synchronized(arb_engine, ref_now, future_match_info):
    """45s d'edat i 10s de skew -> VERIFIED_FRESH."""
    t1 = ref_now - timedelta(seconds=45)
    t2 = ref_now - timedelta(seconds=35)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.15, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.05, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_VERIFIED_FRESH
    assert res["is_valid"] is True
    assert res["is_verified_fresh"] is True
    assert res["age_max"] == 45.0
    assert res["skew_seconds"] == 10.0


def test_tier_verified_fresh_exact_60s_boundary(arb_engine, ref_now, future_match_info):
    """Límit exacte de 60.0s amb skew <= 30.0s -> VERIFIED_FRESH."""
    t1 = ref_now - timedelta(seconds=60.0)
    t2 = ref_now - timedelta(seconds=40.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_VERIFIED_FRESH
    assert res["is_verified_fresh"] is True
    assert res["age_max"] == 60.0


def test_tier_potentially_stale_at_61s(arb_engine, ref_now, future_match_info):
    """61.0s (supera 60.0s per 1s) -> POTENTIALLY_STALE."""
    t1 = ref_now - timedelta(seconds=61.0)
    t2 = ref_now - timedelta(seconds=55.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_POTENTIALLY_STALE
    assert res["is_verified_fresh"] is False
    assert res["is_valid"] is True
    assert res["age_max"] == 61.0


def test_tier_potentially_stale_when_skew_exceeds_30s(arb_engine, ref_now, future_match_info):
    """Quotes joves (40s i 5s), però amb skew de 35s (> 30s) -> POTENTIALLY_STALE."""
    t1 = ref_now - timedelta(seconds=40.0)
    t2 = ref_now - timedelta(seconds=5.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_POTENTIALLY_STALE
    assert res["is_verified_fresh"] is False
    assert res["skew_seconds"] == 35.0


def test_tier_potentially_stale_exact_300s_boundary(arb_engine, ref_now, future_match_info):
    """Límit exacte de 300.0s (5 minuts) -> POTENTIALLY_STALE."""
    t1 = ref_now - timedelta(seconds=300.0)
    t2 = ref_now - timedelta(seconds=280.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_POTENTIALLY_STALE
    assert res["age_max"] == 300.0


def test_tier_recent_historical_at_301s_and_600s(arb_engine, ref_now, future_match_info):
    """301s i 600s (10 minuts) -> RECENT_HISTORICAL."""
    for s_age in (301.0, 600.0):
        t1 = ref_now - timedelta(seconds=s_age)
        t2 = ref_now - timedelta(seconds=s_age - 10)
        legs = [
            {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
            {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
        ]
        res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
        assert res["tier"] == ArbitrageEngine.TIER_RECENT_HISTORICAL
        assert res["is_verified_fresh"] is False
        assert res["age_max"] == s_age


def test_tier_recent_historical_exact_1800s_boundary(arb_engine, ref_now, future_match_info):
    """Límit exacte de 1800.0s (30 minuts) -> RECENT_HISTORICAL."""
    t1 = ref_now - timedelta(seconds=1800.0)
    t2 = ref_now - timedelta(seconds=1790.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_RECENT_HISTORICAL
    assert res["age_max"] == 1800.0


def test_tier_excluded_at_1801s(arb_engine, ref_now, future_match_info):
    """1801.0s (30m 1s) supera el sostre màxim -> EXCLUDED."""
    t1 = ref_now - timedelta(seconds=1801.0)
    t2 = ref_now - timedelta(seconds=1790.0)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t1.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t2.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_EXCLUDED
    assert res["is_valid"] is False
    assert "exceeds_max_age_1800s" in res["reason"]

    # També comprova que calculate_2way_arbitrage el descarta completament
    arb = arb_engine.calculate_2way_arbitrage(legs[0], legs[1], "1X2", future_match_info, now=ref_now)
    assert arb is None


# =============================================================================
# 2. PROVES DE VALIDACIÓ DE SEGURETAT DE TIMESTAMPS
# =============================================================================

def test_missing_or_null_timestamp_is_excluded(arb_engine, ref_now, future_match_info):
    """Timestamp absent o buit -> EXCLUDED."""
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": None},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": ref_now.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_EXCLUDED
    assert res["is_valid"] is False
    assert "missing_timestamp" in res["reason"]


def test_future_timestamp_rejected_as_excluded(arb_engine, ref_now, future_match_info):
    """Timestamp en el futur (> now + 5s) és rebutjat terminantment -> EXCLUDED."""
    t_future = ref_now + timedelta(seconds=15)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t_future.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": ref_now.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=future_match_info, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_EXCLUDED
    assert res["is_valid"] is False
    assert "future_timestamp" in res["reason"]


def test_match_already_started_is_excluded(arb_engine, ref_now):
    """Partit que ja ha començat (hora en el passat) queda descartat -> EXCLUDED."""
    past_match = {
        "matchup": "Sevilla vs Betis",
        "date_time": (ref_now - timedelta(minutes=15)).isoformat(),
        "competition_id": "LALIGA"
    }
    t = ref_now - timedelta(seconds=20)
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10, "captured_at": t.isoformat()},
        {"bookmaker": "Bet365", "selection": "X2", "odd": 2.10, "captured_at": t.isoformat()}
    ]
    res = arb_engine.evaluate_freshness_tier(legs, match_info=past_match, now=ref_now)
    assert res["tier"] == ArbitrageEngine.TIER_EXCLUDED
    assert res["is_valid"] is False
    assert res["reason"] == "match_already_started"


# =============================================================================
# 3. PROVES DE CÀLCUL MATEMÀTIC, COMISSIONS I NEAR-ARBS
# =============================================================================

def test_exchange_commission_reduces_net_profit(arb_engine, ref_now, future_match_info):
    """Verifica que la comissió a l'exchange es dedueix estrictament del PnL net."""
    t = ref_now - timedelta(seconds=25)
    # Winamax (casa tradicional 0%) vs Betfair Exchange (2% comissió)
    leg1 = {"bookmaker": "Winamax", "selection": "Més de 2.5", "odd": 2.05, "captured_at": t.isoformat()}
    leg2 = {
        "bookmaker": "Betfair",
        "selection": "Menys de 2.5",
        "odd": 2.05,
        "captured_at": t.isoformat(),
        "commission_rate": 2.0,
        "commission_model": "NET_MARKET_WINNINGS"
    }
    arb = arb_engine.calculate_2way_arbitrage(leg1, leg2, "Over/Under 2.5", future_match_info, now=ref_now, bankroll=100.0)
    assert arb is not None
    assert arb["is_surebet"] is True
    assert arb["freshness_tier"] == ArbitrageEngine.TIER_VERIFIED_FRESH
    assert arb["net_guaranteed_profit"] > 0.0
    # El retorn brut sense comissió hauria estat 102.50€ (+2.50€)
    # Amb comissió, el net és inferior a 2.50€
    assert arb["net_guaranteed_profit"] < 2.50


def test_near_arbs_strictly_separated_from_surebets(arb_engine, ref_now, future_match_info):
    """Quotes sense benefici net garantit (marge entre -2.5% i 0%) es classifiquen com a NEAR_ARB."""
    t = ref_now - timedelta(seconds=20)
    # Cuotes 1.95 i 1.95 -> Invers = 1/1.95 + 1/1.95 = 1.0256 (marge 102.6%, profit net ~ -2.5%)
    leg1 = {"bookmaker": "Winamax", "selection": "1X", "odd": 1.96, "captured_at": t.isoformat()}
    leg2 = {"bookmaker": "Bet365", "selection": "2", "odd": 1.98, "captured_at": t.isoformat()}
    arb = arb_engine.calculate_2way_arbitrage(leg1, leg2, "Doble Oportunitat", future_match_info, now=ref_now, bankroll=100.0)
    assert arb is not None
    assert arb["is_surebet"] is False
    assert arb["status"] == "NEAR_ARB"
    assert arb["net_guaranteed_profit"] <= 0.0


# =============================================================================
# 4. PROVES D'IMMUTABILITAT I RE-EXPORTACIÓ
# =============================================================================

def test_timestamp_immutability_upon_scan_all_matches(arb_engine, ref_now):
    """
    Verifica que scan_all_matches conserva intactes les dates originals captured_at
    i mai no les substitueix per 'now' o per l'hora d'execució.
    """
    original_cap_1 = (ref_now - timedelta(seconds=120)).isoformat()
    original_cap_2 = (ref_now - timedelta(seconds=110)).isoformat()

    match_fixture = {
        "id": "M_TEST_IMMUTABILITY",
        "competition_id": "LALIGA",
        "competition_name": "LaLiga",
        "date": (ref_now + timedelta(hours=5)).isoformat(),
        "date_time": (ref_now + timedelta(hours=5)).isoformat(),
        "status": "SCHEDULED",
        "home_team": {"name": "Getafe"},
        "away_team": {"name": "Celta"},
        "odds_1x2_winamax": {"1": 2.10, "X": 3.40, "2": 3.80},
        "odds_winamax_captured_at": original_cap_1,
        "odds_1x2_bet365": {"1": 1.90, "X": 3.40, "2": 4.50},
        "odds_bet365_captured_at": original_cap_2,
        "odds_dc_winamax": {"1X": 1.95, "X2": 1.80, "12": 1.35},
        "odds_dc_bet365": {"1X": 1.90, "X2": 2.10, "12": 1.30}
    }

    report = arb_engine.scan_all_matches([match_fixture], now=ref_now)
    all_opps = report["all_opportunities"]
    assert len(all_opps) > 0

    for opp in all_opps:
        for leg in opp["legs"]:
            # El timestamp conservat ha de coincidir exactament amb l'original
            assert leg["captured_at"] in (original_cap_1, original_cap_2)
            assert leg["captured_at"] != ref_now.isoformat()


# =============================================================================
# 5. PROVES DE LA MÀQUINA D'ESTATS DE CANCEL·LACIÓ AL DAEMON
# =============================================================================

def test_daemon_cancellation_state_machine():
    """
    Verifica la seqüència estricta d'estats:
    PENDING -> RUNNING -> CANCEL_REQUESTED -> CANCELLED.
    Comprova que una sessió cancel·lada MAI no acaba en COMPLETED.
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    assert daemon.session_status == "PENDING"
    assert daemon.cancel_requested is False

    # Iniciar sessió
    daemon.start_session("sess_123", max_duration_seconds=600)
    assert daemon.session_status == "RUNNING"
    assert daemon.session_id == "sess_123"
    assert daemon.cancel_requested is False

    # Sol·licitar cancel·lació
    daemon.request_cancellation()
    assert daemon.cancel_requested is True
    assert daemon.session_status == "CANCEL_REQUESTED"

    # La comprovació cooperativa fa la transició a CANCELLED
    was_cancelled = daemon.check_cancellation()
    assert was_cancelled is True
    assert daemon.session_status == "CANCELLED"

    # Assegurar que el límit de duració no el canvia a COMPLETED si ja estava cancel·lada
    daemon.check_duration_limit()
    assert daemon.session_status == "CANCELLED"
    assert daemon.session_status != "COMPLETED"


def test_daemon_natural_completion_without_cancellation():
    """
    Verifica que sense petició de cancel·lació, en complir la durada màxima
    la sessió passa a COMPLETED.
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    daemon.start_session("sess_nat", max_duration_seconds=1)
    assert daemon.session_status == "RUNNING"

    # Simular pas del temps
    daemon.session_start_time = daemon.session_start_time - 2.0
    completed = daemon.check_duration_limit()
    assert completed is True
    assert daemon.session_status == "COMPLETED"
    assert daemon.cancel_requested is False


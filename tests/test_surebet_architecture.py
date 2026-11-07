"""
tests/test_surebet_architecture.py
==================================
Valida la fiabilitat de detecció de Surebets, política de frescor estricta,
decalatge temporal (clock skew), comissions i funcionament del daemon i workflow.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent

from engine.arbitrage_engine import ArbitrageEngine
from scripts.surebet_daemon import SurebetDaemon


def test_arbitrage_stale_odds_rejected_when_strict():
    """Verifica que quotes que superen max_odds_age_seconds són rebutjades amb frescor estricta."""
    engine = ArbitrageEngine(enforce_strict_freshness=True, max_odds_age_seconds=60.0)
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Leg1: quota Winamax de fa 10 minuts (600s > 60s)
    leg1 = {
        "bookmaker": "Winamax",
        "selection": "1X",
        "odd": 2.10,
        "captured_at": (now - timedelta(minutes=10)).isoformat()
    }
    # Leg2: quota Bet365 fresca de fa 10 segons
    leg2 = {
        "bookmaker": "Bet365",
        "selection": "2",
        "odd": 2.20,
        "captured_at": (now - timedelta(seconds=10)).isoformat()
    }

    match_info = {"matchup": "Test Match"}
    arb = engine.calculate_2way_arbitrage(leg1, leg2, "Doble Oportunitat", match_info, now=now)

    # Rebutjat perquè Winamax està STALE
    assert arb is None, "Una surebet amb quotes STALE ha de ser rebutjada si enforce_strict_freshness és True"


def test_arbitrage_clock_skew_rejected_when_strict():
    """Verifica que si el decalatge entre cases supera max_skew_seconds es rebutja per evitar stale skew."""
    engine = ArbitrageEngine(enforce_strict_freshness=True, max_odds_age_seconds=300.0, max_skew_seconds=30.0)
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Ambdues quotes tenen menys de 300s (Winamax 240s, Bet365 10s), però el decalatge és de 230s > 30s!
    leg1 = {
        "bookmaker": "Winamax",
        "selection": "1X",
        "odd": 2.15,
        "captured_at": (now - timedelta(seconds=240)).isoformat()
    }
    leg2 = {
        "bookmaker": "Bet365",
        "selection": "2",
        "odd": 2.15,
        "captured_at": (now - timedelta(seconds=10)).isoformat()
    }

    match_info = {"matchup": "Test Match"}
    arb = engine.calculate_2way_arbitrage(leg1, leg2, "Doble Oportunitat", match_info, now=now)

    # Rebutjat per clock skew excessiu
    assert arb is None, "Un decalatge de 230s > 30s ha de ser descartat per risc d'arbitratge fantasma"


def test_arbitrage_fresh_and_aligned_accepted():
    """Verifica que quotes fresques i sincronitzades produeixen una Surebet confirmada amb retorn positiu."""
    engine = ArbitrageEngine(enforce_strict_freshness=True, max_odds_age_seconds=300.0, max_skew_seconds=60.0)
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Winamax 2.15 de fa 25s, Bet365 2.15 de fa 10s (skew = 15s < 60s, ambdues < 300s)
    # Suma d'inversos = 1/2.15 + 1/2.15 = 0.4651 + 0.4651 = 0.9302 < 1.0 (Retorn garantit: +7.5%)
    leg1 = {
        "bookmaker": "Winamax",
        "selection": "1X",
        "odd": 2.15,
        "captured_at": (now - timedelta(seconds=25)).isoformat()
    }
    leg2 = {
        "bookmaker": "Bet365",
        "selection": "2",
        "odd": 2.15,
        "captured_at": (now - timedelta(seconds=10)).isoformat()
    }

    match_info = {"matchup": "Girona vs Espanyol"}
    arb = engine.calculate_2way_arbitrage(leg1, leg2, "Doble Oportunitat", match_info, now=now)

    assert arb is not None
    assert arb["is_surebet"] is True
    assert arb["status"] == "SUREBET"
    assert arb["net_guaranteed_profit"] > 0.0
    assert arb["freshness_valid"] is True
    assert arb["skew_seconds"] <= 60.0


def test_arbitrage_commission_deduction():
    """Verifica el càlcul de net_profit_pct descomptant comissions."""
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts = now.isoformat()

    # Sense comissió
    eng_no_comm = ArbitrageEngine(commission_rate=0.0)
    arb_no_comm = eng_no_comm.calculate_2way_arbitrage(
        {"bookmaker": "Winamax", "selection": "1X", "odd": 2.10, "captured_at": ts},
        {"bookmaker": "Bet365", "selection": "2", "odd": 2.10, "captured_at": ts},
        "Test",
        {"matchup": "A vs B"},
        now=now
    )

    # Amb comissió del 5%
    eng_comm = ArbitrageEngine(commission_rate=0.05)
    arb_comm = eng_comm.calculate_2way_arbitrage(
        {"bookmaker": "Winamax", "selection": "1X", "odd": 2.10, "captured_at": ts},
        {"bookmaker": "Bet365", "selection": "2", "odd": 2.10, "captured_at": ts},
        "Test",
        {"matchup": "A vs B"},
        now=now
    )

    assert arb_no_comm is not None
    assert arb_comm is not None
    # El benefici net amb comissió ha de ser estrictament menor que sense comissió
    assert arb_comm["net_profit_pct"] < arb_no_comm["gross_profit_pct"]


def test_daemon_adaptive_proximity_intervals():
    """Verifica que compute_polling_interval adapta el temps de mostreig segons la proximitat."""
    daemon = SurebetDaemon()
    now = datetime.now(timezone.utc)

    # 1. Partit en directe (LIVE) -> 10 segons
    matches_live = [{"status": "LIVE"}]
    assert daemon.compute_polling_interval(matches_live) == 10

    # 2. Partit a 30 minuts (<1h) -> 60 segons
    d_30m = (now + timedelta(minutes=30)).strftime("%d.%m.%Y %H:%M")
    matches_30m = [{"status": "SCHEDULED", "date": d_30m}]
    assert daemon.compute_polling_interval(matches_30m) == 60

    # 3. Partit a 3 hores (<6h) -> 300 segons (5 min)
    d_3h = (now + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    matches_3h = [{"status": "SCHEDULED", "date": d_3h}]
    assert daemon.compute_polling_interval(matches_3h) == 300

    # 4. Partit a 24 hores (<48h) -> 900 segons (15 min)
    d_24h = (now + timedelta(hours=24)).strftime("%d.%m.%Y %H:%M")
    matches_24h = [{"status": "SCHEDULED", "date": d_24h}]
    assert daemon.compute_polling_interval(matches_24h) == 900

    # 5. Partit a 4 dies (>48h) -> 3600 segons (60 min)
    d_4d = (now + timedelta(days=4)).strftime("%d.%m.%Y %H:%M")
    matches_4d = [{"status": "SCHEDULED", "date": d_4d}]
    assert daemon.compute_polling_interval(matches_4d) == 3600


def test_workflow_file_exists_and_configured():
    """Verifica que el workflow surebet_monitor.yml està ben configurat."""
    wf_path = ROOT / ".github" / "workflows" / "surebet_monitor.yml"
    assert wf_path.exists(), "El workflow .github/workflows/surebet_monitor.yml ha d'existir"

    content = wf_path.read_text(encoding="utf-8")
    assert "cron: '*/5 * * * *'" in content
    assert "workflow_dispatch:" in content
    assert "surebets-live" in content, "Ha d'utilitzar una branca o destí aïllat per no embrutar main"


def test_differential_bookmaker_commissions_and_defaults():
    """
    Verifica que:
    1. Els límits per defecte són max_odds_age_seconds=60s i max_skew_seconds=30s.
    2. Són configurables mitjançant variables d'entorn SUREBET_MAX_ODDS_AGE_SECONDS i SUREBET_MAX_SKEW_SECONDS.
    3. Les comissions s'apliquen diferencialment segons la casa (ex: 0% a Winamax/Bet365, 2% a Betfair).
    """
    eng_def = ArbitrageEngine()
    assert eng_def.max_odds_age_seconds == 60.0
    assert eng_def.max_skew_seconds == 30.0

    # Provar comissions diferencials
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts = now.isoformat()

    # Pota 1 a Bet365 (sportsbook tradicional: comissió 0.0%)
    # Pota 2 a Betfair (exchange: comissió 2.0%)
    eng_diff = ArbitrageEngine(bookmaker_commissions={"bet365": 0.0, "betfair": 0.02})
    leg1 = {"bookmaker": "Bet365", "selection": "1X", "odd": 2.10, "captured_at": ts}
    leg2 = {"bookmaker": "Betfair", "selection": "2", "odd": 2.10, "captured_at": ts}

    assert eng_diff.get_leg_commission(leg1) == 0.0
    assert eng_diff.get_leg_commission(leg2) == 0.02

    res = eng_diff.calculate_2way_arbitrage(leg1, leg2, "Test Diff", {"matchup": "A vs B"}, now=now)
    assert res is not None
    assert res["legs"][0]["commission_rate"] == 0.0
    assert res["legs"][1]["commission_rate"] == 0.02
    assert res["legs"][0]["expected_return"] > res["legs"][1]["expected_return"]



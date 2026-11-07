"""
tests/test_three_bookmaker_arbitrage.py
=======================================
Tests matemàtics i d'integració per a surebets combinant 2 i 3 cases d'apostes reals:
- Winamax + Bet365
- Winamax + Betway
- Bet365 + Betway
- Winamax + Bet365 + Betway (1X2 tripartit complet)

Verifica:
1. Detecció de surebets bipartites (Winamax + Betway, Bet365 + Betway).
2. Detecció de surebets tripartites (1 a Winamax, X a Bet365, 2 a Betway).
3. Optimitzador d'apostes enteres per a 3 cases (stakes enters sumen bankroll, retorn garantit > 0 en tots 3 escenaris).
4. Política de frescor estricta (enforce_strict_freshness=True):
   - Quotes dins de llindars (edat <= 60s, decalatge <= 30s) -> Acceptades com a fresques.
   - Quotes obsoletes (> 60s) o amb gran decalatge (> 30s) -> Descartades o marcades stale.
5. Format d'instruccions d'execució pas a pas per a 3 cases.
"""

import pytest
from datetime import datetime, timezone, timedelta

from engine.arbitrage_engine import (
    ArbitrageEngine,
    normalize_bankroll,
    round_commercial
)


@pytest.fixture
def engine_strict():
    return ArbitrageEngine(
        min_profit_threshold=0.0,
        max_odds_age_seconds=60.0,
        max_skew_seconds=30.0,
        enforce_strict_freshness=True
    )


@pytest.fixture
def engine_permissive():
    return ArbitrageEngine(
        min_profit_threshold=-2.5,
        max_odds_age_seconds=3600.0,
        max_skew_seconds=600.0,
        enforce_strict_freshness=False
    )


# -----------------------------------------------------------------------------
# 1. Surebets Bipartites amb Betway
# -----------------------------------------------------------------------------
def test_bipartite_arbitrage_winamax_betway_ou25(engine_strict):
    """Over 2.5 a Winamax @ 2.15, Under 2.5 a Betway @ 2.15 -> Surebet ~7.5%."""
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()

    match_dict = {
        "id": 1001,
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "date": "2026-11-03",
        "home_team": {"name": "Getafe CF"},
        "away_team": {"name": "Girona FC"},
        "status": "SCHEDULED",
        "odds_ou25_winamax": {"over": 2.15, "under": 1.70},
        "odds_winamax_captured_at": now_iso,
        "url_winamax": "https://www.winamax.es/paris-sportifs/match/1001",
        "odds_ou25_betway": {"over": 1.72, "under": 2.15},
        "odds_betway_captured_at": now_iso,
        "url_betway": "https://betway.es/es/sports/evt/5001"
    }

    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    ou_arbs = [o for o in opportunities if "2.5" in o["market_name"]]

    assert len(ou_arbs) >= 1
    arb = ou_arbs[0]
    assert arb["margin_pct"] < 100.0
    assert arb["profit_pct"] > 0.0
    assert len(arb["legs"]) == 2

    bookies = {l["bookmaker"].upper() for l in arb["legs"]}
    assert bookies == {"WINAMAX", "BETWAY"}


def test_bipartite_arbitrage_bet365_betway_btts(engine_strict):
    """BTTS Sí a Bet365 @ 2.10, BTTS No a Betway @ 2.12 -> Surebet ~5.49%."""
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()

    match_dict = {
        "id": 1002,
        "competition_id": "PREMIER",
        "competition_name": "Premier League",
        "date": "2026-11-03",
        "home_team": {"name": "Arsenal"},
        "away_team": {"name": "Chelsea"},
        "status": "SCHEDULED",
        "odds_btts_bet365": {"yes": 2.10, "no": 1.75},
        "odds_bet365_captured_at": now_iso,
        "url_bet365": "https://www.bet365.es/#/AC/B1/C1/D13/E12345/F2/",
        "odds_btts_betway": {"yes": 1.75, "no": 2.12},
        "odds_betway_captured_at": now_iso,
        "url_betway": "https://betway.es/es/sports/evt/6002"
    }

    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    btts_arbs = [o for o in opportunities if "BTTS" in o["market_name"] or "Ambdós" in o["market_name"]]

    assert len(btts_arbs) >= 1
    arb = btts_arbs[0]
    assert arb["profit_pct"] > 0.0
    bookies = {l["bookmaker"].upper() for l in arb["legs"]}
    assert bookies == {"BET365", "BETWAY"}


# -----------------------------------------------------------------------------
# 2. Surebet Tripartita (Winamax + Bet365 + Betway creuant 1X2)
# -----------------------------------------------------------------------------
def test_tripartite_1x2_surebet_cross_three_bookmakers(engine_strict):
    """
    Winamax ofereix 1 @ 3.40, Bet365 ofereix X @ 3.35, Betway ofereix 2 @ 3.30.
    Marge = 1/3.40 + 1/3.35 + 1/3.30 = 0.2941 + 0.2985 + 0.3030 = 0.8956 < 1.0.
    Benefici teòric: +11.65% (dins del límit d'error palpable de <=15%).
    """
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()

    match_dict = {
        "id": 1003,
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "date": "2026-11-04",
        "home_team": {"name": "Sevilla FC"},
        "away_team": {"name": "Real Betis"},
        "status": "SCHEDULED",
        # Winamax excel·leix en la victòria local
        "odds_1x2_winamax": {"1": 3.40, "X": 3.00, "2": 2.30},
        "odds_winamax_captured_at": now_iso,
        "url_winamax": "https://www.winamax.es/paris-sportifs/match/1003",
        # Bet365 excel·leix en l'empat
        "odds_1x2_bet365": {"1": 2.40, "X": 3.35, "2": 3.00},
        "odds_bet365_captured_at": now_iso,
        "url_bet365": "https://www.bet365.es/#/AC/B1/C1/D13/E1003/F2/",
        # Betway excel·leix en la victòria visitant
        "odds_1x2_betway": {"1": 2.35, "X": 3.10, "2": 3.30},
        "odds_betway_captured_at": now_iso,
        "url_betway": "https://betway.es/es/sports/evt/7003"
    }

    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    arbs_1x2 = [o for o in opportunities if "1X2" in o["market_name"]]

    assert len(arbs_1x2) >= 1
    arb = arbs_1x2[0]

    # Verificar que les 3 potes provenen cadascuna d'una casa diferent
    legs = arb["legs"]
    assert len(legs) == 3
    leg_bookies = [l["bookmaker"].upper() for l in legs]
    assert "WINAMAX" in leg_bookies
    assert "BET365" in leg_bookies
    assert "BETWAY" in leg_bookies

    # Comprovar correspondència de seleccions
    leg_by_sel = {l["selection"][0]: l for l in legs}
    assert leg_by_sel["1"]["bookmaker"].upper() == "WINAMAX"
    assert leg_by_sel["1"]["odd"] == 3.40
    assert leg_by_sel["X"]["bookmaker"].upper() == "BET365"
    assert leg_by_sel["X"]["odd"] == 3.35
    assert leg_by_sel["2"]["bookmaker"].upper() == "BETWAY"
    assert leg_by_sel["2"]["odd"] == 3.30

    assert arb["margin_pct"] < 90.0
    assert arb["profit_pct"] > 10.0


# -----------------------------------------------------------------------------
# 3. Optimitzador d'Apostes Enteres per a 3 Cases
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("bankroll", [50, 75, 100, 150, 200])
def test_three_bookmaker_integer_stakes_optimization(engine_strict, bankroll):
    """
    Verifica que l'optimitzador enter reparteix el capital de manera exhaustiva
    garantint que:
    1. La suma dels 3 stakes és exactament igual al bankroll total.
    2. El benefici net és estrictament positiu en tots 3 escenaris (1 guanya, X guanya, 2 guanya).
    3. No hi ha pèrdues en cap combinació.
    """
    legs = [
        {"selection": "1", "odd": 3.40, "bookmaker": "WINAMAX"},
        {"selection": "X", "odd": 3.35, "bookmaker": "BET365"},
        {"selection": "2", "odd": 3.30, "bookmaker": "BETWAY"},
    ]

    opt = engine_strict.optimize_stakes_for_bankroll(legs, bankroll=float(bankroll))

    # 1. Suma exacta d'apostes enteres
    stakes = opt["round_stakes"]
    assert sum(stakes) == bankroll
    assert all(isinstance(s, int) for s in stakes)
    assert all(s > 0 for s in stakes)

    # 2. Retorn i benefici garantit positiu en cadascun dels 3 escenaris
    scenario_pnls = opt["scenario_pnls"]
    assert len(scenario_pnls) == 3
    for s_idx, pnl in enumerate(scenario_pnls):
        assert pnl > 0.0, f"Escenari {s_idx} ha tingut pnl negatiu: {pnl}"

    assert opt["net_guaranteed_profit"] > 0.0
    assert opt["min_guaranteed_return"] > bankroll
    assert opt["profit_pct"] > 0.0


# -----------------------------------------------------------------------------
# 4. Validació de Política de Frescor Estricta (enforce_strict_freshness)
# -----------------------------------------------------------------------------
def test_strict_freshness_accepts_recent_odds(engine_strict):
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    # Quotes capturades fa 25 segons (dins del límit de 60s)
    t_winamax = (now_utc - timedelta(seconds=25)).isoformat()
    # Quotes capturades fa 10 segons (decalatge de 15s < 30s)
    t_betway = (now_utc - timedelta(seconds=10)).isoformat()

    match_dict = {
        "id": 1004,
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "date": "2026-11-03",
        "home_team": {"name": "Rayo Vallecano"},
        "away_team": {"name": "CA Osasuna"},
        "status": "SCHEDULED",
        "odds_ou25_winamax": {"over": 2.15, "under": 1.70},
        "odds_winamax_captured_at": t_winamax,
        "odds_ou25_betway": {"over": 1.70, "under": 2.15},
        "odds_betway_captured_at": t_betway
    }

    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    assert len(opportunities) >= 1
    assert opportunities[0]["freshness_valid"] is True


def test_strict_freshness_rejects_stale_odds(engine_strict):
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    # Winamax s'ha actualitzat fa 20 segons
    t_winamax = (now_utc - timedelta(seconds=20)).isoformat()
    # Betway té quotes de fa 12 minuts (720 segons > 60s màxim)
    t_betway_stale = (now_utc - timedelta(minutes=12)).isoformat()

    match_dict = {
        "id": 1005,
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "date": "2026-11-03",
        "home_team": {"name": "Rayo Vallecano"},
        "away_team": {"name": "CA Osasuna"},
        "status": "SCHEDULED",
        "odds_ou25_winamax": {"over": 2.15, "under": 1.70},
        "odds_winamax_captured_at": t_winamax,
        "odds_ou25_betway": {"over": 1.70, "under": 2.15},
        "odds_betway_captured_at": t_betway_stale
    }

    # Amb motor estricte, l'oportunitat amb quotes de fa 12 minuts NO s'ha d'acceptar
    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    assert len(opportunities) == 0


def test_strict_freshness_rejects_large_skew(engine_strict):
    now_utc = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    # Winamax s'ha actualitzat fa 5 segons
    t_winamax = (now_utc - timedelta(seconds=5)).isoformat()
    # Betway s'ha actualitzat fa 50 segons (edat < 60s, però decalatge = 45s > 30s límit)
    t_betway_skew = (now_utc - timedelta(seconds=50)).isoformat()

    match_dict = {
        "id": 1006,
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "date": "2026-11-03",
        "home_team": {"name": "Rayo Vallecano"},
        "away_team": {"name": "CA Osasuna"},
        "status": "SCHEDULED",
        "odds_ou25_winamax": {"over": 2.15, "under": 1.70},
        "odds_winamax_captured_at": t_winamax,
        "odds_ou25_betway": {"over": 1.70, "under": 2.15},
        "odds_betway_captured_at": t_betway_skew
    }

    opportunities = engine_strict.scan_match(match_dict, now=now_utc)
    # Decalatge de 45s supera el màxim permès de 30s -> Rebutjat
    assert len(opportunities) == 0

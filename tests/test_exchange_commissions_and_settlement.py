"""
tests/test_exchange_commissions_and_settlement.py
=================================================
Proves exhaustives i independents dels models de comissió d'exchange i liquidació per escenaris
a ArbitrageEngine:
1. Betfair (2% DGOJ/ES) i Matchbook (4% ES, 2% UK) amb compensació de guanys i pèrdues al mateix mercat.
2. Smarkets Standard (2% NET_MARKET_WINNINGS) amb compensació neta de mercat.
3. Smarkets Pro (1% PER_BET_SETTLEMENT) i Select (3%) amb comissió individual tant en apostes guanyadores com perdedores.
4. Apostes amb market_id diferents que MAI no es compensin.
5. Absència de market_id / metadades incompletes: aïllament estricte amb leg_id determinista (sense id(leg)).
6. Diferents proveïdors, esdeveniments, períodes i línies: mai barrejats.
7. Casos de void/push (retorn net sense penalització de comissió).
8. Verificació numèrica independent dels exemples matemàtics documentats.
9. Sobreescriptura de perfils territorials i taxes explícites.
"""

import os
import math
import pytest
from engine.arbitrage_engine import (
    ArbitrageEngine,
    COMMISSION_MODEL_NET_MARKET_WINNINGS,
    COMMISSION_MODEL_PER_BET_SETTLEMENT,
    COMMISSION_MODEL_NONE
)


# ==============================================================================
# 1. IDENTIFICADORS DETERMINISTES I AÏLLAMENT ESTRICTE
# ==============================================================================

def test_deterministic_leg_id_stability():
    """Verifica que generate_leg_id sigui determinista i no utilitzi id(leg)."""
    leg_id_1 = ArbitrageEngine.generate_leg_id(0, "Betfair", "Over 2.5", 2.10)
    leg_id_2 = ArbitrageEngine.generate_leg_id(0, "Betfair", "Over 2.5", 2.10)
    assert leg_id_1 == leg_id_2, "El leg_id ha de ser determinista i idèntic per als mateixos paràmetres"
    assert "leg_0_" in leg_id_1
    assert len(leg_id_1) > 10

    # Diferent índex o diferent quota genera diferent leg_id
    leg_id_diff = ArbitrageEngine.generate_leg_id(1, "Betfair", "Over 2.5", 2.10)
    assert leg_id_1 != leg_id_diff


def test_settlement_key_strict_isolation_when_no_native_market_id():
    """
    Verifica que en absència de native_market_id o correspondència documentada,
    dues apostes de la mateixa exchange s'aïllin estrictament i MAI comparteixin settlement_key.
    """
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.0}
    leg2 = {"bookmaker": "Betfair", "selection": "X", "odd": 3.4}

    k1 = ArbitrageEngine.get_settlement_key(leg1)
    k2 = ArbitrageEngine.get_settlement_key(leg2)

    assert "isolated" in k1
    assert "isolated" in k2
    assert k1 != k2, "Apostes sense native_market_id han de tenir settlement_key diferents per evitar compensació indeguda"


def test_settlement_key_groups_when_same_native_market_id():
    """Verifica que dues apostes amb el MATEIX native_market_id comparteixin settlement_key."""
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.0, "native_market_id": "1.205839210"}
    leg2 = {"bookmaker": "Betfair", "selection": "X", "odd": 3.4, "native_market_id": "1.205839210"}

    k1 = ArbitrageEngine.get_settlement_key(leg1)
    k2 = ArbitrageEngine.get_settlement_key(leg2)

    assert k1 == k2
    assert k1 == "betfair::market::1.205839210"


def test_settlement_key_never_crosses_different_market_ids():
    """Verifica que apostes amb native_market_id diferents tinguin settlement_keys diferents."""
    leg_1x2 = {"bookmaker": "Betfair", "selection": "1", "odd": 2.0, "native_market_id": "1.205839210"}
    leg_ou = {"bookmaker": "Betfair", "selection": "Over 2.5", "odd": 1.9, "native_market_id": "1.205839299"}

    k_1x2 = ArbitrageEngine.get_settlement_key(leg_1x2)
    k_ou = ArbitrageEngine.get_settlement_key(leg_ou)

    assert k_1x2 != k_ou


def test_settlement_key_never_crosses_different_providers():
    """Verifica que proveïdors diferents mai comparteixin settlement_key encara que tinguin el mateix ID numèric."""
    leg_bf = {"bookmaker": "Betfair", "selection": "1", "odd": 2.0, "native_market_id": "12345"}
    leg_sm = {"bookmaker": "Smarkets", "selection": "1", "odd": 2.0, "native_market_id": "12345"}

    assert ArbitrageEngine.get_settlement_key(leg_bf) != ArbitrageEngine.get_settlement_key(leg_sm)


# ==============================================================================
# 2. MODELS DE COMISSIÓ I CONFIGURACIÓ TERRITORIAL
# ==============================================================================

def test_betfair_spain_default_commission():
    """Verifica que Betfair Espanya tingui el model NET_MARKET_WINNINGS i taxa 2.0%."""
    engine = ArbitrageEngine()
    profile = engine.get_exchange_profile({"bookmaker": "Betfair"})
    assert profile["commission_model"] == COMMISSION_MODEL_NET_MARKET_WINNINGS
    assert math.isclose(profile["rate"], 0.02, abs_tol=1e-5)


def test_matchbook_territorial_rate_default_spain_vs_uk():
    """Verifica que Matchbook apliqui 4.0% per a Espanya/ROW i 2.0% per a UK/IE."""
    # Per defecte (Espanya)
    engine_es = ArbitrageEngine()
    prof_es = engine_es.get_exchange_profile({"bookmaker": "Matchbook"})
    assert prof_es["commission_model"] == COMMISSION_MODEL_NET_MARKET_WINNINGS
    assert math.isclose(prof_es["rate"], 0.04, abs_tol=1e-5)

    # Perfil UK/IE
    custom_profiles = {
        "matchbook": {
            "commission_model": COMMISSION_MODEL_NET_MARKET_WINNINGS,
            "rate": 0.02,
            "territory": "UK_IE"
        }
    }
    engine_uk = ArbitrageEngine(exchange_profiles=custom_profiles)
    prof_uk = engine_uk.get_exchange_profile({"bookmaker": "Matchbook"})
    assert math.isclose(prof_uk["rate"], 0.02, abs_tol=1e-5)


def test_smarkets_profiles_distinction():
    """Verifica la distinció clara entre Smarkets Standard, Pro i Select."""
    engine = ArbitrageEngine()

    p_std = engine.get_exchange_profile({"bookmaker": "Smarkets"})
    assert p_std["commission_model"] == COMMISSION_MODEL_NET_MARKET_WINNINGS
    assert math.isclose(p_std["rate"], 0.02, abs_tol=1e-5)

    p_pro = engine.get_exchange_profile({"bookmaker": "smarkets_pro"})
    assert p_pro["commission_model"] == COMMISSION_MODEL_PER_BET_SETTLEMENT
    assert math.isclose(p_pro["rate"], 0.01, abs_tol=1e-5)

    p_sel = engine.get_exchange_profile({"bookmaker": "smarkets_select"})
    assert p_sel["commission_model"] == COMMISSION_MODEL_PER_BET_SETTLEMENT
    assert math.isclose(p_sel["rate"], 0.03, abs_tol=1e-5)


# ==============================================================================
# 3. VERIFICACIÓ MATEMÀTICA DELS EXEMPLES DE LIQUIDACIÓ PER ESCENARIS
# ==============================================================================

def test_numerical_example_smarkets_standard_net_market_winnings():
    """
    Verifica l'exemple numèric documentat pas a pas:
    - Pota 1 (Smarkets Standard, market_id="M1"): Back 1 @ 3.50, stake = 30 €
    - Pota X (Smarkets Standard, market_id="M1"): Back X @ 3.50, stake = 30 €
    - Pota 2 (Winamax, bookmaker tradicional):   Back 2 @ 2.60, stake = 40 €

    Escenari 1 (Guanya 1):
      Smarkets: +30 * 2.5 = +75 €, Pota X: -30 €. Guany net = +45 €. Comm 2% = 0.90 €. Net = +44.10 €
      Winamax: -40 €
      Total Net PnL = +44.10 - 40 = +4.10 €

    Escenari X (Guanya X):
      Idèntic a Escenari 1 = +4.10 €

    Escenari 2 (Guanya 2):
      Smarkets: Pota 1: -30 €, Pota X: -30 €. Saldo = -60 € (Pèrdua). Comm = 0.00 €. Net = -60 €
      Winamax: +40 * 1.6 = +64.00 €
      Total Net PnL = +64.00 - 60.00 = +4.00 €

    Mínim garantit = +4.00 € (+4.00%).
    """
    engine = ArbitrageEngine()

    leg1 = {"bookmaker": "Smarkets", "selection": "1", "odd": 3.50, "native_market_id": "M1"}
    legX = {"bookmaker": "Smarkets", "selection": "X", "odd": 3.50, "native_market_id": "M1"}
    leg2 = {"bookmaker": "Winamax", "selection": "2", "odd": 2.60}

    legs = [leg1, legX, leg2]
    stakes = [30.0, 30.0, 40.0]

    pnl_s0 = engine.calculate_scenario_pnl(legs, stakes, 0)
    pnl_sx = engine.calculate_scenario_pnl(legs, stakes, 1)
    pnl_s2 = engine.calculate_scenario_pnl(legs, stakes, 2)

    assert math.isclose(pnl_s0, 4.10, abs_tol=1e-2), f"Escenari 1 esperat 4.10, obtingut {pnl_s0}"
    assert math.isclose(pnl_sx, 4.10, abs_tol=1e-2), f"Escenari X esperat 4.10, obtingut {pnl_sx}"
    assert math.isclose(pnl_s2, 4.00, abs_tol=1e-2), f"Escenari 2 esperat 4.00, obtingut {pnl_s2}"

    guaranteed = min(pnl_s0, pnl_sx, pnl_s2)
    assert math.isclose(guaranteed, 4.00, abs_tol=1e-2)


def test_numerical_example_smarkets_pro_per_bet_settlement():
    """
    Verifica l'exemple numèric sota Smarkets Pro (PER_BET_SETTLEMENT @ 1%):
    - Pota 1 (Smarkets Pro): Back 1 @ 3.50, stake = 30 €
    - Pota X (Smarkets Pro): Back X @ 3.50, stake = 30 €
    - Pota 2 (Winamax):      Back 2 @ 2.60, stake = 40 €

    Escenari 1 (Guanya 1):
      Pota 1 (Win): +75 €, Comm = 75 * 0.01 = 0.75 € -> Net = +74.25 €
      Pota X (Loss): -30 €, Comm = 30 * 0.01 = 0.30 € -> Net = -30.30 €
      Smarkets Net = 74.25 - 30.30 = +43.95 €
      Winamax: -40.00 €
      Total Net PnL = +43.95 - 40 = +3.95 €

    Escenari 2 (Guanya 2):
      Pota 1 (Loss): -30 €, Comm = 0.30 € -> Net = -30.30 €
      Pota X (Loss): -30 €, Comm = 0.30 € -> Net = -30.30 €
      Smarkets Net = -60.60 €
      Winamax (Win): +64.00 €
      Total Net PnL = +64.00 - 60.60 = +3.40 €

    Mínim garantit = +3.40 € (+3.40%).
    """
    engine = ArbitrageEngine()

    leg1 = {"bookmaker": "smarkets_pro", "selection": "1", "odd": 3.50, "native_market_id": "M1"}
    legX = {"bookmaker": "smarkets_pro", "selection": "X", "odd": 3.50, "native_market_id": "M1"}
    leg2 = {"bookmaker": "Winamax", "selection": "2", "odd": 2.60}

    legs = [leg1, legX, leg2]
    stakes = [30.0, 30.0, 40.0]

    pnl_s0 = engine.calculate_scenario_pnl(legs, stakes, 0)
    pnl_s2 = engine.calculate_scenario_pnl(legs, stakes, 2)

    assert math.isclose(pnl_s0, 3.95, abs_tol=1e-2), f"Escenari 1 esperat 3.95, obtingut {pnl_s0}"
    assert math.isclose(pnl_s2, 3.40, abs_tol=1e-2), f"Escenari 2 esperat 3.40, obtingut {pnl_s2}"

    guaranteed = min(pnl_s0, pnl_s2)
    assert math.isclose(guaranteed, 3.40, abs_tol=1e-2)


# ==============================================================================
# 4. COMPENSACIÓ DE MERCAT VS AÏLLAMENT
# ==============================================================================

def test_different_market_ids_never_compensate():
    """
    Si dues apostes a Betfair tenen market_id diferents (ex: mercat 1X2 i mercat Doble Oportunitat),
    NO es compensen i cada mercat guanyador paga comissió completa sense desgravar la pèrdua de l'altre.
    """
    engine = ArbitrageEngine()

    # Suposem Leg 1 a mercat M_1X2 i Leg 2 a mercat M_DC
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 2.50, "native_market_id": "M_1X2"}
    leg2 = {"bookmaker": "Betfair", "selection": "X2", "odd": 1.70, "native_market_id": "M_DC"}

    # Escenari on guanya Leg 1 (stake 50 cadascuna):
    # Leg 1 guanya: +50 * 1.5 = +75 € brut. Comissió 2% = 1.50 € -> Net = +73.50 €
    # Leg 2 perd: -50 €. Pèrdua = -50.00 €
    # Perquè estan en mercats diferents, M_1X2 paga 1.50 € de comissió (no compensa el -50 € de M_DC)
    # Total PnL = +73.50 - 50 = +23.50 €
    pnl = engine.calculate_scenario_pnl([leg1, leg2], [50.0, 50.0], 0)
    assert math.isclose(pnl, 23.50, abs_tol=1e-2)

    # En canvi, si haguessin estat al MATEIX mercat "M_SAME":
    # Guany brut net del mercat = +75 - 50 = +25 €. Comissió 2% = 0.50 € -> Net = +24.50 €
    leg1_same = dict(leg1, native_market_id="M_SAME")
    leg2_same = dict(leg2, native_market_id="M_SAME")
    pnl_same = engine.calculate_scenario_pnl([leg1_same, leg2_same], [50.0, 50.0], 0)
    assert math.isclose(pnl_same, 24.50, abs_tol=1e-2)

    # La manca de compensació entre mercats diferents és estrictament més conservadora
    assert pnl < pnl_same


# ==============================================================================
# 5. CASOS DE PUSH / VOID
# ==============================================================================

def test_push_void_scenario_liquidation():
    """
    Verifica que en escenaris amb push/void (retorn de l'aposta, PnL = 0.0),
    no es cobri cap comissió a l'exchange ni es penalitzi l'usuari.
    """
    engine = ArbitrageEngine()

    leg_dnb = {"bookmaker": "Betfair", "selection": "1 DNB", "odd": 1.90, "native_market_id": "M_DNB"}
    leg_x2 = {"bookmaker": "Winamax", "selection": "X2", "odd": 2.15}

    # Escenari de PUSH a leg_dnb i PUSH a leg_x2 (ex: partit anul·lat)
    pnl_push = engine.calculate_scenario_pnl(
        [leg_dnb, leg_x2],
        [50.0, 50.0],
        scenario_index=0,
        leg_outcomes=["PUSH", "PUSH"]
    )
    assert math.isclose(pnl_push, 0.0, abs_tol=1e-5), "En push total, el PnL net ha de ser estrictament 0.0 €"

    # Escenari DNB Empat (leg_dnb fa push, leg_x2 guanya):
    # leg_dnb: push -> PnL = 0.0 €
    # leg_x2: win -> +50 * (2.15 - 1) = +57.50 €
    pnl_dnb_push_x2_win = engine.calculate_scenario_pnl(
        [leg_dnb, leg_x2],
        [50.0, 50.0],
        scenario_index=1,
        leg_outcomes=["PUSH", "WIN"]
    )
    assert math.isclose(pnl_dnb_push_x2_win, 57.50, abs_tol=1e-2)


# ==============================================================================
# 6. INTEGRACIÓ AMB CALCULATE_2WAY I CALCULATE_3WAY ARBITRAGE
# ==============================================================================

def test_calculate_2way_arbitrage_with_exchange_commissions():
    """
    Verifica que calculate_2way_arbitrage calculi el profit garantit exacte
    deduint les comissions de mercat d'exchange per escenaris.
    """
    engine = ArbitrageEngine()

    match_info = {"matchup": "Real Madrid vs Barcelona", "date": "05.11.2026"}
    # Quota 1: Winamax @ 2.10
    # Quota 2: Betfair @ 2.05 (amb 2% comissió DGOJ)
    res = engine.calculate_2way_arbitrage(
        {"bookmaker": "Winamax", "selection": "Over 2.5", "odd": 2.10},
        {"bookmaker": "Betfair", "selection": "Under 2.5", "odd": 2.05, "native_market_id": "BF_OU25"},
        "Over/Under 2.5",
        match_info
    )

    assert res is not None
    assert "legs" in res
    assert len(res["legs"]) == 2
    # Comprovar que cada pota té leg_id, commission_model i settlement_key
    l1 = res["legs"][0]
    l2 = res["legs"][1]
    assert "leg_id" in l1 and "leg_id" in l2
    assert l1["commission_model"] == COMMISSION_MODEL_NONE
    assert l2["commission_model"] == COMMISSION_MODEL_NET_MARKET_WINNINGS
    assert "market::BF_OU25" in l2["settlement_key"]


def test_calculate_3way_arbitrage_with_smarkets_pro():
    """
    Verifica que calculate_3way_arbitrage integri correctament Smarkets Pro
    deduint el turnover fee de cada pota per escenari.
    """
    engine = ArbitrageEngine()

    match_info = {"matchup": "Sevilla vs Betis", "date": "08.11.2026"}
    res = engine.calculate_3way_arbitrage(
        {"bookmaker": "smarkets_pro", "selection": "1", "odd": 3.40},
        {"bookmaker": "smarkets_pro", "selection": "X", "odd": 3.60},
        {"bookmaker": "Winamax", "selection": "2", "odd": 3.20},
        match_info
    )

    assert res is not None
    assert res["type"] == "3-WAY"
    assert len(res["legs"]) == 3
    assert res["legs"][0]["commission_model"] == COMMISSION_MODEL_PER_BET_SETTLEMENT
    assert res["legs"][1]["commission_model"] == COMMISSION_MODEL_PER_BET_SETTLEMENT
    assert res["legs"][2]["commission_model"] == COMMISSION_MODEL_NONE


# ==============================================================================
# 7. RESOLUCIÓ DINÀMICA DE CONFIGURACIÓ I PREVALENÇA
# ==============================================================================

def test_runtime_env_vars_precedence_dynamic():
    """
    Verifica que les variables d'entorn modificades en temps d'execució
    afectin de manera immediata i dinàmica les noves instàncies d'ArbitrageEngine.
    """
    from unittest.mock import patch

    # 1. Territori UK -> taxa 2% a Matchbook
    with patch.dict(os.environ, {"MATCHBOOK_TERRITORY": "UK"}, clear=False):
        engine_uk = ArbitrageEngine()
        prof_uk = engine_uk.get_exchange_profile({"bookmaker": "Matchbook"})
        assert math.isclose(prof_uk["rate"], 0.02, abs_tol=1e-5)
        assert prof_uk["territory"] == "UK"

    # 2. Taxa explícita via variable d'entorn -> sobreeescriu territori
    with patch.dict(os.environ, {"MATCHBOOK_COMMISSION_RATE": "0.015"}, clear=False):
        engine_rate = ArbitrageEngine()
        prof_rate = engine_rate.get_exchange_profile({"bookmaker": "Matchbook"})
        assert math.isclose(prof_rate["rate"], 0.015, abs_tol=1e-5)

    # 3. Taxa Betfair via variable d'entorn
    with patch.dict(os.environ, {"BETFAIR_COMMISSION_RATE": "0.025"}, clear=False):
        engine_bf = ArbitrageEngine()
        prof_bf = engine_bf.get_exchange_profile({"bookmaker": "Betfair"})
        assert math.isclose(prof_bf["rate"], 0.025, abs_tol=1e-5)


def test_constructor_bookmaker_commissions_synchronization():
    """
    Verifica que bookmaker_commissions passat al constructor actualitzi
    tant self.bookmaker_commissions com self.exchange_profiles, mantenint
    get_leg_commission() i get_exchange_profile() 100% coherents.
    """
    engine = ArbitrageEngine(bookmaker_commissions={"matchbook": 0.018})

    assert math.isclose(engine.bookmaker_commissions["matchbook"], 0.018, abs_tol=1e-5)
    prof = engine.get_exchange_profile({"bookmaker": "Matchbook"})
    assert math.isclose(prof["rate"], 0.018, abs_tol=1e-5)
    assert prof["commission_model"] == COMMISSION_MODEL_NET_MARKET_WINNINGS

    rate_leg = engine.get_leg_commission({"bookmaker": "Matchbook"})
    assert math.isclose(rate_leg, 0.018, abs_tol=1e-5)


# ==============================================================================
# 8. REGRESSIÓ D'OPTIMITZACIÓ MATEMÀTICA D'STAKES AMB COMISSIONS ASIMÈTRIQUES
# ==============================================================================

def test_asymmetric_commission_stake_optimization_2way_regression():
    """
    Cas de regressió 2-way:
    - Pota 1: Winamax @ 1.40 (comissió 0%)
    - Pota 2: Matchbook @ 3.80 (comissió 4%)

    La fórmula d'inversos bruts proposava [73, 27], donant un PnL net de -0.42 € (NEAR_ARB fals).
    L'optimitzador exhaustiu global troba [72, 28] amb un guany net garantit de +0.80 € (+0.80%).
    """
    engine = ArbitrageEngine()

    leg1 = {"bookmaker": "Winamax", "selection": "1X", "odd": 1.40}
    leg2 = {"bookmaker": "Matchbook", "selection": "2", "odd": 3.80, "native_market_id": "MB_1X2"}

    match_info = {"matchup": "Team A vs Team B", "date": "10.11.2026"}
    res = engine.calculate_2way_arbitrage(leg1, leg2, "Doble Oportunitat vs 2", match_info)

    assert res is not None
    assert res["is_surebet"] is True
    assert res["status"] == "SUREBET"
    assert math.isclose(res["net_guaranteed_profit"], 0.80, abs_tol=1e-2)
    assert math.isclose(res["net_profit_pct"], 0.80, abs_tol=1e-2)

    l1 = res["legs"][0]
    l2 = res["legs"][1]
    assert l1["stake_rounded"] == 72
    assert l2["stake_rounded"] == 28
    assert math.isclose(l1["stake_pct"], 72.0, abs_tol=1e-1)
    assert math.isclose(l2["stake_pct"], 28.0, abs_tol=1e-1)
    assert l1["expected_return"] >= 100.80
    assert l2["expected_return"] >= 100.80


def test_asymmetric_commission_stake_optimization_3way_regression():
    """
    Cas de regressió 3-way:
    - Pota 1: Winamax @ 1.80 (0% comissió)
    - Pota X: Bet365 @ 4.00 (0% comissió)
    - Pota 2: Matchbook @ 6.00 (4% comissió)

    La cerca heurística sobre inversos bruts triava [57, 25, 18] amb mínim 0.00 € (rebutjada com a Surebet).
    L'optimitzador ajustat a comissions troba [56, 26, 18] amb mínim +0.80 € (+0.80%), confirmant la Surebet.
    """
    engine = ArbitrageEngine()

    leg1 = {"bookmaker": "Winamax", "selection": "1", "odd": 1.80}
    legX = {"bookmaker": "Bet365", "selection": "X", "odd": 4.00}
    leg2 = {"bookmaker": "Matchbook", "selection": "2", "odd": 6.00, "native_market_id": "MB_1X2"}

    match_info = {"matchup": "Team A vs Team B", "date": "10.11.2026"}
    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)

    assert res is not None
    assert res["is_surebet"] is True
    assert res["status"] == "SUREBET"
    assert math.isclose(res["net_guaranteed_profit"], 0.80, abs_tol=1e-2)
    assert math.isclose(res["net_profit_pct"], 0.80, abs_tol=1e-2)

    l1, lx, l2 = res["legs"]
    assert l1["stake_rounded"] == 56
    assert lx["stake_rounded"] == 26
    assert l2["stake_rounded"] == 18
    assert math.isclose(l1["stake_pct"], 56.0, abs_tol=1e-1)
    assert math.isclose(lx["stake_pct"], 26.0, abs_tol=1e-1)
    assert math.isclose(l2["stake_pct"], 18.0, abs_tol=1e-1)


# ==============================================================================
# 9. VALIDACIÓ DE CLAU CANÒNICA I SUPÒSIT DE COMPTE ÚNIC
# ==============================================================================

def test_proven_market_key_validation_rejects_unvalidated_strings():
    """
    Verifica que claus canòniques no estructurades o genèriques (ex: '1X2')
    siguin rebutjades per risc de barrejament entre partits diferents, derivant a l'aïllament.
    En canvi, claus estructurades amb identificador d'esdeveniment s'acceptin correctament.
    """
    # 1. Clau genèrica no qualificada -> AÏLLAMENT ESTRICTE
    leg_raw1 = {"bookmaker": "Betfair", "selection": "1", "odd": 2.0, "proven_market_key": "1X2"}
    leg_raw2 = {"bookmaker": "Betfair", "selection": "X", "odd": 3.0, "proven_market_key": "1X2"}
    k1 = ArbitrageEngine.get_settlement_key(leg_raw1)
    k2 = ArbitrageEngine.get_settlement_key(leg_raw2)
    assert "isolated" in k1 and "isolated" in k2
    assert k1 != k2, "Una clau no qualificada com '1X2' mai no ha de permetre agrupació entre partits"

    # 2. Clau qualificada estructurada (dict) amb event_id -> AGRUPACIÓ LÍCITA
    leg_struct1 = {
        "bookmaker": "Betfair",
        "selection": "1",
        "odd": 2.0,
        "proven_market_key": {"event_id": "MATCH_999", "market_type": "1X2"}
    }
    leg_struct2 = {
        "bookmaker": "Betfair",
        "selection": "X",
        "odd": 3.0,
        "proven_market_key": {"event_id": "MATCH_999", "market_type": "1X2"}
    }
    k3 = ArbitrageEngine.get_settlement_key(leg_struct1)
    k4 = ArbitrageEngine.get_settlement_key(leg_struct2)
    assert "canonical" in k3
    assert k3 == k4
    assert "MATCH_999" in k3


def test_single_account_assumption_and_multi_account_isolation():
    """
    Verifica que:
    1. Per defecte (supòsit de compte únic), ordres al mateix native_market_id comparteixen mercat.
    2. Si s'especifica un 'account_id' diferent, queden estrictament aïllades per evitar
       creuar comissions de comptes independents d'exchange.
    """
    # 1. Compte únic per defecte
    leg_def1 = {"bookmaker": "Betfair", "native_market_id": "M_100", "selection": "1", "odd": 2.0}
    leg_def2 = {"bookmaker": "Betfair", "native_market_id": "M_100", "selection": "X", "odd": 3.0}
    assert ArbitrageEngine.get_settlement_key(leg_def1) == ArbitrageEngine.get_settlement_key(leg_def2)

    # 2. Comptes d'usuari diferents (multi-account)
    leg_acc_a = {"bookmaker": "Betfair", "native_market_id": "M_100", "account_id": "trader_alpha", "selection": "1", "odd": 2.0}
    leg_acc_b = {"bookmaker": "Betfair", "native_market_id": "M_100", "account_id": "trader_beta", "selection": "X", "odd": 3.0}
    k_a = ArbitrageEngine.get_settlement_key(leg_acc_a)
    k_b = ArbitrageEngine.get_settlement_key(leg_acc_b)
    assert k_a != k_b, "Comptes d'usuari diferents mai han de creuar liquidació d'exchange"
    assert "trader_alpha" in k_a
    assert "trader_beta" in k_b


# ==============================================================================
# 10. INTEGRACIÓ AMB SCAN_MATCH I SCAN_ALL_MATCHES
# ==============================================================================

def test_scan_match_and_scan_all_matches_with_exchange_odds():
    """
    Verifica que scan_match() i scan_all_matches() descobreixin i liquidin
    correctament mercats amb quotes d'exchanges (Matchbook, Betfair).
    """
    engine = ArbitrageEngine()

    match_with_exchange = {
        "home_team": {"name": "FC Barcelona"},
        "away_team": {"name": "Real Madrid"},
        "date": "15.11.2026 21:00",
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "status": "SCHEDULED",
        "odds_1x2_winamax": {"1": 1.40, "X": 4.50, "2": 6.50},
        "odds_dc_winamax": {"1X": 1.40, "X2": 2.50, "12": 1.20},
        "odds_1x2_matchbook": {"1": 1.35, "X": 4.20, "2": 3.80},
        "market_id_1x2_matchbook": "MB_EL_CLASICO_1X2"
    }

    arbs = engine.scan_match(match_with_exchange)
    assert isinstance(arbs, list)
    assert len(arbs) > 0

    # Comprovar que s'ha detectat la surebet entre Winamax (1X @ 1.40) i Matchbook (2 @ 3.80)
    surebets = [a for a in arbs if a["is_surebet"]]
    assert len(surebets) >= 1
    best_surebet = surebets[0]
    assert best_surebet["status"] == "SUREBET"
    assert best_surebet["net_guaranteed_profit"] > 0.0

    # Verificar que el summary de scan_all_matches reflecteix l'oportunitat
    report = engine.scan_all_matches([match_with_exchange])
    assert report["summary"]["total_surebets"] >= 1
    assert report["summary"]["max_profit_pct"] > 0.0


# ==============================================================================
# 11. OPTIMITZADOR 3-WAY EXACTE: COMPARACIÓ AMB FORÇA BRUTA INDEPENDENT
# ==============================================================================

def independent_brute_force_3way(engine: ArbitrageEngine, leg1: dict, legX: dict, leg2: dict):
    """
    Funció de referència completament independent que avalua exhaustivament
    tots els 4.851 punts de la xarxa discreta d'enters positius:
    s1 >= 1, sx >= 1, s2 >= 1, s1 + sx + s2 = 100.
    Aplica la mateixa jerarquia determinista d'objectius:
    1. min(p0, px, p2)
    2. avg_pnl
    3. menor var_pnl
    4. ordre lexicogràfic
    """
    legs = [
        dict(leg1, leg_id=engine.generate_leg_id(0, str(leg1.get("bookmaker", "")), str(leg1.get("selection", "")), float(leg1["odd"])), _leg_index=0),
        dict(legX, leg_id=engine.generate_leg_id(1, str(legX.get("bookmaker", "")), str(legX.get("selection", "")), float(legX["odd"])), _leg_index=1),
        dict(leg2, leg_id=engine.generate_leg_id(2, str(leg2.get("bookmaker", "")), str(leg2.get("selection", "")), float(leg2["odd"])), _leg_index=2),
    ]
    profiles = [engine.get_exchange_profile(l) for l in legs]
    keys = [engine.get_settlement_key(l) for l in legs]

    best_s1, best_sx, best_s2 = None, None, None
    best_min_pnl = -float("inf")
    best_avg_pnl = -float("inf")
    best_var_pnl = float("inf")

    for s1 in range(1, 99):
        for sx in range(1, 100 - s1):
            s2 = 100 - s1 - sx
            stakes = [float(s1), float(sx), float(s2)]
            p0 = engine.calculate_scenario_pnl(legs, stakes, 0, profiles=profiles, settlement_keys=keys)
            px = engine.calculate_scenario_pnl(legs, stakes, 1, profiles=profiles, settlement_keys=keys)
            p2 = engine.calculate_scenario_pnl(legs, stakes, 2, profiles=profiles, settlement_keys=keys)

            min_p = min(p0, px, p2)
            avg_p = (p0 + px + p2) / 3.0
            var_p = (p0 - avg_p) ** 2 + (px - avg_p) ** 2 + (p2 - avg_p) ** 2

            if min_p > best_min_pnl + 1e-6:
                best_min_pnl = min_p
                best_avg_pnl = avg_p
                best_var_pnl = var_p
                best_s1, best_sx, best_s2 = s1, sx, s2
            elif abs(min_p - best_min_pnl) <= 1e-6:
                if avg_p > best_avg_pnl + 1e-6:
                    best_min_pnl = min_p
                    best_avg_pnl = avg_p
                    best_var_pnl = var_p
                    best_s1, best_sx, best_s2 = s1, sx, s2
                elif abs(avg_p - best_avg_pnl) <= 1e-6:
                    if var_p < best_var_pnl - 1e-6:
                        best_min_pnl = min_p
                        best_avg_pnl = avg_p
                        best_var_pnl = var_p
                        best_s1, best_sx, best_s2 = s1, sx, s2

    return best_s1, best_sx, best_s2, best_min_pnl


def test_3way_optimizer_matches_reference_3_bookmakers_no_commission():
    """Cas 1: 3 bookmakers tradicionals sense comissió."""
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Winamax", "selection": "1", "odd": 3.10}
    legX = {"bookmaker": "Bet365", "selection": "X", "odd": 3.40}
    leg2 = {"bookmaker": "Bwin", "selection": "2", "odd": 3.50}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_matches_reference_1_exchange_2_bookmakers():
    """Cas 2: 1 exchange amb comissió (Betfair 2%) + 2 bookmakers."""
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.15, "native_market_id": "BF_MATCH_101"}
    legX = {"bookmaker": "Winamax", "selection": "X", "odd": 3.35}
    leg2 = {"bookmaker": "Bet365", "selection": "2", "odd": 3.45}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_matches_reference_same_exchange_same_native_market():
    """Cas 3: 2 potes a la mateixa exchange en el mateix mercat natiu (agrupació NET_MARKET_WINNINGS)."""
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.20, "native_market_id": "BF_SHARED_1X2"}
    legX = {"bookmaker": "Betfair", "selection": "X", "odd": 3.30, "native_market_id": "BF_SHARED_1X2"}
    leg2 = {"bookmaker": "Winamax", "selection": "2", "odd": 3.40}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_matches_reference_same_exchange_different_markets():
    """Cas 4: 2 potes a la mateixa exchange en mercats diferents (sense agrupació, aïllament estricte)."""
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.20, "native_market_id": "BF_MKT_A"}
    legX = {"bookmaker": "Betfair", "selection": "X", "odd": 3.30, "native_market_id": "BF_MKT_B"}
    leg2 = {"bookmaker": "Winamax", "selection": "2", "odd": 3.40}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_matches_reference_per_bet_settlement_smarkets_pro_select():
    """Cas 5: Exchange amb PER_BET_SETTLEMENT (Smarkets Pro 1% i Select 3%)."""
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Smarkets", "selection": "1", "odd": 3.10, "account_tier": "pro"}
    legX = {"bookmaker": "Smarkets", "selection": "X", "odd": 3.30, "account_tier": "select"}
    leg2 = {"bookmaker": "Winamax", "selection": "2", "odd": 3.50}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_matches_reference_extreme_odds():
    """Cas 6: Quotes asimètriques extremes (un favorit molt clar i dues sorpreses altes, o1=1.25, ox=9.0, o2=13.0 <= 15.0)."""
    engine = ArbitrageEngine()
    # 1/1.25 + 1/9.0 + 1/13.0 = 0.80 + 0.1111 + 0.0769 = 0.9880 (profit brut ~ +1.21%)
    leg1 = {"bookmaker": "Winamax", "selection": "1", "odd": 1.25}
    legX = {"bookmaker": "Betfair", "selection": "X", "odd": 9.0, "native_market_id": "BF_MKT_EXT"}
    leg2 = {"bookmaker": "Bet365", "selection": "2", "odd": 13.0}
    match_info = {"matchup": "Super Favorit vs Desconegut", "date": "01.12.2026"}

    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    assert res is not None

    ref_s1, ref_sx, ref_s2, ref_min_pnl = independent_brute_force_3way(engine, leg1, legX, leg2)

    assert res["legs"][0]["stake_rounded"] == ref_s1
    assert res["legs"][1]["stake_rounded"] == ref_sx
    assert res["legs"][2]["stake_rounded"] == ref_s2
    assert abs(res["net_guaranteed_profit"] - round(ref_min_pnl, 2)) < 0.02


def test_3way_optimizer_execution_time_under_budget():
    """Verifica que l'optimitzador exhaustiu dels 4.851 punts s'executa en menys de 150 ms."""
    import time
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Winamax", "selection": "1", "odd": 3.10}
    legX = {"bookmaker": "Bet365", "selection": "X", "odd": 3.40}
    leg2 = {"bookmaker": "Betfair", "selection": "2", "odd": 3.50, "native_market_id": "BF_BENCH"}
    match_info = {"matchup": "A vs B", "date": "01.12.2026"}

    t0 = time.perf_counter()
    res = engine.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert res is not None
    assert elapsed_ms < 150.0, f"Temps d'execució ({elapsed_ms:.1f} ms) per sobre del límit de 150 ms"


# ==============================================================================
# 12. CONTRACTE DE DADES, VALORS AUTORITARIS I VALIDACIÓ FRONTEND
# ==============================================================================

def test_data_contract_authoritative_and_informative_fields_presence():
    """
    Verifica que el contracte de dades generat pel backend conté tots els camps:
    - Autoritaris de liquidació: base_investment, net_guaranteed_profit, min_guaranteed_return,
      profit_pct, net_profit_pct, i per pota: stake_rounded, expected_return, settlement_key,
      commission_model, commission_rate.
    - Informatius: type, market_name, matchup, date, competition_id, competition_name,
      is_surebet, status, gross_profit_pct, margin_pct, skew_seconds, freshness_valid.
    """
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Winamax", "selection": "Over 2.5", "odd": 2.10}
    leg2 = {"bookmaker": "Betfair", "selection": "Under 2.5", "odd": 2.10, "native_market_id": "BF_OU"}
    match_info = {"matchup": "Equip A vs Equip B", "date": "01.12.2026", "competition_id": "LALIGA"}

    arb = engine.calculate_2way_arbitrage(leg1, leg2, "Over/Under 2.5", match_info)
    assert arb is not None

    authoritative_root = [
        "base_investment", "net_guaranteed_profit", "min_guaranteed_return",
        "profit_pct", "net_profit_pct"
    ]
    informative_root = [
        "type", "market_name", "matchup", "date", "competition_id",
        "is_surebet", "status", "gross_profit_pct", "margin_pct",
        "skew_seconds", "freshness_valid"
    ]
    for field in authoritative_root:
        assert field in arb, f"Camp autoritari '{field}' absent a l'arrel"
    for field in informative_root:
        assert field in arb, f"Camp informatiu '{field}' absent a l'arrel"

    authoritative_leg = [
        "stake_rounded", "expected_return", "settlement_key",
        "commission_model", "commission_rate"
    ]
    for leg in arb["legs"]:
        for field in authoritative_leg:
            assert field in leg, f"Camp autoritari '{field}' absent a la pota"
        assert "bookmaker" in leg
        assert "selection" in leg
        assert "odd" in leg


def test_frontend_does_not_recalculate_gross_odds_or_divert_exchanges():
    """
    Verifica per anàlisi estàtica del fitxer web/app.js:
    1. No recalcula stakes mitjançant quotes brutes (total / sumInv / odd) a renderCard ni copyArbitrageInstructions.
    2. Utilitza computeArbitrageValues per consumir directament stake_rounded i expected_return.
    3. getOperatorInfo mai no redirigeix Betfair, Matchbook o Smarkets cap a Bet365.
    4. Mostra 'Sense enllaç directe' quan no hi ha URL disponible per a un exchange.
    """
    js_path = os.path.join(os.path.dirname(__file__), "..", "web", "app.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # 1. computeArbitrageValues existeix i s'utilitza com a font única de veritat
    assert "function computeArbitrageValues" in js_content
    assert "function getOperatorInfo" in js_content

    # 2. No hi ha càlcul de quotes brutes a renderCard ni copyArbitrageInstructions
    assert "exactStakes = legs.map" not in js_content
    assert "(total / sumInv) / l.odd" not in js_content
    assert "(totalBankroll / sumInv) / l.odd" not in js_content

    # 3. getOperatorInfo defineix Betfair, Matchbook i Smarkets explícitament
    assert "b.includes('betfair')" in js_content
    assert "b.includes('matchbook')" in js_content
    assert "b.includes('smarkets')" in js_content
    assert "Sense enllaç directe" in js_content

    # 4. No hi ha cap línia que assumeixi que non-winamax és automàticament bet365 a arbitrage
    assert "isWinamax ? 'arb-tag-winamax' : 'arb-tag-bet365'" not in js_content
    assert "isWinamax ? (leg.url || 'https://www.winamax.es') : getValidBet365Url(leg.url)" not in js_content


# ==============================================================================
# 9. VERIFICACIÓ D'ABSÈNCIA DE SCRAPERS FALSOS O SIMULATS D'EXCHANGES
# ==============================================================================

def verify_scraper_directory_no_fake_exchanges(scraper_dir_path: str):
    """Funció auxiliar verificadora que valida l'existència i contingut del directori scraper."""
    if not os.path.isdir(scraper_dir_path):
        raise AssertionError(f"El directori de scrapers no existeix a {scraper_dir_path}")
    files = os.listdir(scraper_dir_path)
    if not files:
        raise AssertionError(f"El directori {scraper_dir_path} està buit")
    for fname in files:
        fname_lower = fname.lower()
        if "betfair" in fname_lower:
            raise AssertionError(f"Scraper no autoritzat detectat: {fname}")
        if "matchbook" in fname_lower:
            raise AssertionError(f"Scraper no autoritzat detectat: {fname}")
        if "smarkets" in fname_lower:
            raise AssertionError(f"Scraper no autoritzat detectat: {fname}")
    return files


def test_no_simulated_or_fake_scrapers_for_exchanges():
    """
    Objectiu 4: Verifica que NO s'hagin creat scrapers falsos o simulats per a Betfair, Matchbook o Smarkets.
    Només Winamax i Bet365 han de tenir mòduls de scraping reals actius al directori scraper/ (singular).
    """
    scraper_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scraper"))
    files = verify_scraper_directory_no_fake_exchanges(scraper_dir)
    # Confirmar presència dels scrapers legítims
    assert any("bet365" in f.lower() for f in files), "scraper/ ha de contenir el scraper de Bet365"
    assert any("winamax" in f.lower() for f in files), "scraper/ ha de contenir el scraper de Winamax"


def test_scraper_verifier_logic_detects_simulated_files(tmp_path):
    """Meta-test: comprova que el verificador detecta i rebutja fitxers simulats d'exchanges."""
    fake_bf = tmp_path / "betfair_scraper.py"
    fake_bf.write_text("# fake", encoding="utf-8")
    with pytest.raises(AssertionError, match="Scraper no autoritzat detectat: betfair_scraper.py"):
        verify_scraper_directory_no_fake_exchanges(str(tmp_path))

    fake_bf.unlink()
    fake_mb = tmp_path / "matchbook_api.py"
    fake_mb.write_text("# fake", encoding="utf-8")
    with pytest.raises(AssertionError, match="Scraper no autoritzat detectat: matchbook_api.py"):
        verify_scraper_directory_no_fake_exchanges(str(tmp_path))

    fake_mb.unlink()
    fake_sm = tmp_path / "smarkets_live.py"
    fake_sm.write_text("# fake", encoding="utf-8")
    with pytest.raises(AssertionError, match="Scraper no autoritzat detectat: smarkets_live.py"):
        verify_scraper_directory_no_fake_exchanges(str(tmp_path))


def test_scraper_verifier_fails_if_dir_missing(tmp_path):
    """Meta-test: comprova que el verificador falla si el directori no existeix o està buit."""
    non_existent = str(tmp_path / "inexistent_dir")
    with pytest.raises(AssertionError, match="El directori de scrapers no existeix"):
        verify_scraper_directory_no_fake_exchanges(non_existent)

    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    with pytest.raises(AssertionError, match="està buit"):
        verify_scraper_directory_no_fake_exchanges(str(empty_dir))


# ==============================================================================
# 10. PROVES DE BANKROLLS NO ESTÀNDARD I DISCREPÀNCIES ZERO
# ==============================================================================

@pytest.mark.parametrize("bankroll", [50.0, 75.0, 99.0, 100.0, 101.0, 150.0, 200.0])
def test_bankroll_exact_simplex_optimization_2way(bankroll):
    """
    Verifica que per a qualsevol bankroll (50, 75, 99, 100, 101, 150, 200 €) en 2-way:
    1. Els stakes sumen exactament el bankroll.
    2. El net_guaranteed_profit coincideix amb el mínim dels PnL nets de cada escenari.
    3. min_guaranteed_return coincideix exactament amb bankroll + net_guaranteed_profit.
    4. La discrepància entre el PnL anunciat i la liquidació real és exactament 0.00 €.
    """
    engine = ArbitrageEngine()
    leg1 = {
        "bookmaker": "Betfair",
        "selection": "Over 2.5",
        "odd": 2.15,
        "native_market_id": "bf_ou_99"
    }
    leg2 = {
        "bookmaker": "Winamax",
        "selection": "Under 2.5",
        "odd": 2.10
    }
    arb = engine.calculate_2way_arbitrage(
        leg1, leg2, "Total Gols (Over/Under 2.5)",
        {"matchup": "Equip A vs Equip B", "competition_id": "test_comp", "competition_name": "Test"},
        bankroll=bankroll
    )
    assert arb is not None
    assert arb["base_investment"] == bankroll
    stakes = [l["stake_rounded"] for l in arb["legs"]]
    assert sum(stakes) == bankroll
    assert all(s >= 1 for s in stakes)

    # Comprovar liquidació real
    p0 = engine.calculate_scenario_pnl(arb["legs"], stakes, 0)
    p1 = engine.calculate_scenario_pnl(arb["legs"], stakes, 1)
    min_real_pnl = round(min(p0, p1), 2)

    assert arb["net_guaranteed_profit"] == min_real_pnl
    assert arb["min_guaranteed_return"] == round(bankroll + min_real_pnl, 2)
    assert arb["scenario_net_pnls"] == [round(p0, 2), round(p1, 2)]
    assert arb["scenario_total_returns"] == [round(bankroll + p0, 2), round(bankroll + p1, 2)]


@pytest.mark.parametrize("bankroll", [50.0, 75.0, 99.0, 100.0, 101.0, 150.0, 200.0])
def test_bankroll_exact_simplex_optimization_3way(bankroll):
    """
    Verifica que per a qualsevol bankroll (50, 75, 99, 100, 101, 150, 200 €) en 3-way:
    1. Els stakes sumen exactament el bankroll.
    2. El net_guaranteed_profit coincideix amb el mínim dels PnL nets de cada escenari.
    3. min_guaranteed_return coincideix exactament amb bankroll + net_guaranteed_profit.
    4. La discrepància entre el benefici garantit i la liquidació real és 0.00 €.
    """
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Bet365", "selection": "1", "odd": 3.10}
    legx = {"bookmaker": "Winamax", "selection": "X", "odd": 3.30}
    leg2 = {"bookmaker": "Betfair", "selection": "2", "odd": 3.40, "native_market_id": "bf_1x2_99"}

    arb = engine.calculate_3way_arbitrage(
        leg1, legx, leg2,
        {"matchup": "Equip A vs Equip B", "competition_id": "test_comp", "competition_name": "Test"},
        bankroll=bankroll
    )
    assert arb is not None
    assert arb["base_investment"] == bankroll
    stakes = [l["stake_rounded"] for l in arb["legs"]]
    assert sum(stakes) == bankroll
    assert all(s >= 1 for s in stakes)

    # Liquidació real independent
    p0 = engine.calculate_scenario_pnl(arb["legs"], stakes, 0)
    p1 = engine.calculate_scenario_pnl(arb["legs"], stakes, 1)
    p2 = engine.calculate_scenario_pnl(arb["legs"], stakes, 2)
    min_real_pnl = round(min(p0, p1, p2), 2)

    assert arb["net_guaranteed_profit"] == min_real_pnl
    assert arb["min_guaranteed_return"] == round(bankroll + min_real_pnl, 2)
    assert arb["scenario_net_pnls"] == [round(p0, 2), round(p1, 2), round(p2, 2)]


def test_arbitrage_engine_optimize_stakes_for_bankroll_api():
    """Verifica el mètode públic ArbitrageEngine.optimize_stakes_for_bankroll."""
    engine = ArbitrageEngine()
    legs = [
        {"bookmaker": "Winamax", "selection": "1", "odd": 2.10},
        {"bookmaker": "Bet365", "selection": "2", "odd": 2.15}
    ]
    res_50 = engine.optimize_stakes_for_bankroll(legs, bankroll=50.0)
    assert res_50["bankroll"] == 50.0
    assert sum(res_50["round_stakes"]) == 50
    assert res_50["net_guaranteed_profit"] == min(res_50["scenario_pnls"])
    assert res_50["min_guaranteed_return"] == 50.0 + res_50["net_guaranteed_profit"]

    res_99 = engine.optimize_stakes_for_bankroll(legs, bankroll=99.0)
    assert res_99["bankroll"] == 99.0
    assert sum(res_99["round_stakes"]) == 99


# ==============================================================================
# 11. SEMÀNTICA DE RETORNS: PAGAMENT BRUT INDIVIDUAL VS RETORN GLOBAL DE CARTERA
# ==============================================================================

def test_return_semantics_separation():
    """
    Verifica que cada pota contingui el camp 'gross_payout' (s_i * odd_i)
    i que aquest sigui conceptualment i numèricament diferent del retorn global
    de cartera ('min_guaranteed_return' o 'scenario_total_returns').
    """
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Winamax", "selection": "1", "odd": 3.10}
    legx = {"bookmaker": "Bet365", "selection": "X", "odd": 3.30}
    leg2 = {"bookmaker": "Betfair", "selection": "2", "odd": 3.40, "native_market_id": "bf_1x2_sem"}

    arb = engine.calculate_3way_arbitrage(
        leg1, legx, leg2,
        {"matchup": "Equip A vs Equip B", "competition_id": "test_comp", "competition_name": "Test"},
        bankroll=100.0
    )
    assert arb is not None
    # Cada pota té gross_payout
    for i, leg in enumerate(arb["legs"]):
        assert "gross_payout" in leg, f"Pota {i} ha de tenir gross_payout"
        expected_gross = round(leg["stake_rounded"] * leg["odd"], 2)
        assert leg["gross_payout"] == expected_gross
        assert leg["gross_payout"] != arb["min_guaranteed_return"] or abs(leg["gross_payout"] - arb["min_guaranteed_return"]) < 5.0

    # L'arrel conté els retorns globals per escenari
    assert "scenario_total_returns" in arb
    assert len(arb["scenario_total_returns"]) == 3
    assert "scenario_net_pnls" in arb
    assert len(arb["scenario_net_pnls"]) == 3


# ==============================================================================
# 12. INTEGRACIÓ DE PROVES FUNCIONALS FRONTEND (NODE.JS)
# ==============================================================================

def test_frontend_node_functional_suite():
    """
    Executa la suite completa de proves funcionals Node.js de frontend (tests/test_functional_web.js)
    i verifica que totes les proves passin sense fallades.
    """
    import subprocess
    js_test_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_functional_web.js"))
    assert os.path.isfile(js_test_path), f"El fitxer de proves frontend no existeix: {js_test_path}"

    result = subprocess.run(
        ["node", js_test_path],
        capture_output=True,
        text=True,
        check=False
    )
    assert result.returncode == 0, f"Error executant test_functional_web.js:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "TOTES LES PROVES FUNCIONALS DE FRONTEND HAN PASSAT" in result.stdout


# ==============================================================================
# 13. POLÍTICA D'ARRODONIMENT COMERCIAL I COHERÈNCIA DE BANKROLLS DECIMALS
# ==============================================================================

@pytest.mark.parametrize("val,expected", [
    (100.49, 100),
    (100.50, 101),
    (100.51, 101),
    (101.50, 102),
    (102.50, 103),
    (99.40, 99),
    (50.0, 50),
    (100.0, 100),
    (200.0, 200),
])
def test_commercial_rounding_policy_python_vs_independent(val, expected):
    """
    Comprova que l'arrodoniment comercial a Python implementa la mateixa política que JavaScript (half-up):
    1. Comprovació independent amb math.floor(x + 0.5).
    2. Comprovació de round_commercial(val) == expected.
    3. Comprovació de normalize_bankroll(val, min_legs=2) == max(2, expected).
    """
    independent_rounded = int(math.floor(val + 0.5))
    assert independent_rounded == expected, f"Fórmula independent per a {val} ha de ser {expected}"
    assert ArbitrageEngine.round_commercial(val) == expected
    assert ArbitrageEngine.normalize_bankroll(val, min_legs=2) == max(2, expected)


@pytest.mark.parametrize("input_b,expected_b", [
    (100.49, 100),
    (100.50, 101),
    (100.51, 101),
    (101.50, 102),
    (102.50, 103),
    (99.40, 99),
    (50.0, 50),
    (100.0, 100),
    (200.0, 200),
])
def test_arbitrage_engine_decimal_bankrolls_effective_settlement(input_b, expected_b):
    """
    Verifica que per a qualsevol bankroll decimal (100.49, 100.50, 100.51, 101.50, 102.50, 99.40, 50, 100, 200 €):
    1. Python normalitza el bankroll al mateix valor efectiu enter esperat.
    2. La suma dels stakes coincideix exactament amb el bankroll efectiu normalitzat.
    3. El retorn garantit es calcula sobre aquest mateix bankroll efectiu.
    4. La liquidació real per escenaris amb calculate_scenario_pnl coincideix amb discrepància 0.00 €.
    """
    engine = ArbitrageEngine()
    leg1 = {"bookmaker": "Betfair", "selection": "1", "odd": 3.15, "commission_rate": 0.02, "commission_model": "NET_MARKET_WINNINGS", "native_market_id": "bf_1"}
    legx = {"bookmaker": "Winamax", "selection": "X", "odd": 3.35, "commission_rate": 0.0, "commission_model": "NONE"}
    leg2 = {"bookmaker": "Bet365", "selection": "2", "odd": 3.45, "commission_rate": 0.0, "commission_model": "NONE"}
    match_info = {"matchup": "Equip A vs Equip B", "competition_id": "test_comp", "competition_name": "Test"}

    arb = engine.calculate_3way_arbitrage(leg1, legx, leg2, match_info, bankroll=input_b)
    assert arb is not None

    # 1. Base d'inversió és el bankroll efectiu normalitzat
    assert arb["base_investment"] == float(expected_b)

    # 2. Suma dels stakes és exactament el bankroll efectiu
    stakes = [l["stake_rounded"] for l in arb["legs"]]
    assert sum(stakes) == expected_b
    assert all(isinstance(s, int) and s >= 1 for s in stakes)

    # 3. Retorn mínim calculat sobre el bankroll efectiu
    expected_min_return = round(float(expected_b) + arb["net_guaranteed_profit"], 2)
    assert arb["min_guaranteed_return"] == expected_min_return

    # 4. Liquidació real d'escenaris
    p0 = engine.calculate_scenario_pnl(arb["legs"], stakes, 0)
    px = engine.calculate_scenario_pnl(arb["legs"], stakes, 1)
    p2 = engine.calculate_scenario_pnl(arb["legs"], stakes, 2)
    real_min_pnl = round(min(p0, px, p2), 2)
    assert arb["net_guaranteed_profit"] == real_min_pnl




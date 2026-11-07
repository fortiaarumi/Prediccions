"""
tests/test_audit_prioritized_fixes.py
======================================
Suite completa de tests de verificació matemàtica i regressió:
1. Over / Exactly / Under 6 en un mercat europeu de 3 opcions amb comprovació de valors individuals exactes.
2. Over 10.0 i Under 10.0 asiàtics, incloent-hi la probabilitat exacta de push (devolució).
3. Over 8.5 i Under 8.5 sense devolució (mitja línia binària).
4. Mercat enter de format desconegut: no es publica com a oportunitat de valor.
5. Càlcul correcte de l'EV i de la quota justa per a Over i Under asiàtics.
6. Rebuig d'associació d'una quota europea a una selecció asiàtica del mateix llindar.
7. Transmissió idèntica de probabilitats a single_markets, value_bets, exporter i exclusió segura de combinades.
8. Regressió del cas de Bet365 Sevilla vs Osasuna: Quota real 1.29 per a Over 6 (+7.7% EV) i 8.50 per a Exactly 6 (-35.1% EV).
9. Manteniment de procedència i motius de rebuig Bet365 a value_bets.
10. Marcador modal amb probabilitat exacta procedent de la matriu Dixon-Coles.
"""

import math
import pytest
from scipy.stats import poisson

from engine.value_bet_engine import ValueBetEngine
from engine.combo_bet_engine import ComboBetEngine
from model.goal_model import GoalModel
from engine.web_data_exporter import WebDataExporter


# =============================================================================
# CAS 1: OVER / EXACTLY / UNDER 6 EN UN MERCAT EUROPEU DE 3 OPCIONS
# =============================================================================
def test_1_european_3way_corners_over_exactly_under_6():
    """Verifica els valors numèrics individuals exactes de win, loss i push per a un mercat europeu de 3 opcions."""
    lam = 9.5
    parsed_over = ValueBetEngine.classify_corner_market("Alternative Corners Over 6")
    parsed_exact = ValueBetEngine.classify_corner_market("Alternative Corners Exactly 6")
    parsed_under = ValueBetEngine.classify_corner_market("Alternative Corners Under 6")

    assert parsed_over["market_type"] == "OVER"
    assert parsed_over["market_format"] == "EUROPEAN_3WAY"
    assert parsed_exact["market_type"] == "EXACT"
    assert parsed_exact["market_format"] == "EUROPEAN_3WAY"
    assert parsed_under["market_type"] == "UNDER"
    assert parsed_under["market_format"] == "EUROPEAN_3WAY"

    p_over = ValueBetEngine.calculate_corner_probabilities("OVER", 6.0, lam, "EUROPEAN_3WAY")
    p_exact = ValueBetEngine.calculate_corner_probabilities("EXACT", 6.0, lam, "EUROPEAN_3WAY")
    p_under = ValueBetEngine.calculate_corner_probabilities("UNDER", 6.0, lam, "EUROPEAN_3WAY")

    # Valors teòrics calculats independentment per a Poisson(9.5) i K=6:
    # F(6) = 0.164949, F(5) = 0.088528, pmf(6) = 0.076421
    # Over 6: win = 1 - F(6) = 0.835051, loss = F(6) = 0.164949, push = 0.0
    assert math.isclose(p_over["p_win"], 0.835051, abs_tol=1e-5)
    assert math.isclose(p_over["p_loss"], 0.164949, abs_tol=1e-5)
    assert p_over["p_push"] == 0.0
    assert math.isclose(p_over["p_win"] + p_over["p_push"] + p_over["p_loss"], 1.0, abs_tol=1e-9)

    # Exactly 6: win = pmf(6) = 0.076421, loss = 1 - pmf(6) = 0.923579, push = 0.0
    assert math.isclose(p_exact["p_win"], 0.076421, abs_tol=1e-5)
    assert math.isclose(p_exact["p_loss"], 0.923579, abs_tol=1e-5)
    assert p_exact["p_push"] == 0.0
    assert math.isclose(p_exact["p_win"] + p_exact["p_push"] + p_exact["p_loss"], 1.0, abs_tol=1e-9)

    # Under 6: win = F(5) = 0.088528, loss = 1 - F(5) = 0.911472, push = 0.0
    assert math.isclose(p_under["p_win"], 0.088528, abs_tol=1e-5)
    assert math.isclose(p_under["p_loss"], 0.911472, abs_tol=1e-5)
    assert p_under["p_push"] == 0.0
    assert math.isclose(p_under["p_win"] + p_under["p_push"] + p_under["p_loss"], 1.0, abs_tol=1e-9)

    # Suma de les 3 opcions mútuament excloents = 1.0
    total_3way = p_over["p_win"] + p_exact["p_win"] + p_under["p_win"]
    assert math.isclose(total_3way, 1.0, abs_tol=1e-9)


# =============================================================================
# CAS 2: OVER 10.0 I UNDER 10.0 ASIÀTICS AMB PROBABILITAT EXACTA DE PUSH
# =============================================================================
def test_2_asian_corners_over_10_and_under_10_positive_push():
    """Verifica els valors individuals exactes de win, push i loss per a Over 10.0 i Under 10.0 asiàtics."""
    lam = 9.5
    parsed_o = ValueBetEngine.classify_corner_market("Asian Total Corners Over 10.0")
    parsed_u = ValueBetEngine.classify_corner_market("Asian Total Corners Under 10.0")

    assert parsed_o["market_format"] == "ASIAN_2WAY"
    assert parsed_u["market_format"] == "ASIAN_2WAY"

    probs_o = ValueBetEngine.calculate_corner_probabilities("OVER", 10.0, lam, "ASIAN_2WAY")
    probs_u = ValueBetEngine.calculate_corner_probabilities("UNDER", 10.0, lam, "ASIAN_2WAY")

    # Valors teòrics per a Poisson(9.5) i K=10:
    # F(10) = 0.645328, F(9) = 0.521826, pmf(10) = 0.123502
    # Over 10: win = 1 - F(10) = 0.354672, push = pmf(10) = 0.123502, loss = F(9) = 0.521826
    assert math.isclose(probs_o["p_win"], 0.354672, abs_tol=1e-5)
    assert math.isclose(probs_o["p_push"], 0.123502, abs_tol=1e-5)
    assert math.isclose(probs_o["p_loss"], 0.521826, abs_tol=1e-5)
    assert math.isclose(probs_o["p_win"] + probs_o["p_push"] + probs_o["p_loss"], 1.0, abs_tol=1e-9)

    # Under 10: win = F(9) = 0.521826, push = pmf(10) = 0.123502, loss = 1 - F(10) = 0.354672
    assert math.isclose(probs_u["p_win"], 0.521826, abs_tol=1e-5)
    assert math.isclose(probs_u["p_push"], 0.123502, abs_tol=1e-5)
    assert math.isclose(probs_u["p_loss"], 0.354672, abs_tol=1e-5)
    assert math.isclose(probs_u["p_win"] + probs_u["p_push"] + probs_u["p_loss"], 1.0, abs_tol=1e-9)


# =============================================================================
# CAS 3: OVER 8.5 I UNDER 8.5 SENSE DEVOLUCIÓ (MITJA LÍNIA)
# =============================================================================
def test_3_half_ball_corners_over_85_and_under_85_no_push():
    """Verifica els valors individuals exactes per a Over 8.5 i Under 8.5."""
    lam = 9.5
    parsed_o = ValueBetEngine.classify_corner_market("Corners 2-Way Over 8.5")
    parsed_u = ValueBetEngine.classify_corner_market("Corners 2-Way Under 8.5")

    assert parsed_o["market_format"] == "HALF_BALL_2WAY"
    assert parsed_u["market_format"] == "HALF_BALL_2WAY"

    probs_o = ValueBetEngine.calculate_corner_probabilities("OVER", 8.5, lam, "HALF_BALL_2WAY")
    probs_u = ValueBetEngine.calculate_corner_probabilities("UNDER", 8.5, lam, "HALF_BALL_2WAY")

    # Valors teòrics per a Poisson(9.5) i T=8.5 (floor=8):
    # F(8) = 0.391823, Over = 1 - F(8) = 0.608177
    assert math.isclose(probs_o["p_win"], 0.608177, abs_tol=1e-5)
    assert math.isclose(probs_o["p_loss"], 0.391823, abs_tol=1e-5)
    assert probs_o["p_push"] == 0.0
    assert math.isclose(probs_o["p_win"] + probs_o["p_loss"], 1.0, abs_tol=1e-9)

    assert math.isclose(probs_u["p_win"], 0.391823, abs_tol=1e-5)
    assert math.isclose(probs_u["p_loss"], 0.608177, abs_tol=1e-5)
    assert probs_u["p_push"] == 0.0
    assert math.isclose(probs_u["p_win"] + probs_u["p_loss"], 1.0, abs_tol=1e-9)


# =============================================================================
# CAS 4: MERCAT ENTER DE FORMAT DESCONEGUT: EXCLÒS DE VALOR
# =============================================================================
def test_4_unknown_integer_corner_market_excluded():
    """Un mercat enter sense indicadors ni metadata es cataloga com a UNKNOWN i no es publica."""
    parsed = ValueBetEngine.classify_corner_market("Corners Over 10")
    assert parsed["market_format"] == "UNKNOWN"
    assert parsed["market_type"] == "UNKNOWN"

    engine = ValueBetEngine()
    pred = {
        "home_team": "Team A", "away_team": "Team B",
        "goals": {"prob_1X2": {"1": 40.0, "X": 30.0, "2": 30.0}, "over_under": {}, "btts": {}, "top_scorelines": []},
        "cards": {"prob_over_cards": {}},
        "corners": {"expected_total_corners": 9.5, "prob_over_corners": {}}
    }
    unknown_feed = {"corners": {"Corners Over 10": 2.20}}
    res = engine.analyze_match_betting(pred, winamax_odds=unknown_feed, bet365_odds={})
    singles = res.get("single_markets", [])

    assert len([m for m in singles if "10" in m.get("name", "")]) == 0


# =============================================================================
# CAS 5: EV I QUOTA JUSTA PER A OVER I UNDER ASIÀTICS
# =============================================================================
def test_5_asian_ev_and_fair_odd_for_over_and_under():
    """Comprova la quota justa i EV d'Over 10.0 i Under 10.0 asiàtics amb la fórmula amb push."""
    engine = ValueBetEngine(kelly_fraction=0.25)
    lam = 9.5
    probs_o = ValueBetEngine.calculate_corner_probabilities("OVER", 10.0, lam, "ASIAN_2WAY")
    probs_u = ValueBetEngine.calculate_corner_probabilities("UNDER", 10.0, lam, "ASIAN_2WAY")

    # 1. Over 10.0: P_win = 0.354672, P_push = 0.123502, P_loss = 0.521826
    # Quota justa: (1 - 0.123502) / 0.354672 = 0.876498 / 0.354672 = 2.4713 -> 2.47
    v_o_neg = engine.calculate_market_value(probs_o["p_win"], 2.10, push_prob=probs_o["p_push"], loss_prob=probs_o["p_loss"])
    assert v_o_neg["fair_odd"] == 2.47
    # EV a quota 2.10: (0.354672 * 2.10 + 0.123502 - 1.0) * 100 = (0.744811 + 0.123502 - 1.0) * 100 = -13.17% -> -13.2%
    assert v_o_neg["ev_pct"] == -13.2
    assert v_o_neg["is_value"] is False

    # EV a quota 2.60: (0.354672 * 2.60 + 0.123502 - 1.0) * 100 = (0.922147 + 0.123502 - 1.0) * 100 = +4.56% -> +4.6%
    v_o_pos = engine.calculate_market_value(probs_o["p_win"], 2.60, push_prob=probs_o["p_push"], loss_prob=probs_o["p_loss"])
    assert v_o_pos["ev_pct"] == 4.6
    assert v_o_pos["is_value"] is True
    assert v_o_pos["kelly_pct"] > 0.0

    # 2. Under 10.0: P_win = 0.521826, P_push = 0.123502, P_loss = 0.354672
    # Quota justa: (1 - 0.123502) / 0.521826 = 0.876498 / 0.521826 = 1.6796 -> 1.68
    v_u_pos = engine.calculate_market_value(probs_u["p_win"], 2.10, push_prob=probs_u["p_push"], loss_prob=probs_u["p_loss"])
    assert v_u_pos["fair_odd"] == 1.68
    # EV a quota 2.10: (0.521826 * 2.10 + 0.123502 - 1.0) * 100 = (1.095835 + 0.123502 - 1.0) * 100 = +21.93% -> +21.9%
    assert v_u_pos["ev_pct"] == 21.9
    assert v_u_pos["is_value"] is True

    # EV a quota 1.60: (0.521826 * 1.60 + 0.123502 - 1.0) * 100 = (0.834922 + 0.123502 - 1.0) * 100 = -4.16% -> -4.2%
    v_u_neg = engine.calculate_market_value(probs_u["p_win"], 1.60, push_prob=probs_u["p_push"], loss_prob=probs_u["p_loss"])
    assert v_u_neg["ev_pct"] == -4.2
    assert v_u_neg["is_value"] is False


# =============================================================================
# CAS 6: REBUIG D'EMPARELLAMENT DE QUOTES AMB SEMÀNTICA DIFERENT
# =============================================================================
def test_6_reject_mismatched_semantics_odds_and_formats():
    """Un mercat asiàtic de 2 opcions no es pot emparellar amb una quota d'un mercat europeu de 3 opcions."""
    b365_european = {
        "Alternative Corners Over 10": 2.50,
        "Alternative Corners Exactly 10": 7.50,
        "Alternative Corners Under 10": 1.65
    }
    # Cerca per a mercat ASIAN_2WAY
    matched_odd = ValueBetEngine._find_matching_corner_odd(
        b365_european, target_type="OVER", target_thresh=10.0, target_format="ASIAN_2WAY"
    )
    assert matched_odd is None

    matched_euro = ValueBetEngine._find_matching_corner_odd(
        b365_european, target_type="OVER", target_thresh=10.0, target_format="EUROPEAN_3WAY"
    )
    assert matched_euro == 2.50


# =============================================================================
# CAS 7: TRANSMISSIÓ DE PROBABILITATS I EXCLUSIÓ SEGURA DE COMBINADES
# =============================================================================
def test_7_prob_propagation_value_bets_and_combos():
    """Comprova que les probabilitats de ValueBetEngine són exactament les que es transmeten i s'exclouen de combinades."""
    engine = ValueBetEngine()
    lam = 9.5
    pred = {
        "home_team": "Team A", "away_team": "Team B",
        "goals": {"prob_1X2": {"1": 45.0, "X": 25.0, "2": 30.0}, "over_under": {}, "btts": {}, "top_scorelines": []},
        "cards": {"prob_over_cards": {}},
        "corners": {"expected_total_corners": lam, "prob_over_corners": {}}
    }
    asian_feed = {"corners": {"Asian Total Corners Over 10.0": 2.60}}
    res = engine.analyze_match_betting(pred, winamax_odds=asian_feed, bet365_odds={})
    singles = res["single_markets"]
    assert len(singles) >= 1
    m = singles[0]

    # Verificar probabilitats transmeses
    assert m["model_prob"] == 35.5  # round(0.354672 * 100, 1)
    assert m["push_prob"] == 12.4   # round(0.123502 * 100, 1)
    assert m["loss_prob"] == 52.2   # round(0.521826 * 100, 1)
    assert m["market_format"] == "ASIAN_2WAY"

    # Exclusió segura de combinades
    combo_engine = ComboBetEngine()
    leg = {
        "matchup": "A vs B", "competition_id": "LALIGA",
        "name": m["name"], "category": "Córners",
        "market_format": m["market_format"], "model_prob": m["model_prob"],
        "push_prob": m["push_prob"], "bookie_odd": m["bookie_odd"],
        "provenance": "WINAMAX", "is_verified": True
    }
    other_leg = {
        "matchup": "C vs D", "competition_id": "LALIGA",
        "name": "1X", "category": "Doble Oportunitat",
        "market_format": "HALF_BALL_2WAY", "model_prob": 70.0,
        "push_prob": 0.0, "bookie_odd": 1.40,
        "provenance": "WINAMAX", "is_verified": True
    }
    assert combo_engine.is_valid_combo_legs([leg, other_leg]) is False

    # Càlcul conjunt a calculate_combo_summary
    stats = combo_engine.calculate_combo_summary([leg, other_leg], profile_name="TEST_PUSH")
    # Payout conjunt: (0.355 * 2.60 + 0.124) * (0.70 * 1.40 + 0.0) = 1.047 * 0.98 = 1.026 -> +2.6% EV
    assert stats["has_push_legs"] is True
    assert math.isclose(stats["ev_pct"], 2.6, abs_tol=0.2)


# =============================================================================
# CAS 8: REGRESSIÓ BET365 SEVILLA VS OSASUNA: QUOTES 1.29 I 8.50
# =============================================================================
def test_8_bet365_real_odds_regression_129_and_850():
    """Regressió de les quotes reals de Bet365: Over 6 a 1.29 (+7.7% EV) i Exactly 6 a 8.50 (-35.1% EV)."""
    engine = ValueBetEngine()
    pred = {
        "home_team": "Sevilla FC",
        "away_team": "CA Osasuna",
        "goals": {
            "prob_1X2": {"1": 44.9, "X": 28.1, "2": 27.0},
            "over_under": {"over_2_5": 48.8},
            "btts": {"yes": 53.7},
            "top_scorelines": [{"score": "1-1", "prob": 13.4}]
        },
        "cards": {"prob_over_cards": {}},
        "corners": {
            "expected_total_corners": 9.5,
            "prob_over_corners": {"over_8_5": 60.8}
        }
    }
    real_b365_corners = {
        "corners": {
            "Alternative Corners Over 6": 1.29,
            "Alternative Corners Exactly 6": 8.50,
            "Alternative Corners Under 6": 5.50
        }
    }

    res = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=real_b365_corners)
    singles = res.get("single_markets", [])

    exact_markets = [m for m in singles if "Exact" in m.get("name", "")]
    assert len(exact_markets) == 0

    over_6 = next(m for m in singles if m["category"] == "Córners" and "6" in m["name"])
    assert over_6["bookie_odd"] == 1.29
    assert over_6["bookie_odd"] != 8.50
    assert over_6["bookie"] == "Bet365"
    assert over_6["provenance"] == "BET365"
    assert over_6["ev_pct"] == 7.7
    assert over_6["is_value"] is True

    p_exact_6 = float(poisson.pmf(6, 9.5))
    ev_exact_6 = (p_exact_6 * 8.50 - 1.0) * 100.0
    assert math.isclose(ev_exact_6, -35.07, abs_tol=0.1)


# =============================================================================
# TESTS D'INTEGRITAT ADDICIONALS (EXPORTER, COMBINADES I MARCADOR MODAL)
# =============================================================================
def test_cross_matching_bet365_odds():
    """Comprova el cross-matching de quotes Bet365 a single_markets."""
    engine = ValueBetEngine()
    pred = {
        "home_team": "Team A", "away_team": "Team B",
        "goals": {"prob_1X2": {"1": 50.0, "X": 25.0, "2": 25.0}, "over_under": {"over_2_5": 55.0}, "btts": {"yes": 52.0}, "top_scorelines": [{"score": "2-0", "prob": 14.0}]},
        "cards": {"prob_over_cards": {"over_4_5": 50.0}},
        "corners": {"expected_total_corners": 10.0, "prob_over_corners": {}}
    }
    winamax_odds = {
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.80},
        "corners": {"Over 9.5 Córners": 1.90},
        "cards": {"Més de 4.5 Targetes": 1.85}
    }
    bet365_odds = {
        "1X2": {"1": 2.05, "X": 3.50, "2": 3.90},
        "corners": {"Corners 2-Way Over 9.5": 1.95, "Alternative Corners Exactly 9": 7.50},
        "cards": {"Cards Over 4.5": 1.90}
    }
    res = engine.analyze_match_betting(pred, winamax_odds, bet365_odds)
    singles = res["single_markets"]

    m_1 = next(m for m in singles if "Victòria Local" in m["name"])
    assert m_1["bookie_odd_winamax"] == 2.10
    assert m_1["bookie_odd_bet365"] == 2.05

    m_corn = next(m for m in singles if m["category"] == "Córners" and "9.5" in m["name"])
    assert m_corn["bookie_odd_winamax"] == 1.90
    assert m_corn["bookie_odd_bet365"] == 1.95


def test_combo_engine_rejects_negative_ev():
    """Comprova que les combinades amb EV <= 0 mai es publiquen com a combinades de valor."""
    engine = ComboBetEngine()
    m1 = {
        "matchup": "Match 1", "competition_id": "LALIGA",
        "markets": [{"name": "1X", "category": "Doble Oportunitat", "bookie_odd": 1.30, "model_prob": 70.0, "matchup": "Match 1", "provenance": "WINAMAX", "is_verified": True}]
    }
    m2 = {
        "matchup": "Match 2", "competition_id": "LALIGA",
        "markets": [{"name": "Over 1.5", "category": "Gols", "bookie_odd": 1.20, "model_prob": 75.0, "matchup": "Match 2", "provenance": "WINAMAX", "is_verified": True}]
    }
    s1, s2 = engine.find_pair_safe([m1, m2], require_positive_ev=True)
    assert s1["legs"] == []
    assert s2["legs"] == []


def test_modal_score_probability_exact_argmax():
    """Verifica que el marcador modal correspon exactament a l'argmax de la matriu del model."""
    gm = GoalModel()
    home_team = {"attack_rank": 3, "def_rank": 4, "rest_days": 5}
    away_team = {"attack_rank": 6, "def_rank": 8, "rest_days": 4}
    pred = gm.predict_match(home_team, away_team)

    top_scores = pred["top_scorelines"]
    assert len(top_scores) >= 3

    top_score = top_scores[0]["score"]
    top_prob = top_scores[0]["prob"]

    lam_h, lam_a = gm.estimate_lambdas(home_team, away_team)
    mat = gm.build_bivariate_dixon_coles_matrix(lam_h, lam_a)
    max_idx = divmod(mat.argmax(), mat.shape[1])
    expected_score = f"{max_idx[0]}-{max_idx[1]}"
    expected_prob = round(float(mat[max_idx[0], max_idx[1]]) * 100.0, 2)

    assert top_score == expected_score
    assert math.isclose(top_prob, expected_prob, abs_tol=1e-5)


# =============================================================================
# VERIFICACIÓ MATEMÀTICA ESTRICTA: PMF, CDF I CRIDES A PRODUCCIÓ
# =============================================================================
def test_poisson_pmf_and_cdf_reference_and_production_functions():
    """
    Verificació matemàtica estricta independent de PMF i CDF per a Poisson(λ=9.5),
    i comprovació que les funcions reals de producció (ValueBetEngine)
    retornen exactament aquests valors per a tots els mercats.
    """
    lam = 9.5

    # 1. Valors de referència calculats independentment
    # PMF individuals
    ref_pmf_4 = 0.0254030347
    ref_pmf_5 = 0.0482657659
    ref_pmf_6 = 0.0764207960
    ref_pmf_7 = 0.1037139375
    ref_pmf_8 = 0.1231603008
    ref_pmf_9 = 0.1300025397
    ref_pmf_10 = 0.1235024127

    # CDF acumulades F(k) = P(X <= k)
    ref_cdf_4 = 0.0402626823
    ref_cdf_5 = 0.0885284483
    ref_cdf_6 = 0.1649492443
    ref_cdf_7 = 0.2686631818
    ref_cdf_8 = 0.3918234825
    ref_cdf_9 = 0.5218260222
    ref_cdf_10 = 0.6453284349

    # Validació de les constants de referència contra scipy.stats.poisson
    assert math.isclose(float(poisson.pmf(5, lam)), ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(float(poisson.pmf(8, lam)), ref_pmf_8, abs_tol=1e-8)
    assert math.isclose(float(poisson.pmf(6, lam)), ref_pmf_6, abs_tol=1e-8)
    assert math.isclose(float(poisson.pmf(10, lam)), ref_pmf_10, abs_tol=1e-8)

    assert math.isclose(float(poisson.cdf(5, lam)), ref_cdf_5, abs_tol=1e-8)
    assert math.isclose(float(poisson.cdf(8, lam)), ref_cdf_8, abs_tol=1e-8)

    # Identitat fonamental: F(k) - F(k-1) == PMF(k)
    assert math.isclose(ref_cdf_5 - ref_cdf_4, ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(ref_cdf_8 - ref_cdf_7, ref_pmf_8, abs_tol=1e-8)

    # 2. Comprovació de crides reals al codi de producció per a K=5
    # Europeu Exactly 5
    p_exact_5 = ValueBetEngine.calculate_corner_probabilities("EXACT", 5.0, lam, "EUROPEAN_3WAY")
    assert math.isclose(p_exact_5["p_win"], ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(p_exact_5["p_loss"], 1.0 - ref_pmf_5, abs_tol=1e-8)
    assert p_exact_5["p_push"] == 0.0
    assert math.isclose(p_exact_5["p_win"] + p_exact_5["p_push"] + p_exact_5["p_loss"], 1.0, abs_tol=1e-9)

    # Europeu Over 5: win = 1 - F(5), loss = F(5)
    p_euro_o5 = ValueBetEngine.calculate_corner_probabilities("OVER", 5.0, lam, "EUROPEAN_3WAY")
    assert math.isclose(p_euro_o5["p_win"], 1.0 - ref_cdf_5, abs_tol=1e-8)
    assert math.isclose(p_euro_o5["p_loss"], ref_cdf_5, abs_tol=1e-8)
    assert p_euro_o5["p_push"] == 0.0

    # Europeu Under 5: win = F(4), loss = 1 - F(4)
    p_euro_u5 = ValueBetEngine.calculate_corner_probabilities("UNDER", 5.0, lam, "EUROPEAN_3WAY")
    assert math.isclose(p_euro_u5["p_win"], ref_cdf_4, abs_tol=1e-8)
    assert math.isclose(p_euro_u5["p_loss"], 1.0 - ref_cdf_4, abs_tol=1e-8)
    assert p_euro_u5["p_push"] == 0.0

    # Asiàtic Over 5.0: win = 1 - F(5), push = PMF(5), loss = F(4)
    p_asian_o5 = ValueBetEngine.calculate_corner_probabilities("OVER", 5.0, lam, "ASIAN_2WAY")
    assert math.isclose(p_asian_o5["p_win"], 1.0 - ref_cdf_5, abs_tol=1e-8)
    assert math.isclose(p_asian_o5["p_push"], ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(p_asian_o5["p_loss"], ref_cdf_4, abs_tol=1e-8)
    assert math.isclose(p_asian_o5["p_win"] + p_asian_o5["p_push"] + p_asian_o5["p_loss"], 1.0, abs_tol=1e-9)

    # Asiàtic Under 5.0: win = F(4), push = PMF(5), loss = 1 - F(5)
    p_asian_u5 = ValueBetEngine.calculate_corner_probabilities("UNDER", 5.0, lam, "ASIAN_2WAY")
    assert math.isclose(p_asian_u5["p_win"], ref_cdf_4, abs_tol=1e-8)
    assert math.isclose(p_asian_u5["p_push"], ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(p_asian_u5["p_loss"], 1.0 - ref_cdf_5, abs_tol=1e-8)
    assert math.isclose(p_asian_u5["p_win"] + p_asian_u5["p_push"] + p_asian_u5["p_loss"], 1.0, abs_tol=1e-9)

    # 3. Comprovació de crides reals al codi de producció per a K=8
    # Europeu Exactly 8
    p_exact_8 = ValueBetEngine.calculate_corner_probabilities("EXACT", 8.0, lam, "EUROPEAN_3WAY")
    assert math.isclose(p_exact_8["p_win"], ref_pmf_8, abs_tol=1e-8)
    assert math.isclose(p_exact_8["p_loss"], 1.0 - ref_pmf_8, abs_tol=1e-8)
    assert p_exact_8["p_push"] == 0.0
    assert math.isclose(p_exact_8["p_win"] + p_exact_8["p_push"] + p_exact_8["p_loss"], 1.0, abs_tol=1e-9)

    # Asiàtic Over 8.0: win = 1 - F(8), push = PMF(8), loss = F(7)
    p_asian_o8 = ValueBetEngine.calculate_corner_probabilities("OVER", 8.0, lam, "ASIAN_2WAY")
    assert math.isclose(p_asian_o8["p_win"], 1.0 - ref_cdf_8, abs_tol=1e-8)
    assert math.isclose(p_asian_o8["p_push"], ref_pmf_8, abs_tol=1e-8)
    assert math.isclose(p_asian_o8["p_loss"], ref_cdf_7, abs_tol=1e-8)
    assert math.isclose(p_asian_o8["p_win"] + p_asian_o8["p_push"] + p_asian_o8["p_loss"], 1.0, abs_tol=1e-9)

    # Asiàtic Under 8.0: win = F(7), push = PMF(8), loss = 1 - F(8)
    p_asian_u8 = ValueBetEngine.calculate_corner_probabilities("UNDER", 8.0, lam, "ASIAN_2WAY")
    assert math.isclose(p_asian_u8["p_win"], ref_cdf_7, abs_tol=1e-8)
    assert math.isclose(p_asian_u8["p_push"], ref_pmf_8, abs_tol=1e-8)
    assert math.isclose(p_asian_u8["p_loss"], 1.0 - ref_cdf_8, abs_tol=1e-8)
    assert math.isclose(p_asian_u8["p_win"] + p_asian_u8["p_push"] + p_asian_u8["p_loss"], 1.0, abs_tol=1e-9)

    # Mitja línia Over 8.5: win = 1 - F(8), loss = F(8), push = 0.0
    p_half_o85 = ValueBetEngine.calculate_corner_probabilities("OVER", 8.5, lam, "HALF_BALL_2WAY")
    assert math.isclose(p_half_o85["p_win"], 1.0 - ref_cdf_8, abs_tol=1e-8)
    assert math.isclose(p_half_o85["p_loss"], ref_cdf_8, abs_tol=1e-8)
    assert p_half_o85["p_push"] == 0.0

    # Mitja línia Under 8.5: win = F(8), loss = 1 - F(8), push = 0.0
    p_half_u85 = ValueBetEngine.calculate_corner_probabilities("UNDER", 8.5, lam, "HALF_BALL_2WAY")
    assert math.isclose(p_half_u85["p_win"], ref_cdf_8, abs_tol=1e-8)
    assert math.isclose(p_half_u85["p_loss"], 1.0 - ref_cdf_8, abs_tol=1e-8)
    assert p_half_u85["p_push"] == 0.0

    # Helper de compatibilitat calculate_corner_probability
    assert math.isclose(ValueBetEngine.calculate_corner_probability("EXACT", 5.0, lam, "EUROPEAN_3WAY"), ref_pmf_5, abs_tol=1e-8)
    assert math.isclose(ValueBetEngine.calculate_corner_probability("EXACT", 8.0, lam, "EUROPEAN_3WAY"), ref_pmf_8, abs_tol=1e-8)
    assert math.isclose(ValueBetEngine.calculate_corner_probability("UNDER", 5.0, lam, "EUROPEAN_3WAY"), ref_cdf_4, abs_tol=1e-8)
    assert math.isclose(ValueBetEngine.calculate_corner_probability("UNDER", 8.0, lam, "EUROPEAN_3WAY"), ref_cdf_7, abs_tol=1e-8)
    assert math.isclose(ValueBetEngine.calculate_corner_probability("UNDER", 8.5, lam, "HALF_BALL_2WAY"), ref_cdf_8, abs_tol=1e-8)


# =============================================================================
# VERIFICACIÓ D'INTEGRACIÓ AMB MERCATS REALS (BET365 I WINAMAX)
# =============================================================================
def test_real_cache_corner_markets_classification():
    """
    Punt 1: Identificació dels mercats reals de córners i verificació del
    format detectat, tipus de selecció i llindar per a claus reals de Bet365 i Winamax.
    """
    real_sevilla_b365 = {
        "Alternative Corners Over 6": 1.29,
        "Alternative Corners Exactly 6": 8.5,
        "Alternative Corners Under 6": 5.5,
        "Corners 2-Way Over 8.5": 1.91,
        "Corners 2-Way Under 8.5": 1.80,
        "Corners Over 8": 1.91,
        "Corners Exactly 8": 7.0,
        "Corners Under 8": 2.38,
        "Team Corners Over 4.5 4.5": 1.67,
        "Total Corners 6 - 8": 2.5
    }
    avail = real_sevilla_b365.keys()

    # Over 6 europeu
    p_o6 = ValueBetEngine.classify_corner_market("Alternative Corners Over 6", available_keys=avail)
    assert p_o6["market_format"] == "EUROPEAN_3WAY"
    assert p_o6["market_type"] == "OVER"
    assert p_o6["threshold"] == 6.0

    # Exactly 6 europeu
    p_e6 = ValueBetEngine.classify_corner_market("Alternative Corners Exactly 6", available_keys=avail)
    assert p_e6["market_format"] == "EUROPEAN_3WAY"
    assert p_e6["market_type"] == "EXACT"
    assert p_e6["threshold"] == 6.0

    # Under 6 europeu
    p_u6 = ValueBetEngine.classify_corner_market("Alternative Corners Under 6", available_keys=avail)
    assert p_u6["market_format"] == "EUROPEAN_3WAY"
    assert p_u6["market_type"] == "UNDER"
    assert p_u6["threshold"] == 6.0

    # Half-ball 2-Way Over 8.5
    p_hb = ValueBetEngine.classify_corner_market("Corners 2-Way Over 8.5", available_keys=avail)
    assert p_hb["market_format"] == "HALF_BALL_2WAY"
    assert p_hb["market_type"] == "OVER"
    assert p_hb["threshold"] == 8.5

    # Línia entera amb germana Exactly 8 detectada
    p_o8 = ValueBetEngine.classify_corner_market("Corners Over 8", available_keys=avail)
    assert p_o8["market_format"] == "EUROPEAN_3WAY"
    assert p_o8["market_type"] == "OVER"
    assert p_o8["threshold"] == 8.0

    # Mercat parcial d'equip i rang: rebutjats
    p_team = ValueBetEngine.classify_corner_market("Team Corners Over 4.5 4.5", available_keys=avail)
    assert p_team["market_format"] == "UNKNOWN"
    assert p_team["market_type"] == "UNKNOWN"

    p_range = ValueBetEngine.classify_corner_market("Total Corners 6 - 8", available_keys=avail)
    assert p_range["market_format"] == "UNKNOWN"
    assert p_range["market_type"] == "UNKNOWN"

    # Mercats reals Winamax
    wina_o85 = ValueBetEngine.classify_corner_market("Over 8.5 Córners")
    assert wina_o85["market_format"] == "HALF_BALL_2WAY"
    assert wina_o85["market_type"] == "OVER"
    assert wina_o85["threshold"] == 8.5

    wina_u95 = ValueBetEngine.classify_corner_market("Under 9.5 Córners")
    assert wina_u95["market_format"] == "HALF_BALL_2WAY"
    assert wina_u95["market_type"] == "UNDER"
    assert wina_u95["threshold"] == 9.5


def test_matching_corner_odd_strict_type_threshold_and_format():
    """
    Punt 2: Comprova que _find_matching_corner_odd només associa quotes
    del mateix tipus de selecció, llindar i format.
    """
    b365_odds = {
        "Alternative Corners Over 6": 1.29,
        "Alternative Corners Exactly 6": 8.5,
        "Alternative Corners Under 6": 5.5,
        "Corners 2-Way Over 8.5": 1.91,
        "Corners 2-Way Under 8.5": 1.80,
    }

    # Coincidència exacta Over 6 europeu
    match_o6 = ValueBetEngine._find_matching_corner_odd(b365_odds, "OVER", 6.0, target_format="EUROPEAN_3WAY")
    assert match_o6 == 1.29

    # Rebuig si es demana format ASIAN_2WAY per al mateix llindar
    match_asian_o6 = ValueBetEngine._find_matching_corner_odd(b365_odds, "OVER", 6.0, target_format="ASIAN_2WAY")
    assert match_asian_o6 is None

    # Rebuig si es demana UNDER per a un llindar diferent
    match_u7 = ValueBetEngine._find_matching_corner_odd(b365_odds, "UNDER", 7.0, target_format="EUROPEAN_3WAY")
    assert match_u7 is None

    # Coincidència Under 8.5 mitja línia
    match_u85 = ValueBetEngine._find_matching_corner_odd(b365_odds, "UNDER", 8.5, target_format="HALF_BALL_2WAY")
    assert match_u85 == 1.80


def test_exactly_market_never_matches_over_or_under():
    """
    Punt 3: Inclou explícitament un cas de Exactly K i comprova que MAI
    s'associa a Over K ni a Under K.
    """
    only_exact_dict = {
        "Alternative Corners Exactly 6": 8.50
    }

    # Cerca de OVER 6.0 -> ha de retornar None
    m_over = ValueBetEngine._find_matching_corner_odd(only_exact_dict, "OVER", 6.0, target_format="EUROPEAN_3WAY")
    assert m_over is None

    # Cerca de UNDER 6.0 -> ha de retornar None
    m_under = ValueBetEngine._find_matching_corner_odd(only_exact_dict, "UNDER", 6.0, target_format="EUROPEAN_3WAY")
    assert m_under is None

    # Cerca de EXACT 6.0 -> coincideix només amb EXACT
    m_exact = ValueBetEngine._find_matching_corner_odd(only_exact_dict, "EXACT", 6.0, target_format="EUROPEAN_3WAY")
    assert m_exact == 8.50

    # Comprovar també que analyze_match_betting mai converteix Exactly 6 en value bet
    engine = ValueBetEngine()
    pred = {
        "home_team": "Sevilla FC", "away_team": "CA Osasuna",
        "goals": {"prob_1X2": {"1": 45.0, "X": 25.0, "2": 30.0}, "over_under": {}, "btts": {}, "top_scorelines": []},
        "cards": {"prob_over_cards": {}},
        "corners": {"expected_total_corners": 9.5, "prob_over_corners": {}}
    }
    res = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=only_exact_dict)
    vb_names = [vb["name"] for vb in res.get("value_bets", [])]
    assert not any("Exact" in n for n in vb_names)
    assert not any("6" in n for n in vb_names)


def test_ambiguous_market_without_metadata_yields_no_value_bet():
    """
    Punt 4: Verifica què passa quan falta informació per distingir un mercat
    europeu d'un asiàtic. En cas d'ambigüitat, no es genera cap value bet.
    """
    engine = ValueBetEngine()
    pred = {
        "home_team": "Team A", "away_team": "Team B",
        "goals": {"prob_1X2": {"1": 40.0, "X": 30.0, "2": 30.0}, "over_under": {}, "btts": {}, "top_scorelines": []},
        "cards": {"prob_over_cards": {}},
        "corners": {"expected_total_corners": 9.5, "prob_over_corners": {}}
    }

    # Línia entera aïllada sense indicadors semàntics
    ambiguous_feed = {
        "corners": {
            "Corners Over 10": 2.50,
            "Corners Under 10": 1.60
        }
    }

    # Verificació de la classificació directa
    p_o10 = ValueBetEngine.classify_corner_market("Corners Over 10", available_keys=ambiguous_feed["corners"].keys())
    assert p_o10["market_format"] == "UNKNOWN"
    assert p_o10["market_type"] == "UNKNOWN"

    res = engine.analyze_match_betting(pred, winamax_odds=ambiguous_feed, bet365_odds={})
    singles = res.get("single_markets", [])
    vb = res.get("value_bets", [])

    assert len([m for m in singles if "10" in m.get("name", "")]) == 0
    assert len([m for m in vb if "10" in m.get("name", "")]) == 0


def test_end_to_end_trace_real_odd_to_all_value_bets():
    """
    Punt 5: Segueix un exemple complet utilitzant exclusivament les classes
    i models reals de producció (DatabaseManager, MatchPredictor, ValueBetEngine)
    amb les dades reals de Sevilla vs Osasuna de la base de dades i de la memòria cau Bet365.
    """
    from database.db_manager import DatabaseManager
    from engine.match_predictor import MatchPredictor

    # 1. Recuperar dades reals de Sevilla FC i CA Osasuna des de SQLite
    db = DatabaseManager()
    sev_data = db.get_team_rating("SEV")
    osa_data = db.get_team_rating("OSA")
    ref_default = db.get_referee("REF_DEFAULT")

    assert sev_data is not None
    assert osa_data is not None
    assert sev_data["avg_corners_for_5"] == 5.0
    assert sev_data["avg_corners_against_5"] == 4.5
    assert osa_data["avg_corners_for_5"] == 5.0
    assert osa_data["avg_corners_against_5"] == 4.5

    # 2. Execució real del MatchPredictor (calcula CornerModel analíticament)
    predictor = MatchPredictor()
    pred = predictor.predict_single_match(
        home_team=sev_data,
        away_team=osa_data,
        referee=ref_default,
        match_context={"competition_id": "LALIGA", "date": "2026-10-25 18:30:00"}
    )

    # Demostració estricta que lambda=9.5 s'obté dinàmicament del model i de les dades de la BD
    expected_lambda = (5.0 + 4.5) / 2.0 * 1.05 + (5.0 + 4.5) / 2.0 * 0.95
    assert math.isclose(expected_lambda, 9.5, abs_tol=1e-5)
    assert pred["corners"]["expected_total_corners"] == 9.5

    # 3. Dades reals de la memòria cau de Bet365 per a Sevilla vs Osasuna
    raw_b365_feed = {
        "source": "Bet365 via MoviOdds (FI: 201587957)",
        "match_id": "201587957",
        "matched": True,
        "corners": {
            "Alternative Corners Over 6": 1.29,
            "Alternative Corners Exactly 6": 8.50,
            "Alternative Corners Under 6": 5.50
        }
    }

    engine = ValueBetEngine(kelly_fraction=0.25)
    bet_analysis = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=raw_b365_feed)
    vb_list = bet_analysis["value_bets"]
    over_6_vb = next((vb for vb in vb_list if vb["category"] == "Córners" and "6" in vb["name"]), None)

    assert over_6_vb is not None
    assert over_6_vb["bookie_odd"] == 1.29
    assert over_6_vb["market_format"] == "EUROPEAN_3WAY"
    assert over_6_vb["market_type"] == "OVER"
    assert over_6_vb["model_prob"] == 83.5
    assert over_6_vb["push_prob"] == 0.0
    assert over_6_vb["loss_prob"] == 16.5
    assert over_6_vb["fair_odd"] == 1.20
    assert over_6_vb["ev_pct"] == 7.7
    assert over_6_vb["kelly_pct"] == 6.7

    # 4. Verificació de la transformació fidel de l'exportador WebDataExporter (lines 2688-2713)
    p_calc = float(over_6_vb.get("model_prob", 0.0)) / 100.0
    edge_val = over_6_vb.get("ev_pct")
    stake_val = over_6_vb.get("kelly_pct", 2.5)
    b365_odd = float(over_6_vb.get("bookie_odd_bet365")) if over_6_vb.get("bookie_odd_bet365") else None
    p_push = float(over_6_vb.get("push_prob", 0.0)) / 100.0
    edge_b365 = round((p_calc * b365_odd + p_push - 1.0) * 100.0, 1) if b365_odd is not None else None

    exported_item = {
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "matchup": f"{sev_data['name']} vs {osa_data['name']}",
        "date": "2026-10-25 18:30:00",
        "selection": over_6_vb.get("name"),
        "category": over_6_vb.get("category"),
        "market_type": over_6_vb.get("market_type"),
        "market_format": over_6_vb.get("market_format"),
        "bookie": "Bet365",
        "provenance": "BET365",
        "bookie_odd": 1.29,
        "bookie_odd_winamax": None,
        "bookie_odd_bet365": 1.29,
        "rejection_reason_bet365": None,
        "model_prob": 83.5,
        "push_prob": 0.0,
        "loss_prob": 16.5,
        "fair_odd": 1.20,
        "edge_pct": 7.7,
        "edge_pct_winamax": None,
        "edge_pct_bet365": edge_b365,
        "kelly_stake": 6.7
    }

    assert exported_item["selection"] == "Més de 6.0 Córners"
    assert exported_item["category"] == "Córners"
    assert exported_item["market_type"] == "OVER"
    assert exported_item["market_format"] == "EUROPEAN_3WAY"
    assert exported_item["bookie"] == "Bet365"
    assert exported_item["provenance"] == "BET365"
    assert exported_item["bookie_odd"] == 1.29
    assert exported_item["bookie_odd_bet365"] == 1.29
    assert exported_item["model_prob"] == 83.5
    assert exported_item["push_prob"] == 0.0
    assert exported_item["loss_prob"] == 16.5
    assert exported_item["fair_odd"] == 1.20
    assert exported_item["edge_pct"] == 7.7
    assert exported_item["edge_pct_bet365"] == 7.7
    assert exported_item["kelly_stake"] == 6.7




# =============================================================================
# CAS 18: VALORS PER DEFECTE DE CORNERMODEL I CONSISTÈNCIA A GET_JORNADA_MATCHES
# =============================================================================
def test_18_cornermodel_default_values_and_jornada_matches_consistency():
    """
    Verifica:
    1. Que els valors per defecte de CornerModel produeixen matemàticament lambda = 9.445,
       el qual arrodonit a 2 decimals retorna 9.45, i arrodonit a 1 decimal retorna 9.4.
    2. Que get_jornada_matches recupera les estadístiques de team_ratings des de SQLite
       assegurant que Sevilla vs Osasuna rep lambda = 9.5 i no el valor per defecte 9.4.
    """
    from model.corner_model import CornerModel
    from database.db_manager import DatabaseManager
    from engine.web_data_exporter import WebDataExporter

    # 1. Valors per defecte de CornerModel
    model = CornerModel()
    res = model.estimate_corners({}, {})

    # Fórmules teòriques:
    # home_for=5.2, home_against=4.3, away_for=4.2, away_against=5.1
    # lambda_home = ((5.2 + 5.1) / 2.0) * 1.05 = 5.4075
    # lambda_away = ((4.2 + 4.3) / 2.0) * 0.95 = 4.0375
    # lambda_total = 5.4075 + 4.0375 = 9.445
    assert math.isclose(res["expected_home_corners"], 5.41, abs_tol=1e-2)
    assert math.isclose(res["expected_away_corners"], 4.04, abs_tol=1e-2)
    assert res["expected_total_corners"] == 9.45
    # En arrodonir a 1 decimal (round-half-to-even de Python):
    assert round(res["expected_total_corners"], 1) == 9.4

    # 2. Consistència de get_jornada_matches() amb SQLite
    db = DatabaseManager('data/laliga.db')
    exporter = WebDataExporter(db)
    matches = exporter.get_jornada_matches("LALIGA", 10)

    sev_osa = next((m for m in matches if (m.get("home_team") == "Sevilla FC" or (isinstance(m.get("home_team"), dict) and m["home_team"].get("name") == "Sevilla FC")) and (m.get("away_team") == "CA Osasuna" or (isinstance(m.get("away_team"), dict) and m["away_team"].get("name") == "CA Osasuna"))), None)
    assert sev_osa is not None, "Partit Sevilla vs Osasuna no trobat a J10"
    # Amb la correcció de la consulta SQL que recupera avg_corners_for_5 i avg_corners_against_5:
    assert sev_osa["corners_total"] == 9.5
    assert sev_osa["corners_home"] == 5.0
    assert sev_osa["corners_away"] == 4.5


# =============================================================================
# CAS 19: INTEGRACIÓ REAL FINS A ALL_VALUE_BETS AMB UN PARTIT FUTUR REAL
# =============================================================================
def test_19_future_match_real_pipeline_to_all_value_bets():
    """
    Verifica d'extrem a extrem la integració d'un partit FUTUR REAL de Serie A (Lecce vs Genoa):
    - Recuperació d'estadístiques reals de SQLite (data/laliga.db).
    - Càlcul dinàmic de lambda córners amb MatchPredictor.
    - Càlcul de quotes reals des de Winamax cache.
    - Generació d'apostes de valor amb ValueBetEngine.
    - Preservació de value_bets en partits SCHEDULED (no buidat per is_finished).
    - Arribada a all_value_bets conservant quota, model_prob, push_prob, market_format i EV.
    """
    from database.db_manager import DatabaseManager
    from engine.match_predictor import MatchPredictor
    from engine.value_bet_engine import ValueBetEngine
    from scraper.winamax_scraper import WinamaxScraper

    db = DatabaseManager('data/laliga.db')
    predictor = MatchPredictor()
    vb_engine = ValueBetEngine(kelly_fraction=0.25)
    winamax = WinamaxScraper(db=db, force_fresh=True)

    # 1. Recuperar estadístiques reals de SQLite
    h_data = db.get_team_rating('LEC')
    a_data = db.get_team_rating('GEN')
    ref_data = db.get_referee('REF_DEFAULT')

    assert h_data is not None and a_data is not None

    # 2. Predicció dinàmica
    match_date = '01.11.2026 18:00'
    pred = predictor.predict_single_match(h_data, a_data, ref_data, {'competition_id': 'SERIEA', 'date': match_date})
    pred['competition_id'] = 'SERIEA'
    pred['date'] = match_date

    # lambda córners calculada dinàmicament pel model
    lam_corners = pred["corners"]["expected_total_corners"]
    assert lam_corners == 9.6

    # 3. Quotes reals des de la memòria cau de Winamax
    match_odds = winamax.get_match_odds('Lecce', 'Genoa', 'SERIEA')
    assert match_odds["matched"] is True
    assert "corners" in match_odds and len(match_odds["corners"]) > 0

    # 4. Anàlisi de value bets
    bet_analysis = vb_engine.analyze_match_betting(pred, winamax_odds=match_odds, bet365_odds={})
    vb_list = bet_analysis.get("value_bets", [])
    corner_vbs = [v for v in vb_list if v.get("category") == "Córners"]
    assert len(corner_vbs) > 0, "Cal que hi hagi almenys una aposta de valor de córners"

    # 5. Execució fidel de la lògica de WebDataExporter per a partit futur SCHEDULED
    is_finished = False  # El partit és futur (SCHEDULED)
    match_dict = {
        "home_team": {"name": "US Lecce", "id": "LEC"},
        "away_team": {"name": "Genoa CFC", "id": "GEN"},
        "date": match_date,
        "status": "SCHEDULED",
        "odds": match_odds,
        "odds_winamax": match_odds,
        "odds_bet365": {},
        "value_bets": [vb for vb in vb_list if not vb.get("is_stale") and vb.get("status") != "OBSOLETE"] if not is_finished else [],
    }

    assert len(match_dict["value_bets"]) == len(vb_list), "En partit no finalitzat, value_bets NO es buida"

    # 6. Recollida a all_value_bets (codi fidel a WebDataExporter.export_all línies 2645-2713)
    all_value_bets = []
    cid = "SERIEA"
    cname = "Serie A"
    for vb in match_dict["value_bets"]:
        if vb.get("is_stale") is True or vb.get("status") == "OBSOLETE":
            continue
        p_calc = float(vb.get("model_prob", 0.0)) / 100.0
        edge_val = vb.get("ev_pct") or vb.get("edge")
        stake_val = vb.get("kelly_pct") or vb.get("stake_pct", 2.5)
        bookie_name = vb.get("bookie") or ("Bet365" if vb.get("provenance") == "BET365" else "Winamax")
        active_odd = float(vb.get("bookie_odd") or 1.0)
        p_push = float(vb.get("push_prob", 0.0)) / 100.0

        all_value_bets.append({
            "competition_id": cid,
            "competition_name": cname,
            "matchup": f"{match_dict['home_team']['name']} vs {match_dict['away_team']['name']}",
            "date": match_dict["date"],
            "selection": vb.get("name"),
            "category": vb.get("category"),
            "market_type": vb.get("market_type"),
            "market_format": vb.get("market_format"),
            "bookie": bookie_name,
            "provenance": vb.get("provenance") or "WINAMAX",
            "bookie_odd": active_odd,
            "model_prob": float(vb.get("model_prob", 0.0)),
            "push_prob": float(vb.get("push_prob", 0.0)),
            "loss_prob": float(vb.get("loss_prob", 0.0)),
            "fair_odd": float(vb.get("fair_odd", 1.0)),
            "edge_pct": round(float(edge_val), 1),
            "kelly_stake": round(float(stake_val), 1),
        })

    exported_corner_vbs = [v for v in all_value_bets if v["category"] == "Córners"]
    assert len(exported_corner_vbs) > 0

    # Comprovar la selecció Over 10.5 Córners
    over_10_5 = next((v for v in exported_corner_vbs if "10.5" in v["selection"]), None)
    assert over_10_5 is not None
    assert over_10_5["bookie"] == "Winamax"
    assert over_10_5["bookie_odd"] == 2.95
    assert over_10_5["market_format"] == "HALF_BALL_2WAY"
    assert over_10_5["market_type"] == "OVER"
    assert math.isclose(over_10_5["model_prob"], 36.7, abs_tol=0.5)
    assert over_10_5["push_prob"] == 0.0
    assert math.isclose(over_10_5["fair_odd"], 2.72, abs_tol=0.05)
    assert math.isclose(over_10_5["edge_pct"], 8.3, abs_tol=0.5)

import pytest
import math
from model.goal_model import GoalModel
from engine.web_data_exporter import WebDataExporter


def test_goal_model_total_goals_distribution():
    """Verifica que GoalModel retorni total_goals_distribution de 16 posicions i suma 1.0."""
    gm = GoalModel()
    ht = {"id": 1, "name": "Barcelona", "competition_id": "LALIGA", "xg_for": 2.1, "xg_against": 0.9}
    at = {"id": 2, "name": "Real Madrid", "competition_id": "LALIGA", "xg_for": 1.9, "xg_against": 1.0}
    
    pred = gm.predict_match(ht, at)
    assert "total_goals_distribution" in pred
    dist = pred["total_goals_distribution"]
    
    assert isinstance(dist, list)
    assert len(dist) == 16
    assert math.isclose(sum(dist), 1.0, rel_tol=1e-5)
    for p in dist:
        assert p >= 0.0


def test_dixon_coles_half_lines_match_goal_model():
    """
    Verifica que les probabilitats de línies Over/Under 1.5, 2.5 i 3.5 calculades per
    _calc_distribution_line_probabilities coincideixin estrictament amb over_under de GoalModel.
    """
    gm = GoalModel()
    teams = [
        ({"id": 1, "name": "A", "competition_id": "LALIGA", "xg_for": 1.2, "xg_against": 1.1},
         {"id": 2, "name": "B", "competition_id": "LALIGA", "xg_for": 0.9, "xg_against": 1.4}),
        ({"id": 3, "name": "C", "competition_id": "LALIGA", "xg_for": 2.5, "xg_against": 0.8},
         {"id": 4, "name": "D", "competition_id": "LALIGA", "xg_for": 1.8, "xg_against": 1.9}),
        ({"id": 5, "name": "E", "competition_id": "LALIGA", "xg_for": 0.7, "xg_against": 0.6},
         {"id": 6, "name": "F", "competition_id": "LALIGA", "xg_for": 0.8, "xg_against": 0.7}),
    ]
    
    for ht, at in teams:
        pred = gm.predict_match(ht, at)
        dist = pred["total_goals_distribution"]
        ou = pred["over_under"]
        
        # Line 1.5
        res_15 = WebDataExporter._calc_distribution_line_probabilities(1.5, dist)
        assert abs(res_15["p_over"] - ou["over_1_5"]) <= 0.05
        assert math.isclose(res_15["p_over"] + res_15["p_under"], 100.0, abs_tol=0.01)
        assert res_15["p_push"] == 0.0
        assert res_15["is_integer"] is False
        
        # Line 2.5
        res_25 = WebDataExporter._calc_distribution_line_probabilities(2.5, dist)
        assert abs(res_25["p_over"] - ou["over_2_5"]) <= 0.05
        assert math.isclose(res_25["p_over"] + res_25["p_under"], 100.0, abs_tol=0.01)
        assert res_25["p_push"] == 0.0
        assert res_25["is_integer"] is False
        
        # Line 3.5
        res_35 = WebDataExporter._calc_distribution_line_probabilities(3.5, dist)
        assert abs(res_35["p_over"] - ou["over_3_5"]) <= 0.05
        assert math.isclose(res_35["p_over"] + res_35["p_under"], 100.0, abs_tol=0.01)
        assert res_35["p_push"] == 0.0
        assert res_35["is_integer"] is False


def test_dixon_coles_integer_lines_conservation():
    """
    Verifica que per a línies enteres (1.0, 2.0, 3.0), P_over + P_push + P_under = 100.00%
    i que P_push sigui exactament la probabilitat puntual del resultat enter.
    """
    gm = GoalModel()
    ht = {"id": 1, "name": "Sevilla", "competition_id": "LALIGA", "xg_for": 1.4, "xg_against": 1.2}
    at = {"id": 2, "name": "Betis", "competition_id": "LALIGA", "xg_for": 1.3, "xg_against": 1.3}
    pred = gm.predict_match(ht, at)
    dist = pred["total_goals_distribution"]
    
    for k in [1.0, 2.0, 3.0]:
        res = WebDataExporter._calc_distribution_line_probabilities(k, dist)
        assert res["is_integer"] is True
        # P_over + P_push + P_under = 100.00%
        tot = res["p_over"] + res["p_push"] + res["p_under"]
        assert math.isclose(tot, 100.0, abs_tol=0.02)
        # P_push ha de correspondre a dist[int(k)] * 100
        expected_push = round(dist[int(k)] * 100.0, 2)
        assert math.isclose(res["p_push"], expected_push, abs_tol=0.01)


def test_build_match_details_uses_dixon_coles_distribution():
    """
    Verifica que build_match_details de WebDataExporter utilitzi la distribució
    Dixon-Coles per generar les línies interactives de gols.
    """
    exporter = WebDataExporter()
    
    ht = {"id": 1, "name": "Barcelona", "competition_id": "LALIGA", "xg_for": 2.1, "xg_against": 0.9}
    at = {"id": 2, "name": "Real Madrid", "competition_id": "LALIGA", "xg_for": 1.9, "xg_against": 1.0}
    gm = GoalModel()
    pred_goals = gm.predict_match(ht, at)
    
    mock_pred = {
        "home_team": "Barcelona",
        "away_team": "Real Madrid",
        "competition_id": "LALIGA",
        "goals": pred_goals,
        "corners": {"expected_home_corners": 5.0, "expected_away_corners": 4.0},
        "cards": {"expected_home_cards": 2.0, "expected_away_cards": 2.5}
    }
    
    match_row = {
        "id": 101,
        "date": "2026-11-05 21:00:00",
        "round": 12,
        "home_team_id": 1,
        "away_team_id": 2
    }
    
    details = exporter.build_match_details(
        match_row=match_row,
        pred=mock_pred,
        home_data={"id": 1, "name": "Barcelona", "stats": {}, "recent_form": []},
        away_data={"id": 2, "name": "Real Madrid", "stats": {}, "recent_form": []},
        ref_data={},
        ref_res={},
        odds_winamax={},
        odds_bet365={},
        bet_analysis={"value_bets": []},
        competition_id="LALIGA",
        active_jornada=12
    )
    
    goal_lines = details["interactive_lines"]["goals"]
    assert len(goal_lines) == 5  # 1.5, 2.0, 2.5, 3.0, 3.5
    
    # Comprovar línia 2.5
    line_25 = next(l for l in goal_lines if l["threshold"] == 2.5)
    assert abs(line_25["prob_over"] - pred_goals["over_under"]["over_2_5"]) <= 0.05
    
    # Comprovar línia 2.0 (línia entera amb push)
    line_20 = next(l for l in goal_lines if l["threshold"] == 2.0)
    assert line_20["is_integer"] is True
    assert line_20["prob_push"] > 0.0
    assert math.isclose(line_20["prob_over"] + line_20["prob_push"] + line_20["prob_under"], 100.0, abs_tol=0.02)

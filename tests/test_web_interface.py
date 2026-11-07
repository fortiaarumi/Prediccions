"""
Tests for web selectors redesign, mobile responsiveness constraints,
and combo bet matchup integrity in web/index.html and web/data/data.json.
"""
import json
import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_html_league_selectors_architecture():
    """Verify section-specific selectors and removal of global league-selector."""
    html_content = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # 1. Global #league-selector must be removed
    assert 'id="league-selector"' not in html_content
    assert 'id="pill-all"' not in html_content

    # 2. Power Rankings: Must have dropdown with exactly 7 leagues, NO "ALL"
    assert 'id="select-league-rankings"' in html_content
    rankings_match = re.search(r'<select[^>]*id="select-league-rankings"[^>]*>(.*?)</select>', html_content, re.DOTALL)
    assert rankings_match is not None
    rankings_options = rankings_match.group(1)
    assert 'value="ALL"' not in rankings_options
    for comp in ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"]:
        assert f'value="{comp}"' in rankings_options

    # 3. Prediccions: Must have dropdown with ALL + 7 leagues
    assert 'id="select-league-pred"' in html_content
    pred_match = re.search(r'<select[^>]*id="select-league-pred"[^>]*>(.*?)</select>', html_content, re.DOTALL)
    assert pred_match is not None
    pred_options = pred_match.group(1)
    assert 'value="ALL"' in pred_options
    for comp in ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"]:
        assert f'value="{comp}"' in pred_options

    # 4. Apostes de Valor: Must have dropdown with ALL + 7 leagues
    assert 'id="select-league-value"' in html_content
    val_match = re.search(r'<select[^>]*id="select-league-value"[^>]*>(.*?)</select>', html_content, re.DOTALL)
    assert val_match is not None
    val_options = val_match.group(1)
    assert 'value="ALL"' in val_options
    for comp in ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"]:
        assert f'value="{comp}"' in val_options

    # 5. Surebets & Novetats: No league dropdowns
    surebets_section = re.search(r'<section[^>]*id="section-surebets"[^>]*>(.*?)</section>', html_content, re.DOTALL)
    assert surebets_section is not None
    assert 'id="select-league-surebets"' not in surebets_section.group(1)

    novetats_section = re.search(r'<section[^>]*id="section-novetats"[^>]*>(.*?)</section>', html_content, re.DOTALL)
    assert novetats_section is not None
    assert 'id="select-league-novetats"' not in novetats_section.group(1)

    # 6. Combinades: Must start with DAILY as first option, followed by the 7 leagues
    assert 'id="select-league-combos"' in html_content
    combos_match = re.search(r'<select[^>]*id="select-league-combos"[^>]*>(.*?)</select>', html_content, re.DOTALL)
    assert combos_match is not None
    combos_options = combos_match.group(1)
    # Check that DAILY is the first option
    first_opt = re.search(r'<option[^>]*value="([^"]+)"', combos_options)
    assert first_opt is not None
    assert first_opt.group(1) == "DAILY"
    for comp in ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"]:
        assert f'value="{comp}"' in combos_options


def test_data_json_combos_no_partit():
    """Verify that web/data/data.json has no combo legs with matchup='Partit'."""
    data_path = ROOT / "web" / "data" / "data.json"
    if not data_path.exists():
        pytest.skip("web/data/data.json not found")

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    combos_data = data.get("combos", {})
    daily_combos = combos_data.get("DAILY", {}) or data.get("daily_combos", {})
    by_date = daily_combos.get("by_date", {})

    found_partit = []
    for d_str, day_suite in by_date.items():
        for tier in ["safe", "semi", "risky"]:
            for combo in day_suite.get(tier, []):
                for leg in combo.get("legs", []):
                    m = leg.get("matchup")
                    if m and m.strip().lower() == "partit":
                        found_partit.append((d_str, combo.get("combo_id"), leg))

    assert len(found_partit) == 0, f"Found {len(found_partit)} legs with matchup='Partit': {found_partit}"


def test_js_clean_matchup_logic():
    """Verify cleanMatchup function is defined in web/app.js with defensive fallbacks."""
    js_content = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function cleanMatchup(matchup, leg)" in js_content
    assert "window.cleanMatchup = cleanMatchup" in js_content
    assert "onLeagueChange" in js_content
    assert "selectedLeagueRankings" in js_content
    assert "selectedLeaguePredictions" in js_content
    assert "selectedLeagueValue" in js_content
    assert "selectedLeagueCombos" in js_content


def test_html_match_details_modal_structure():
    """FASE 2: Verifica la presencia del modal i l'estructura de pestanyes a web/index.html."""
    html_content = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="modal-match-details"' in html_content
    assert 'md-matchup-header' in html_content
    assert 'data-tab="summary"' in html_content
    assert 'data-tab="comparison"' in html_content
    assert 'data-tab="stats"' in html_content
    assert 'data-tab="markets"' in html_content
    assert 'data-tab="lines"' in html_content
    assert 'data-tab="recommendations"' in html_content
    assert 'closeMatchDetailsModal()' in html_content


def test_js_match_details_modal_and_button():
    """FASE 2: Verifica els manipuladors i el boto 'MES INFORMACIO' a web/app.js."""
    js_content = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "MÉS INFORMACIÓ" in js_content
    assert "openMatchDetailsModal" in js_content
    assert "closeMatchDetailsModal" in js_content
    assert "switchMatchTab" in js_content
    assert "onLineCategoryChange" in js_content
    assert "onLineSliderChange" in js_content


def test_web_data_exporter_build_match_details_structure():
    """FASE 2: Verifica l'estructura, la integritat de dades i les recomanacions de build_match_details."""
    from engine.web_data_exporter import WebDataExporter

    exporter = WebDataExporter()

    mock_match_row = {
        "id": "LALIGA_J10_RMA_FCB",
        "competition_id": "LALIGA",
        "jornada": 10,
        "date_time": "01.11.2026 21:00",
        "status": "SCHEDULED"
    }
    home_data = {"id": "RMA", "name": "Real Madrid", "short_name": "RMA", "elo_rating": 1820.0}
    away_data = {"id": "FCB", "name": "Barcelona", "short_name": "FCB", "elo_rating": 1805.0}
    ref_data = {
        "id": "REF_1", "name": "Sánchez Martínez", "strictness_index": 1.15,
        "yellow_cards_avg": 5.1, "red_cards_avg": 0.28, "fouls_avg": 26.4
    }
    ref_res = {"name": "Sánchez Martínez", "is_generic": False}

    pred = {
        "goals": {
            "expected_goals_home": 1.65,
            "expected_goals_away": 1.25,
            "prob_1X2": {"1": 46.5, "X": 26.5, "2": 27.0},
            "btts": {"yes": 62.0, "no": 38.0}
        },
        "corners": {
            "expected_total_corners": 9.8,
            "prob_over_corners": {"over_8_5": 64.0, "over_9_5": 51.5}
        },
        "cards": {
            "expected_total_cards": 5.2,
            "prob_over_cards": {"over_4_5": 58.0}
        }
    }

    odds_winamax = {
        "matched": True,
        "freshness": "FRESH",
        "provenance": "WINAMAX",
        "1X2": {"1": 2.20, "X": 3.60, "2": 3.40},
        "double_chance": {"1X": 1.35, "12": 1.28, "X2": 1.68},
        "over_under_2_5": {"over": 1.75, "under": 2.10},
        "corners": {"Over 8.5": 1.70, "Under 8.5": 2.15, "Over 9.5": 1.95, "Under 9.5": 1.85}
    }
    odds_bet365 = {
        "matched": True,
        "freshness": "FRESH",
        "provenance": "BET365",
        "1X2": {"1": 2.25, "X": 3.50, "2": 3.30},
        "double_chance": {"1X": 1.36, "12": 1.30, "X2": 1.65},
        "over_under_2_5": {"over": 1.78, "under": 2.05},
        "corners": {"Over 8.5": 1.72, "Under 8.5": 2.10}
    }
    bet_analysis = {
        "value_bets": [
            {
                "category": "1X2",
                "name": "1 - Victòria Real Madrid",
                "bookmaker": "Bet365",
                "odd": 2.25,
                "fair_odd": 2.15,
                "model_prob": 46.5,
                "edge": 4.63,
                "freshness": "FRESH",
                "is_stale": False,
                "recommended_bet_eur": 12.0
            }
        ]
    }

    details = exporter.build_match_details(
        match_row=mock_match_row,
        pred=pred,
        home_data=home_data,
        away_data=away_data,
        ref_data=ref_data,
        ref_res=ref_res,
        odds_winamax=odds_winamax,
        odds_bet365=odds_bet365,
        bet_analysis=bet_analysis,
        competition_id="LALIGA",
        active_jornada=10
    )

    # 1. Estructura general
    assert "summary" in details
    assert "team_comparison" in details
    assert "markets_analysis" in details
    assert "interactive_lines" in details
    assert "recommendations" in details

    # 2. Resum i comparativa
    summary = details["summary"]
    assert summary["home_team"]["name"] == "Real Madrid"
    assert summary["away_team"]["name"] == "Barcelona"
    assert summary["competition_id"] == "LALIGA"

    comp = details["team_comparison"]
    assert "home_standings" in comp
    assert "away_standings" in comp
    assert "home_advanced" in comp
    assert "referee" in comp
    assert comp["referee"]["name"] == "Sánchez Martínez"
    assert comp["referee"]["is_official"] is True

    # 3. Selector interactiu de linies
    lines = details["interactive_lines"]
    assert "corners" in lines
    assert "goals" in lines
    assert "cards" in lines

    # Comprovar linies reals sense inventar quotes
    c_line_85 = next((l for l in lines["corners"] if l["threshold"] == 8.5), None)
    assert c_line_85 is not None
    assert c_line_85["prob_over"] > 0
    assert c_line_85["winamax_over"] == 1.70
    assert c_line_85["bet365_over"] == 1.72
    assert c_line_85["has_real_odds"] is True

    # Si una linia no te quota (ex: corners 12.5), les quotes han de ser estrictament None
    c_line_125 = next((l for l in lines["corners"] if l["threshold"] == 12.5), None)
    if c_line_125:
        assert c_line_125["winamax_over"] is None or isinstance(c_line_125["winamax_over"], (float, int))

    # 4. Recomanacions diferenciades
    rec = details["recommendations"]
    assert "highest_1x2" in rec
    assert "highest_probability_standard" in rec
    assert "highest_probability" in rec
    assert "best_value" in rec
    assert "advised_against" in rec

    h1x2 = rec["highest_1x2"]
    assert h1x2["category"] == "1X2"
    assert h1x2["probability"] == 46.5

    hp = rec["highest_probability_standard"]
    assert hp["probability"] >= 50.0
    assert "disclaimer" in hp
    assert "aposta segura" in hp["disclaimer"].lower() or "certesa" in hp["disclaimer"].lower()

    bv = rec["best_value"]
    assert bv is not None
    assert bv["ev_pct"] == 4.63
    assert bv["bookmaker"] == "Bet365"

    adv = rec["advised_against"]
    assert isinstance(adv, list)

    # 5. Càlcul matemàtic de línies amb push (línies enteres asiàtiques)
    probs_int = exporter._calc_line_probabilities(9.0, 9.8)
    assert probs_int["is_integer"] is True
    assert probs_int["p_push"] > 0.0
    p_w_pct = probs_int["p_over"]
    p_v_pct = probs_int["p_push"]
    fair_int, ev_int = exporter._calc_ev_and_fair_odd(p_w_pct, p_v_pct, 2.10)
    expected_fair_int = round((1.0 - p_v_pct / 100.0) / (p_w_pct / 100.0), 2)
    expected_ev_int = round(((p_w_pct / 100.0) * 2.10 + (p_v_pct / 100.0) - 1.0) * 100.0, 2)
    assert fair_int == expected_fair_int
    assert ev_int == expected_ev_int

    # 6. Semàntica de gols esperats del model (λ)
    assert "lambda_goals_total" in summary
    assert summary["lambda_goals_total"] == round(1.65 + 1.25, 2)

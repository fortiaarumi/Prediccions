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

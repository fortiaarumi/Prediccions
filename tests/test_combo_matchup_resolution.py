import json
import pytest
from engine.combo_bet_engine import ComboBetEngine
from engine.web_data_exporter import WebDataExporter

def test_extract_single_match_markets_matchup():
    engine = ComboBetEngine()
    fake_match = {
        "competition_id": "LALIGA",
        "date": "2026-10-12 21:00",
        "home_name": "Levante UD",
        "away_name": "Sevilla FC",
        "home_team_id": "LEV",
        "away_team_id": "SEV",
        "matchup": "Partit",  # Broken matchup input
        "goals": {
            "prob_1X2": {"1": 40.0, "X": 30.0, "2": 30.0},
            "prob_over_under_2_5": {"over_2_5": 50.0, "under_2_5": 50.0},
            "btts": {"yes": 50.0, "no": 50.0}
        }
    }
    markets = engine._extract_single_match_markets(fake_match)
    assert len(markets) > 0
    for m in markets:
        assert m["matchup"] == "Levante UD vs Sevilla FC"
        assert m["matchup"] != "Partit"
        assert m.get("home_team_id") == "LEV"
        assert m.get("away_team_id") == "SEV"

def test_resolve_leg_matchup_and_teams():
    exporter = WebDataExporter()
    leg = {
        "matchup": "Partit",
        "selection_name": "1X - Levante UD o Empat",
        "match_date": "12.10.2026 21:00"
    }
    resolved_matchup, h_id, a_id = exporter._resolve_leg_matchup_and_teams(leg, default_date="2026-10-12")
    assert resolved_matchup != "Partit"
    assert "Levante" in resolved_matchup
    assert "Sevilla" in resolved_matchup
    assert h_id == "LEV"
    assert a_id == "SEV"

def test_no_partit_in_data_json():
    data = json.load(open("web/data/data.json", "r", encoding="utf-8"))
    found = []
    def check(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "matchup" and v in ["Partit", "Partit Sense Nom"]:
                    found.append(v)
                check(v)
        elif isinstance(obj, list):
            for item in obj:
                check(item)
    check(data)
    assert len(found) == 0, f"Found {len(found)} broken matchups: {found}"

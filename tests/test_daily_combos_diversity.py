"""
Tests for daily combos multi-match diversity and residue elimination.
"""
import json
from pathlib import Path
import pytest
from database.db_manager import DatabaseManager

ROOT = Path(__file__).resolve().parent.parent


def test_daily_combos_diversity_in_data_json():
    """Verify that daily combos for 2026-10-23 do not contain only a single match when 7 matches are analyzed."""
    data_path = ROOT / "web" / "data" / "data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily = data.get("daily_combos", {})
    by_date = daily.get("by_date", {})
    today_suite = by_date.get("2026-10-23") or daily
    matches_count = today_suite.get("matches_count", 0)

    if matches_count >= 2:
        all_matchups = set()
        for tier in ["safe", "semi", "risky"]:
            for combo in today_suite.get(tier, []):
                for leg in combo.get("legs", []):
                    m = leg.get("matchup")
                    if m and m != "Pendent de resoldre":
                        all_matchups.add(m)
        assert len(all_matchups) >= 2, f"Expected daily combos to cover multiple matches, but got: {all_matchups}"


def test_has_daily_combos_with_min_matches():
    """Verify that DatabaseManager.has_daily_combos requires multiple matches when min_matches > 1."""
    db = DatabaseManager(str(ROOT / "data" / "laliga.db"))
    assert db.has_daily_combos("2026-10-23", min_expected=6, min_matches=2) is True

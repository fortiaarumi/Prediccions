import pytest
from database.db_manager import DatabaseManager
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper

def test_genoa_venezia_alias_resolution():
    db = DatabaseManager()
    assert db.find_team_id("Genoa") == "GEN"
    assert db.find_team_id("Génova") == "GEN"
    assert db.find_team_id("Genova") == "GEN"
    assert db.find_team_id("Venezia") == "VEN"
    assert db.find_team_id("Venecia") == "VEN"

def test_winamax_genoa_venezia_detection():
    w = WinamaxScraper(headless=True)
    odds = w.get_match_odds("Genoa", "Venezia", "SERIEA")
    # If cache or live contains Genoa-Venezia, it must match
    assert odds["matched"] is True
    assert odds.get("match_id") is not None
    assert "1" in odds.get("1X2", {})
    assert "X" in odds.get("1X2", {})
    assert "2" in odds.get("1X2", {})
    assert float(odds["1X2"]["1"]) > 1.0

def test_bet365_independent_from_winamax():
    b365 = Bet365Scraper(headless=True)
    winamax_fake_odds = {
        "matched": True,
        "1X2": {"1": 2.10, "X": 3.20, "2": 3.50}
    }
    # Passing winamax_odds must NOT cause Bet365 to derive fake odds
    odds = b365.get_match_odds("UnknownTeamA", "UnknownTeamB", "SERIEA", winamax_odds=winamax_fake_odds)
    assert odds["matched"] is False
    assert odds["1X2"] == {}
    assert odds.get("error") == "NO_ODDS_DETECTED"

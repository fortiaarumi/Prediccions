"""
tests/test_bet365_moviodds_integration.py
==========================================
Verificació de la integració de Bet365 via MoviOdds:
1. Reconciliació temporal amb tolerància de 2.0h (CET vs UTC).
2. Estructura de frescor de quotes (captured_at, freshness=FRESH, is_stale=False).
3. Obtenció correcta a través de Bet365Scraper.
"""

import pytest
from datetime import datetime, timezone
from scraper.moviodds_client import MoviOddsClient, parse_datetime_utc
from scraper.bet365_scraper import Bet365Scraper


def test_moviodds_kickoff_tolerance_reconciles_cet_and_utc():
    """
    Verifica que la tolerància de 2.0h concilia la data emmagatzemada a SQLite
    (hora de Madrid CET UTC+1, ex: 20:00 peninsular -> 19:00 UTC)
    amb el kickoff de MoviOdds/Bet365 (20:00Z UTC).
    """
    client = MoviOddsClient.get_instance()
    # Simulem un partit brut al feed
    raw_match = {
        "id": "FIXTURE_TEST_101",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": "2026-11-08T20:00:00Z",
        "polled_at": "2026-11-08T18:00:00Z",
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Atletico Madrid", "odds_dec": 3.4},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.85},
        ]
    }
    client._raw_matches = [raw_match]
    client._last_fetch_time = datetime.now(timezone.utc).timestamp()

    # match_fixture amb data de calendari local '08.11.2026 20:00' (diferència d'1.0h UTC)
    matched = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 20:00")
    assert matched is not None
    assert matched["id"] == "FIXTURE_TEST_101"


def test_moviodds_parse_match_odds_freshness():
    """
    Verifica que parse_match_odds assigna metadades completes de frescor quan 1X2 és vàlid.
    """
    client = MoviOddsClient.get_instance()
    raw_match = {
        "id": "FIXTURE_TEST_102",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": "2026-11-08T20:00:00Z",
        "polled_at": "2026-11-08T18:15:00Z",
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": "2026-11-08T18:15:00Z"},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": "2026-11-08T18:15:00Z"},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": "2026-11-08T18:15:00Z"},
        ]
    }
    parsed = client.parse_match_odds(raw_match, "LALIGA")
    assert parsed["matched"] is True
    assert parsed["is_stale"] is False
    assert parsed["freshness"] == "FRESH"
    assert parsed["captured_at"] is not None
    assert parsed["1X2"] == {"1": 3.4, "X": 4.5, "2": 1.85}


def test_bet365_scraper_delegates_to_moviodds(monkeypatch):
    """
    Verifica que Bet365Scraper obté les quotes fresques de MoviOdds quan estan disponibles.
    """
    client = MoviOddsClient.get_instance()
    raw_match = {
        "id": "FIXTURE_TEST_103",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": "2026-11-08T20:00:00Z",
        "polled_at": "2026-11-08T18:20:00Z",
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": "2026-11-08T18:20:00Z"},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": "2026-11-08T18:20:00Z"},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": "2026-11-08T18:20:00Z"},
        ]
    }
    client._raw_matches = [raw_match]
    client._last_fetch_time = datetime.now(timezone.utc).timestamp()
    client._parsed_odds_cache.clear()

    scraper = Bet365Scraper(offline=False)
    odds = scraper.get_match_odds("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 20:00")

    assert odds["matched"] is True
    assert odds["1X2"] == {"1": 3.4, "X": 4.5, "2": 1.85}
    assert odds["bookmaker"] == "BET365"
    assert odds["provenance"] == "BET365_MOVIODDS"

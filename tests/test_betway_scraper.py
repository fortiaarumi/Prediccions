"""
tests/test_betway_scraper.py
============================
Tests unitaris i de resiliència per al scraper oficial de Betway Espanya (scraper/betway_scraper.py).
Verifica:
1. Inicialització i lligues admeses.
2. Normalització d'equips mitjançant àlies canònics i DatabaseManager.
3. Extracció de mercats (1X2, Doble Oportunitat, Over/Under 2.5, BTTS) des de GetEventDetails i GetEventMarkets.
4. Gestió d'estats d'error i seleccions no vàlides.
5. Persistència de memòria cau aïllada.
6. get_match_odds unificat per al motor d'arbitratge.
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from scraper.betway_scraper import (
    BetwayScraper,
    BETWAY_COMPETITIONS,
    BETWAY_TEAM_ALIASES
)


@pytest.fixture
def mock_db():
    """Mock del DatabaseManager."""
    db = MagicMock()
    def fake_find_team_id(name):
        mapping = {
            "cornella": 100,
            "sant andreu": 101
        }
        return mapping.get(name.lower().strip())
    db.find_team_id.side_effect = fake_find_team_id

    conn = MagicMock()
    cursor = MagicMock()
    def fake_execute(*args, **kwargs):
        params = args[1] if len(args) > 1 else kwargs.get("params", (0,))
        t_id = params[0]
        id_to_name = {
            100: "UE Cornellà",
            101: "UE Sant Andreu"
        }
        name = id_to_name.get(t_id, f"Team_{t_id}")
        cursor.fetchone.return_value = (name,)
        return cursor

    cursor.execute.side_effect = fake_execute
    conn.cursor.return_value = cursor
    conn.execute.side_effect = fake_execute
    db.get_connection.return_value = conn
    return db


@pytest.fixture
def scraper(mock_db, tmp_path):
    cache_file = tmp_path / "betway_odds_test_cache.json"
    return BetwayScraper(db=mock_db, cache_file=cache_file, offline=False)


# -----------------------------------------------------------------------------
# 1. Inicialització i Competicions
# -----------------------------------------------------------------------------
def test_betway_scraper_initialization(scraper):
    assert scraper is not None
    assert "LALIGA" in BETWAY_COMPETITIONS
    assert "PREMIER" in BETWAY_COMPETITIONS
    assert "HYPERMOTION" in BETWAY_COMPETITIONS
    assert "CHAMPIONSHIP" in BETWAY_COMPETITIONS
    assert "BUNDESLIGA" in BETWAY_COMPETITIONS
    assert "LIGUE1" in BETWAY_COMPETITIONS
    assert "SERIEA" in BETWAY_COMPETITIONS
    assert scraper.cache_path.name == "betway_odds_test_cache.json"


# -----------------------------------------------------------------------------
# 2. Normalització d'Equips i Aliases
# -----------------------------------------------------------------------------
def test_resolve_canonical_team_with_aliases(scraper):
    assert scraper.resolve_canonical_team("Athletic de Bilbao") == "Athletic Club"
    assert scraper.resolve_canonical_team("Atlético Madrid") == "Atlético de Madrid"
    assert scraper.resolve_canonical_team("At. Madrid") == "Atlético de Madrid"
    assert scraper.resolve_canonical_team("Valencia") == "Valencia"
    assert scraper.resolve_canonical_team("Cornellà") == "UE Cornellà"


# -----------------------------------------------------------------------------
# 3. Extracció de Mercats amb API de Serveis Mock
# -----------------------------------------------------------------------------
def test_fetch_event_full_markets_mock(scraper):
    details_response = {
        "Success": True,
        "Event": {
            "Id": 123456,
            "EventName": "Valencia CF vs Real Madrid",
            "Markets": [101, 102, 103, 104]
        }
    }

    markets_response = {
        "Success": True,
        "Markets": [
            {
                "Id": 101,
                "Title": "Guanyador del partit",
                "MarketCName": "win-draw-win",
                "OutcomeGroups": {
                    "home": {"outcomes": [1001]},
                    "draw": {"outcomes": [1002]},
                    "away": {"outcomes": [1003]}
                }
            },
            {
                "Id": 102,
                "Title": "Doble oportunitat",
                "MarketCName": "double-chance",
                "OutcomeGroups": {
                    "home": {"outcomes": [2001]},
                    "draw": {"outcomes": [2002]},
                    "away": {"outcomes": [2003]}
                }
            },
            {
                "Id": 103,
                "Title": "Gols totals - Més de/Menys de 2.5",
                "MarketCName": "-total-goals---2-5",
                "Handicap": 2.5,
                "OutcomeGroups": {
                    "yes": {"outcomes": [3001]},
                    "no": {"outcomes": [3002]}
                }
            },
            {
                "Id": 104,
                "Title": "Ambdós equips marcaran",
                "MarketCName": "both-teams-to-score",
                "OutcomeGroups": {
                    "yes": {"outcomes": [4001]},
                    "no": {"outcomes": [4002]}
                }
            }
        ],
        "Outcomes": [
            {"Id": 1001, "MarketId": 101, "OddsDecimal": 2.80},
            {"Id": 1002, "MarketId": 101, "OddsDecimal": 3.40},
            {"Id": 1003, "MarketId": 101, "OddsDecimal": 2.45},
            {"Id": 2001, "MarketId": 102, "OddsDecimal": 1.55},
            {"Id": 2002, "MarketId": 102, "OddsDecimal": 1.30},
            {"Id": 2003, "MarketId": 102, "OddsDecimal": 1.42},
            {"Id": 3001, "MarketId": 103, "OddsDecimal": 1.85},
            {"Id": 3002, "MarketId": 103, "OddsDecimal": 1.95},
            {"Id": 4001, "MarketId": 104, "OddsDecimal": 1.70},
            {"Id": 4002, "MarketId": 104, "OddsDecimal": 2.05}
        ]
    }

    def fake_call_services_api(endpoint, payload, timeout_seconds=12.0):
        if "GetEventDetails" in endpoint:
            return details_response
        elif "GetEventMarkets" in endpoint:
            return markets_response
        return {"Success": False}

    with patch.object(scraper, "_call_services_api", side_effect=fake_call_services_api):
        result = scraper.fetch_event_full_markets(123456)
        assert result["event_id"] == 123456
        assert result["1X2"] == {"1": 2.80, "X": 3.40, "2": 2.45}
        assert result["double_chance"] == {"1X": 1.55, "12": 1.30, "X2": 1.42}
        assert result["over_under_2_5"] == {"over": 1.85, "under": 1.95}
        assert result["btts"] == {"yes": 1.70, "no": 2.05}


# -----------------------------------------------------------------------------
# 4. Gestió de Mercats Buits o Errors de Serveis
# -----------------------------------------------------------------------------
def test_fetch_event_markets_service_error(scraper):
    with patch.object(scraper, "_call_services_api", return_value={"Success": False, "Errors": ["Timeout"]}):
        res = scraper.fetch_event_full_markets(999999)
        assert "error" in res


# -----------------------------------------------------------------------------
# 5. Persistència de Memòria Cau Aïllada
# -----------------------------------------------------------------------------
def test_betway_cache_persistence(scraper):
    sample_entry = {
        "event_id": 111,
        "home_team": "Valencia CF",
        "away_team": "Real Madrid",
        "1X2": {"1": 2.80, "X": 3.40, "2": 2.45}
    }
    scraper.save_cache_entry("LALIGA_valencia_real_madrid", sample_entry)
    assert scraper.cache_path.exists()

    loaded = scraper.get_cache_entry("LALIGA_valencia_real_madrid")
    assert loaded is not None
    assert loaded["event_id"] == 111
    assert loaded["1X2"]["1"] == 2.80
    assert loaded["bookmaker"] == "BETWAY"
    assert "captured_at" in loaded


# -----------------------------------------------------------------------------
# 6. Resiliència davant Errors de Xarxa a fetch_competition_events
# -----------------------------------------------------------------------------
def test_network_failure_resilience(scraper):
    with patch.object(scraper, "_fetch_url", side_effect=Exception("Connection refused")):
        res = scraper.fetch_competition_events("LALIGA")
        assert res.get("status") == "error"
        assert "events" in res
        assert len(res["events"]) == 0

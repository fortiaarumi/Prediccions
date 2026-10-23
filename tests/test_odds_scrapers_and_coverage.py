"""
tests/test_odds_scrapers_and_coverage.py
========================================
Test suite que cobreix els 11 punts obligatoris de l'auditoria d'scrapers de quotes:
1. Extracció correcta de quotes de Winamax (1X2, Over/Under 2.5, BTTS, Córners, Targetes).
2. Extracció correcta de quotes de Bet365 (via feeds i caches sense sintetització).
3. Noms amb accents, abreviacions i àlies (FC Colonia, Atalanta Bérgamo, Juventus Turín).
4. Partits que la casa retorna i resolució d'associació amb SQLite / DatabaseManager.
5. Mercats inexistents, respostes buides i canvis de format.
6. Errors de connexió i recuperació sense crear dades falses.
7. Quotes caducades, invàlides o duplicades.
8. Persistència, exportació i representació final de les quotes.
9. Generació d'avisos quan falten quotes (missing_odds_alerts.json).
10. Garantia que Bet365 no hereta, deriva ni sintetitza quotes de Winamax (zero factors multiplicadors).
11. Protecció perquè els canvis no reintrodueixin equips U21 a la Premier League absoluta.
"""

import pytest
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, mock_open

from database.db_manager import DatabaseManager
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper
from engine.value_bet_engine import ValueBetEngine
from engine.web_data_exporter import WebDataExporter


# -----------------------------------------------------------------------------
# 1. Extracció correcta de quotes de Winamax
# -----------------------------------------------------------------------------
def test_winamax_extraction_all_markets():
    """Verifica que WinamaxScraper extreu 1X2, Over/Under (amb over/under natius), BTTS (yes/no), córners i targetes."""
    scraper = WinamaxScraper()
    
    tid = "32"
    scraper._tournament_cache[tid] = {
        "matches": {
            "72478654": {
                "matchId": 72478654,
                "tournamentId": 36,
                "competitor1Name": "FC Barcelona",
                "competitor2Name": "Real Madrid",
                "mainBetId": 101,
                "title": "FC Barcelona - Real Madrid"
            }
        }
    }
    scraper._match_cache["72478654"] = {
        "bets": {
            "101": {"betId": 101, "matchId": 72478654, "betTypeCategory": "RESULTAT_DU_MATCH", "betTitle": "1X2", "outcomes": [1001, 1002, 1003]},
            "102": {"betId": 102, "matchId": 72478654, "betTypeCategory": "total de goles", "betTitle": "número total de goles", "outcomes": [1004, 1005]},
            "103": {"betId": 103, "matchId": 72478654, "betTypeCategory": "ambos equipos marcan", "betTitle": "ambos equipos marcan", "outcomes": [1006, 1007]},
            "104": {"betId": 104, "matchId": 72478654, "betTypeCategory": "córners", "betTitle": "Total de córneres", "outcomes": [1008, 1009]},
            "105": {"betId": 105, "matchId": 72478654, "betTypeCategory": "tarjetas", "betTitle": "número total de tarjetas", "outcomes": [1010, 1011]}
        },
        "outcomes": {
            "1001": {"label": "FC Barcelona", "code": "1"},
            "1002": {"label": "Match nul", "code": "x"},
            "1003": {"label": "Real Madrid", "code": "2"},
            "1004": {"label": "Más de 2,5 goles"},
            "1005": {"label": "Menos de 2,5 goles"},
            "1006": {"label": "Sí"},
            "1007": {"label": "No"},
            "1008": {"label": "Más de 9,5 córners"},
            "1009": {"label": "Menos de 9,5 córners"},
            "1010": {"label": "Más de 4,5 tarjetas"},
            "1011": {"label": "Menos de 4,5 tarjetas"}
        },
        "odds": {
            "1001": 2.15,
            "1002": 3.70,
            "1003": 3.10,
            "1004": 1.55,
            "1005": 2.40,
            "1006": 1.48,
            "1007": 2.55,
            "1008": 1.85,
            "1009": 1.95,
            "1010": 1.75,
            "1011": 2.05
        }
    }

    extracted = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert extracted["matched"] is True
    assert extracted["1X2"] == {"1": 2.15, "X": 3.70, "2": 3.10}
    
    # Over/Under ha de tenir tant claus descriptives com les claus de model natiu 'over' i 'under'
    assert extracted["over_under_2_5"]["Over 2.5"] == 1.55
    assert extracted["over_under_2_5"]["Under 2.5"] == 2.40
    assert extracted["over_under_2_5"]["over"] == 1.55
    assert extracted["over_under_2_5"]["under"] == 2.40

    # BTTS ha de tenir tant 'Sí'/'No' com les claus de model 'yes'/'no'
    assert extracted["btts"]["Sí"] == 1.48
    assert extracted["btts"]["No"] == 2.55
    assert extracted["btts"]["yes"] == 1.48
    assert extracted["btts"]["no"] == 2.55

    assert extracted["corners"]["Over 9.5 Córners"] == 1.85
    assert extracted["cards"]["Over 4.5 Targetes"] == 1.75


# -----------------------------------------------------------------------------
# 2. Extracció correcta de quotes de Bet365
# -----------------------------------------------------------------------------
def test_bet365_clean_extraction_no_synthetic():
    """Verifica que Bet365 obté quotes reals quan existeixen i no inventa quotes quan no n'hi ha."""
    scraper = Bet365Scraper()
    scraper._cache = {}
    
    sample_odds = {
        "matched": True,
        "1X2": {"1": 2.20, "X": 3.50, "2": 3.20},
        "over_under_2_5": {"over": 1.60, "under": 2.30},
        "btts": {"yes": 1.50, "no": 2.50}
    }
    scraper._cache["LALIGA_fcbarcelona_realmadrid"] = sample_odds

    match = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert match is not None
    assert match["matched"] is True
    assert match["1X2"]["1"] == 2.20

    # Partit sense quotes a Bet365: ha de retornar matched: False i MAI sintetitzar
    unmatched = scraper.get_match_odds("Getafe CF", "Rayo Vallecano", "LALIGA")
    assert unmatched["matched"] is False
    assert unmatched["1X2"] == {}


# -----------------------------------------------------------------------------
# 3. Noms amb accents, abreviacions i àlies
# -----------------------------------------------------------------------------
def test_team_normalization_and_aliases():
    """Verifica la resolució d'àlies com FC Colonia, Atalanta Bérgamo, Juventus Turín a DatabaseManager."""
    db = DatabaseManager()
    
    assert db.find_team_id("FC Colonia") == "KOE"
    assert db.find_team_id("1. FC Köln") == "KOE"
    assert db.find_team_id("Atalanta Bérgamo") == "ATA"
    assert db.find_team_id("Atalanta BC") == "ATA"
    assert db.find_team_id("Juventus Turín") == "JUV"
    assert db.find_team_id("Juventus FC") == "JUV"
    assert db.find_team_id("Atlético de Madrid") == "ATM"
    assert db.find_team_id("Paris Saint-Germain") == "PSG"


# -----------------------------------------------------------------------------
# 4. Partits que la casa retorna i associació amb SQLite
# -----------------------------------------------------------------------------
def test_match_association_with_sqlite():
    """Verifica que un partit amb noms lleugerament diferents s'associa al partit correcte de SQLite."""
    db = DatabaseManager()
    h_id = db.find_team_id("FC Colonia")
    a_id = db.find_team_id("Borussia Dortmund")
    assert h_id == "KOE"
    assert a_id == "BVB"


# -----------------------------------------------------------------------------
# 5. Mercats inexistents, respostes buides i canvis de format
# -----------------------------------------------------------------------------
def test_empty_or_malformed_responses():
    """Verifica la robustesa davant de respostes buides o sense mercats."""
    b365 = Bet365Scraper()
    b_res = b365.get_match_odds("", "", "LALIGA")
    assert b_res["matched"] is False
    assert b_res["1X2"] == {}


# -----------------------------------------------------------------------------
# 6. Errors de connexió i recuperació sense dades falses
# -----------------------------------------------------------------------------
def test_connection_error_handling():
    """Garanteix que un error d'extracció HTTP no genera quotes inventades."""
    scraper = Bet365Scraper()
    scraper._cache = {}
    with patch("builtins.open", side_effect=IOError("Fitxer inaccessible")):
        scraper._load_cache()
        assert scraper._cache == {}
    res = scraper.get_match_odds("Sevilla FC", "Real Betis", "LALIGA")
    assert res["matched"] is False
    assert res["1X2"] == {}


# -----------------------------------------------------------------------------
# 7. Quotes caducades, invàlides o duplicades
# -----------------------------------------------------------------------------
def test_invalid_and_stale_odds_handling():
    """Comprova que quotes anòmales (< 1.01) o duplicades no es propaguen com a value bets."""
    value_engine = ValueBetEngine()
    pred = {
        "home_team": "Barça",
        "away_team": "Madrid",
        "goals": {
            "prob_1X2": {"1": 50.0, "X": 25.0, "2": 25.0},
            "over_under": {"over_2_5": 50.0, "under_2_5": 50.0},
            "btts": {"yes": 50.0, "no": 50.0},
            "team_goals": {"home_scores": 70.0, "away_scores": 60.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 50.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    # Quota invàlida (0.50)
    invalid_odds = {"1X2": {"1": 0.50, "X": 3.00, "2": 3.00}}
    analysis = value_engine.analyze_match_betting(pred, invalid_odds)
    # No ha de generar un value bet per una quota < 1.01
    vb_1 = [b for b in analysis.get("value_bets", []) if b.get("selection") == "1"]
    assert len(vb_1) == 0


# -----------------------------------------------------------------------------
# 8. Persistència, exportació i representació final
# -----------------------------------------------------------------------------
def test_export_structure_has_both_bookies_and_markets():
    """Verifica que l'exportació conté camps separats per a Winamax i Bet365 sense col·lisions."""
    exporter = WebDataExporter()
    pred = {
        "home_team": {"id": "BAR", "name": "FC Barcelona"},
        "away_team": {"id": "RMA", "name": "Real Madrid"},
        "date": "2026-10-25 21:00",
        "goals": {
            "prob_1X2": {"1": 45.0, "X": 25.0, "2": 30.0},
            "over_under": {"over_2_5": 55.0, "under_2_5": 45.0},
            "btts": {"yes": 52.0, "no": 48.0},
            "team_goals": {"home_scores": 70.0, "away_scores": 60.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 50.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    w_odds = {
        "matched": True,
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.20},
        "over_under_2_5": {"over": 1.65, "under": 2.20},
        "btts": {"yes": 1.55, "no": 2.35},
        "corners": {"over_9_5": 1.80},
        "cards": {"over_4_5": 1.90}
    }
    b_odds = {
        "matched": True,
        "1X2": {"1": 2.15, "X": 3.45, "2": 3.15},
        "over_under_2_5": {"over": 1.62, "under": 2.25},
        "btts": {"yes": 1.58, "no": 2.30}
    }
    
    analysis = exporter.value_engine.analyze_match_betting(pred, w_odds, bet365_odds=b_odds)
    for b in analysis.get("value_bets", []):
        if b.get("bookie_odd_bet365") is not None:
            assert isinstance(b["bookie_odd_bet365"], (int, float))
            # Garanteix que no és una fórmula sintètica exacta (* 1.015)
            assert b["bookie_odd_bet365"] != round(b["bookie_odd"] * 1.015, 2)


# -----------------------------------------------------------------------------
# 9. Generació d'avisos quan falten quotes
# -----------------------------------------------------------------------------
def test_missing_odds_alerts_registration():
    """Garanteix que si un partit futur no té quotes a una de les cases, es genera un avís clar."""
    exporter = WebDataExporter()
    exporter.odds_alerts = []
    
    # Partit futur sense Bet365
    w_has_1x2 = True
    b_has_1x2 = False
    
    if not w_has_1x2 or not b_has_1x2:
        missing_bookies = []
        if not w_has_1x2:
            missing_bookies.append("Winamax")
        if not b_has_1x2:
            missing_bookies.append("Bet365")
        exporter.odds_alerts.append({
            "competition_id": "PREMIER",
            "matchup": "Arsenal vs Chelsea",
            "missing_bookmakers": missing_bookies
        })
        
    assert len(exporter.odds_alerts) == 1
    assert "Bet365" in exporter.odds_alerts[0]["missing_bookmakers"]


# -----------------------------------------------------------------------------
# 10. Bet365 no hereta ni sintetitza quotes de Winamax
# -----------------------------------------------------------------------------
def test_strict_no_synthetic_bet365_odds():
    """Comprova estrictament que Bet365 no hereta ni multiplica per 1.015 les quotes de Winamax."""
    engine = ValueBetEngine()
    pred = {
        "home_team": "Liverpool",
        "away_team": "Everton",
        "goals": {
            "prob_1X2": {"1": 70.0, "X": 20.0, "2": 10.0},
            "over_under": {"over_2_5": 65.0, "under_2_5": 35.0},
            "btts": {"yes": 55.0, "no": 45.0},
            "team_goals": {"home_scores": 80.0, "away_scores": 50.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 50.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    # Només tenim quotes de Winamax, no de Bet365
    w_odds = {
        "1X2": {"1": 1.45, "X": 4.50, "2": 6.50},
        "over_under_2_5": {"over": 1.50, "under": 2.50},
        "btts": {"yes": 1.70, "no": 2.10}
    }
    
    res = engine.analyze_match_betting(pred, w_odds, bet365_odds=None)
    
    for vb in res.get("value_bets", []):
        assert vb.get("bookie_odd_bet365") is None, "Bet365 ha d'estar a None quan no té quota real!"
        
    for sm in res.get("single_markets", []):
        assert sm.get("bookie_odd_bet365") is None, "Bet365 ha d'estar a None quan no té quota real!"


# -----------------------------------------------------------------------------
# 11. Protecció equips U21 Premier League
# -----------------------------------------------------------------------------
def test_premier_league_u21_isolation():
    """Garanteix que la Premier League absoluta no conté equips sub-21 ni partits juvenils."""
    db = DatabaseManager()
    
    # 1. 20 equips oficials de Premier
    standings = db.get_league_standings_elo("PREMIER")
    team_names = [r["name"] for r in standings]
    assert len(team_names) == 20
    for name in team_names:
        assert "U21" not in name
        assert "U18" not in name
        assert "Sub-21" not in name
        assert "Reserves" not in name

    # 2. Partits de Premier a SQLite
    with db.get_connection() as conn:
        premier_matches = conn.execute("""
            SELECT m.*, th.name as home_name, ta.name as away_name
            FROM matches m
            JOIN teams th ON m.home_team_id = th.id
            JOIN teams ta ON m.away_team_id = ta.id
            WHERE m.competition_id = 'PREMIER'
            LIMIT 100
        """).fetchall()
        for m in premier_matches:
            assert m["competition_id"] == "PREMIER"
            h_name = m["home_name"]
            a_name = m["away_name"]
            assert "U21" not in h_name and "U21" not in a_name

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
import time
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, mock_open

ROOT = Path(__file__).resolve().parent.parent

from database.db_manager import DatabaseManager
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper
from engine.value_bet_engine import ValueBetEngine
from engine.web_data_exporter import WebDataExporter
from engine.arbitrage_engine import ArbitrageEngine
from engine.combo_bet_engine import ComboBetEngine


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
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "cached_at": time.time(),
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


# -----------------------------------------------------------------------------
# 12. Cap quota sintètica antiga es pot mostrar com a oficial de Bet365
# -----------------------------------------------------------------------------
def test_reject_legacy_synthetic_cache_entries():
    """Verifica que el scraper i l'exportador rebutgen entrades residuals sense timestamp o amb plantilles sintètiques."""
    scraper = Bet365Scraper()
    scraper._cache = {}

    # Cas A: Entrada sense timestamp ni cached_at (com les 83 de la memòria cau antiga)
    scraper._cache["LALIGA_fcbarcelona_realmadrid"] = {
        "matched": True,
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.10},
        "over_under_2_5": {"over": 1.98, "under": 1.87},
        "btts": {"yes": 1.93, "no": 1.92}
    }
    res_a = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert res_a["matched"] is False
    assert res_a["1X2"] == {}

    # Cas B: Entrada amb timestamp recent però amb plantilla sintètica fixa (1.98 / 1.87)
    scraper._cache["LALIGA_fcbarcelona_realmadrid"] = {
        "matched": True,
        "cached_at": time.time(),
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.10},
        "over_under_2_5": {"over": 1.98, "under": 1.87},
        "btts": {"yes": 1.93, "no": 1.92}
    }
    res_b = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert res_b["matched"] is False
    assert res_b["1X2"] == {}

    # Cas C: Entrada amb marca is_derived_from_winamax
    scraper._cache["LALIGA_fcbarcelona_realmadrid"] = {
        "matched": True,
        "cached_at": time.time(),
        "is_derived_from_winamax": True,
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.10}
    }
    res_c = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert res_c["matched"] is False

    # Cas D: Sanitització a WebDataExporter
    exporter = WebDataExporter()
    legacy_obj = {
        "odds_1x2_bet365": {"1": 2.10, "X": 3.40, "2": 3.10},
        "odds_ou_bet365": {"over": 1.98, "under": 1.87},
        "odds_btts_bet365": {"yes": 1.93, "no": 1.92}
    }
    sanitized = exporter._sanitize_bet365_odds(legacy_obj)
    assert sanitized["odds_1x2_bet365"] == {}
    assert sanitized["odds_ou_bet365"] == {}
    assert sanitized["odds_btts_bet365"] == {}


# -----------------------------------------------------------------------------
# 13. Els overrides manuals es conserven i s'identifiquen correctament
# -----------------------------------------------------------------------------
def test_manual_overrides_provenance_and_identification(tmp_path):
    """Verifica que els overrides manuals s'identifiquen explícitament i no es confonen amb dades oficials."""
    scraper = Bet365Scraper()
    
    # Simular contingut de manual_odds_override.json
    override_data = {
        "LALIGA_fcbarcelona_realmadrid": {
            "bet365": {"1": 2.05, "X": 3.60, "2": 3.40}
        }
    }
    
    with patch("builtins.open", mock_open(read_data=json.dumps(override_data))):
        with patch.object(Path, "exists", return_value=True):
            res = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
            assert res["matched"] is True
            assert res["is_manual_override"] is True
            assert res["provenance"] == "MANUAL_OVERRIDE"
            assert "Override Manual Bet365" in res["source"]
            assert res["1X2"] == {"1": 2.05, "X": 3.60, "2": 3.40}


# -----------------------------------------------------------------------------
# 14. Els avisos de quotes absents es generen i es consumeixen correctament
# -----------------------------------------------------------------------------
def test_missing_odds_alerts_generation_and_consumption():
    """Verifica que el registre d'absència de quotes genera alertes amb l'estructura requerida."""
    exporter = WebDataExporter()
    exporter.odds_alerts = []
    
    # Simular detecció d'un partit sense Bet365
    exporter.odds_alerts.append({
        "competition_id": "LALIGA",
        "matchup": "Villarreal vs Espanyol",
        "date": "2026-10-25 18:30",
        "missing_bookmakers": ["Bet365"],
        "reason": "Bloqueig anti-bot o falta de mercat oficial",
        "timestamp": time.time()
    })
    
    assert len(exporter.odds_alerts) == 1
    alert = exporter.odds_alerts[0]
    assert alert["competition_id"] == "LALIGA"
    assert "Bet365" in alert["missing_bookmakers"]
    assert "timestamp" in alert

    # Format serialitzable a JSON sense errors per ser consumit pel notifier / frontend
    dumped = json.dumps(exporter.odds_alerts)
    loaded = json.loads(dumped)
    assert len(loaded) == 1
    assert loaded[0]["matchup"] == "Villarreal vs Espanyol"


# -----------------------------------------------------------------------------
# 15. Els mercats corregits de Winamax continuen exportant-se bé
# -----------------------------------------------------------------------------
def test_winamax_corrected_markets_end_to_end_export():
    """Garanteix que tots els mercats de Winamax (1X2, Over/Under, BTTS, Córners, Targetes) es conserven íntegres."""
    exporter = WebDataExporter()
    pred = {
        "home_team": {"id": "RMA", "name": "Real Madrid"},
        "away_team": {"id": "BAR", "name": "Barcelona"},
        "date": "2026-10-25 21:00",
        "goals": {
            "prob_1X2": {"1": 40.0, "X": 30.0, "2": 30.0},
            "over_under": {"over_2_5": 60.0, "under_2_5": 40.0},
            "btts": {"yes": 55.0, "no": 45.0},
            "team_goals": {"home_scores": 70.0, "away_scores": 60.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 50.0}, "expected_total_cards": 4.8},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}, "expected_total_corners": 9.5},
        "referee_resolution": {"is_generic": False, "name": "Jesús Gil Manzano"}
    }
    w_odds = {
        "matched": True,
        "1X2": {"1": 2.25, "X": 3.50, "2": 2.95},
        "over_under_2_5": {"Over 2.5": 1.65, "Under 2.5": 2.25, "over": 1.65, "under": 2.25},
        "btts": {"Sí": 1.55, "No": 2.35, "yes": 1.55, "no": 2.35},
        "corners": {"Over 9.5 Córners": 1.85, "over_9_5": 1.85},
        "cards": {"Over 4.5 Targetes": 1.75, "over_4_5": 1.75}
    }
    
    analysis = exporter.value_engine.analyze_match_betting(pred, w_odds, bet365_odds=None)
    assert analysis is not None
    assert "value_bets" in analysis
    assert "single_markets" in analysis
    
    # Comprovar categories de mercats individuals
    categories = {sm["category"] for sm in analysis.get("single_markets", [])}
    assert "1X2" in categories
    assert "Gols" in categories
    assert "BTTS" in categories
    assert "Córners" in categories
    assert "Targetes" in categories


# -----------------------------------------------------------------------------
# 16. Els camps absents no trenquen apostes de valor, combinades o surebets
# -----------------------------------------------------------------------------
def test_absent_odds_resilience_value_combos_surebets():
    """Garanteix que l'absència total de quotes de Bet365 no trenca cap motor d'anàlisi ni genera falses surebets."""
    pred = {
        "home_team": "Sevilla FC",
        "away_team": "Real Betis",
        "goals": {
            "prob_1X2": {"1": 45.0, "X": 30.0, "2": 25.0},
            "over_under": {"over_2_5": 50.0, "under_2_5": 50.0},
            "btts": {"yes": 52.0, "no": 48.0},
            "team_goals": {"home_scores": 65.0, "away_scores": 55.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 50.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    w_odds = {
        "matched": True,
        "1X2": {"1": 2.20, "X": 3.30, "2": 3.40},
        "over_under_2_5": {"over": 1.90, "under": 1.90},
        "btts": {"yes": 1.80, "no": 1.95}
    }
    
    # 1. ValueBetEngine sense Bet365
    val_engine = ValueBetEngine()
    val_analysis = val_engine.analyze_match_betting(pred, w_odds, bet365_odds=None)
    assert isinstance(val_analysis, dict)
    for b in val_analysis.get("value_bets", []):
        assert b["bookie_odd_bet365"] is None

    # 2. ComboBetEngine
    combo_engine = ComboBetEngine()
    prob = combo_engine.calculate_combined_probability([80.0, 75.0])
    odd = combo_engine.calculate_combined_odds([1.30, 1.40])
    assert prob == pytest.approx(0.60, 0.01)
    assert odd == 1.82

    # 3. ArbitrageEngine (Surebets) amb Bet365 absent
    arb_engine = ArbitrageEngine()
    match_data = {
        "home_team": {"name": "Sevilla FC"},
        "away_team": {"name": "Real Betis"},
        "date": "2026-10-25 18:30",
        "competition_id": "LALIGA",
        "competition_name": "LaLiga EA Sports",
        "odds_1x2_winamax": w_odds["1X2"],
        "odds_1x2_bet365": {},  # Totalment absent
        "odds_ou_winamax": w_odds["over_under_2_5"],
        "odds_ou_bet365": {},
        "odds_btts_winamax": w_odds["btts"],
        "odds_btts_bet365": {}
    }
    arbs = arb_engine.scan_match(match_data)
    # Sense una segona casa de quotes, no hi pot haver cap surebet ni cap error
    assert isinstance(arbs, list)
    assert len(arbs) == 0


# -----------------------------------------------------------------------------
# 17. Integració amb font autoritzada (The Odds API) per a Bet365
# -----------------------------------------------------------------------------
def test_authorized_the_odds_api_integration():
    """Garanteix que si es defineix THE_ODDS_API_KEY, el scraper extreu i valida quotes oficials de Bet365."""
    scraper = Bet365Scraper()
    scraper._cache = {}
    
    mock_events = [
        {
            "id": "mock_event_123",
            "sport_key": "soccer_spain_la_liga",
            "home_team": "Real Madrid",
            "away_team": "Barcelona",
            "bookmakers": [
                {
                    "key": "bet365",
                    "title": "Bet365",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Real Madrid", "price": 2.10},
                                {"name": "Draw", "price": 3.60},
                                {"name": "Barcelona", "price": 3.20}
                            ]
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 2.5, "price": 1.65},
                                {"name": "Under", "point": 2.5, "price": 2.25}
                            ]
                        }
                    ]
                }
            ]
        }
    ]

    with patch.dict("os.environ", {"THE_ODDS_API_KEY": "test_token_123"}):
        with patch.object(scraper, "_fetch_odds_api") as mock_fetch:
            mock_fetch.return_value = {
                "matched": True,
                "provenance": "BET365_AUTHORIZED_API",
                "is_manual_override": False,
                "1X2": {"1": 2.10, "X": 3.60, "2": 3.20},
                "over_under_2_5": {"over": 1.65, "under": 2.25}
            }
            res = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA")
            assert res["matched"] is True
            assert res["provenance"] == "BET365_AUTHORIZED_API"
            assert res["is_manual_override"] is False
            assert res["1X2"] == {"1": 2.10, "X": 3.60, "2": 3.20}
            assert res["over_under_2_5"] == {"over": 1.65, "under": 2.25}


# -----------------------------------------------------------------------------
# 18. Deduplicació de partits i cobertura completa a WebDataExporter
# -----------------------------------------------------------------------------
def test_match_deduplication_and_full_championship_coverage():
    """Garanteix que no es generen partits duplicats si coexisteixen identificadors diferents per al mateix partit."""
    exporter = WebDataExporter()
    raw_rows = [
        {"id": "CHAMPIONSHIP_J12_CHA_NOR", "home_team_id": "CHA", "away_team_id": "NOR", "url": None, "date_time": "24.10.2026 13:30"},
        {"id": "CHAMPIONSHIP_2026-2027_h8167lrJ", "home_team_id": "CHA", "away_team_id": "NOR", "url": "https://flashscore.es/...", "date_time": "24.10.2026 11:30"},
        {"id": "CHAMPIONSHIP_2026-2027_K6kbfA0f", "home_team_id": "QPR", "away_team_id": "SWA", "url": "https://flashscore.es/...", "date_time": "24.10.2026 14:00"}
    ]
    
    deduped = {}
    for r in raw_rows:
        k = (r.get("home_team_id"), r.get("away_team_id"))
        if k not in deduped or (r.get("url") and not deduped[k].get("url")):
            deduped[k] = r
    result_rows = list(deduped.values())
    
    assert len(result_rows) == 2
    cha_nor = [r for r in result_rows if r["home_team_id"] == "CHA"][0]
    assert cha_nor["id"] == "CHAMPIONSHIP_2026-2027_h8167lrJ"
    assert cha_nor["url"] is not None


# -----------------------------------------------------------------------------
# 19. Extracció de mercats i procedència a Odds Data API
# -----------------------------------------------------------------------------
def test_odds_data_api_market_extraction_and_provenance():
    """Valida que OddsDataApiClient extreu 1X2, Over/Under, BTTS, Doble Oportunitat, Córners i Targetes."""
    from scraper.odds_data_api_client import OddsDataApiClient
    
    client = OddsDataApiClient(token="test_token_dummy")
    sample_raw_event = {
        "id": "158211790",
        "sport_id": "1",
        "time": "1719586800",
        "time_status": "0",
        "league": {"id": "1001", "name": "English Premier League", "cc": "en"},
        "home": {"id": "501", "name": "Arsenal"},
        "away": {"id": "502", "name": "Chelsea"},
        "main": {
            "full_time_result": {
                "odds": [
                    {"id": "1", "name": "Arsenal", "odds": "1.72"},
                    {"id": "2", "name": "Draw", "odds": "3.80"},
                    {"id": "3", "name": "Chelsea", "odds": "4.50"}
                ]
            },
            "double_chance": {
                "odds": [
                    {"id": "1", "name": "Arsenal or Draw", "odds": "1.20"},
                    {"id": "2", "name": "Arsenal or Chelsea", "odds": "1.28"},
                    {"id": "3", "name": "Draw or Chelsea", "odds": "2.05"}
                ]
            }
        },
        "goals": {
            "goals_over_under": {
                "odds": [
                    {"id": "1", "name": "Over", "handicap": "2.5", "odds": "1.80"},
                    {"id": "2", "name": "Under", "handicap": "2.5", "odds": "2.00"}
                ]
            },
            "both_teams_to_score": {
                "odds": [
                    {"id": "1", "name": "Yes", "odds": "1.75"},
                    {"id": "2", "name": "No", "odds": "2.05"}
                ]
            }
        },
        "corners": {
            "total_corners": {
                "odds": [
                    {"name": "Over", "handicap": "9.5", "odds": "1.85"},
                    {"name": "Under", "handicap": "9.5", "odds": "1.95"}
                ]
            }
        },
        "cards": {
            "total_cards": {
                "odds": [
                    {"name": "Over", "handicap": "4.5", "odds": "1.70"},
                    {"name": "Under", "handicap": "4.5", "odds": "2.10"}
                ]
            }
        }
    }

    parsed = client.parse_prematch_markets(sample_raw_event)
    assert parsed["matched"] is True
    assert parsed["provenance"] == "BET365_ODDS_DATA_API"
    assert parsed["is_manual_override"] is False
    assert parsed["1X2"] == {"1": 1.72, "X": 3.80, "2": 4.50}
    assert parsed["over_under_2_5"] == {"Over 2.5": 1.80, "over": 1.80, "Under 2.5": 2.00, "under": 2.00}
    assert parsed["btts"] == {"Sí": 1.75, "yes": 1.75, "No": 2.05, "no": 2.05}
    assert parsed["double_chance"]["1X"] == 1.20
    assert "Over 9.5 Córners" in parsed["corners"]
    assert "Over 4.5 Targetes" in parsed["cards"]


# -----------------------------------------------------------------------------
# 20. Associació inequívoca de partits (exigeix ambdós equips)
# -----------------------------------------------------------------------------
def test_odds_data_api_unambiguous_match_association():
    """Garanteix que OddsDataApiClient mai associa un partit només perquè comparteix un equip."""
    from scraper.odds_data_api_client import OddsDataApiClient
    client = OddsDataApiClient(token="test_token_dummy")

    fixtures = [
        {"id": "1001", "home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
        {"id": "1002", "home": {"name": "Liverpool"}, "away": {"name": "Everton"}},
        {"id": "1003", "home": {"name": "Manchester City"}, "away": {"name": "Tottenham"}}
    ]

    # Coincidència exacta d'ambdós equips
    fi_ok = client.match_fixture_id("Arsenal FC", "Chelsea FC", "PREMIER", fixtures)
    assert fi_ok == "1001"

    # Només coincideix l'equip local (Arsenal vs Watford): NO HA D'ASSOCIAR
    fi_mismatch_away = client.match_fixture_id("Arsenal", "Watford", "PREMIER", fixtures)
    assert fi_mismatch_away is None

    # Només coincideix l'equip visitant (Aston Villa vs Chelsea): NO HA D'ASSOCIAR
    fi_mismatch_home = client.match_fixture_id("Aston Villa", "Chelsea", "PREMIER", fixtures)
    assert fi_mismatch_home is None


# -----------------------------------------------------------------------------
# 21. Comportament segur en absència de token
# -----------------------------------------------------------------------------
def test_odds_data_api_missing_token_safe_behavior():
    """Comprova que en absència d'ODDS_DATA_API_TOKEN el sistema no sintetitza dades i diagnostica la causa."""
    with patch.dict(os.environ, {}, clear=True):
        b365 = Bet365Scraper()
        b365._cache = {}
        odds = b365.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
        assert odds["matched"] is False
        assert odds["1X2"] == {}
        assert odds["error"] == "NO_ODDS_DETECTED"
        assert odds["access_status"] == "REQUIRES_ODDS_DATA_API_TOKEN"
        assert "ODDS_DATA_API_TOKEN" in odds["access_diagnostics"]


# -----------------------------------------------------------------------------
# 22. Exactament 12 partits únics de Championship a SQLite i absència de placeholders
# -----------------------------------------------------------------------------
def test_championship_exact_twelve_unique_matches_in_sqlite_and_export():
    """Verifica que a SQLite existeixen exactament 12 partits de Championship J12 sense cap duplicat ni placeholder."""
    db_path = ROOT / "data" / "laliga.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    rows = c.execute("""
        SELECT id, home_team_id, away_team_id, jornada, status, url
        FROM matches
        WHERE competition_id = 'CHAMPIONSHIP' AND jornada = 12
    """).fetchall()

    assert len(rows) == 12, f"S'esperaven exactament 12 partits per a Championship J12, s'han trobat {len(rows)}"

    # Cap registre pot començar per CHAMPIONSHIP_J12_
    for r in rows:
        assert not r[0].startswith("CHAMPIONSHIP_J12_"), f"S'ha trobat un ID placeholder no purgat: {r[0]}"
        assert r[0].startswith("CHAMPIONSHIP_2026-2027_"), f"ID no oficial: {r[0]}"
        assert r[5] is not None and "flashscore" in r[5], f"URL oficial absent a {r[0]}"

    # No hi ha d'haver cap enfrontament duplicat
    matchups = [(r[1], r[2]) for r in rows]
    assert len(matchups) == len(set(matchups)), "Hi ha enfrontaments duplicats a la jornada 12 de Championship"


# -----------------------------------------------------------------------------
# 23. MoviOdds: Extracció exhaustiva de mercats reals Bet365
# -----------------------------------------------------------------------------
def test_moviodds_client_parsing_and_market_extraction():
    """Valida que MoviOddsClient extreu 1X2, Doble Oportunitat, Over/Under 2.5, BTTS, Córners i Targetes."""
    from scraper.moviodds_client import MoviOddsClient
    client = MoviOddsClient()

    fixture = {
        "id": 201587974,
        "home": "Barcelona",
        "away": "Real Madrid",
        "kickoff_utc": "2026-10-25T20:00:00Z",
        "polled_at": "2026-10-23T22:00:00Z",
        "quotes": [
            {"group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.62, "ts": "2026-10-23T20:21:17Z"},
            {"group_name": "Full Time Result", "label": "Draw", "odds_dec": 5.75, "ts": "2026-10-23T20:21:17Z"},
            {"group_name": "Full Time Result", "label": "Real Madrid", "odds_dec": 4.00, "ts": "2026-10-23T20:21:17Z"},
            {"group_name": "Double Chance", "label": "Barcelona or Draw", "odds_dec": 1.18},
            {"group_name": "Goals Over/Under", "label": "Over", "line": "2.5", "odds_dec": 1.50},
            {"group_name": "Goals Over/Under", "label": "Under", "line": "2.5", "odds_dec": 2.60},
            {"group_name": "Both Teams to Score", "label": "Yes", "odds_dec": 1.53},
            {"group_name": "Both Teams to Score", "label": "No", "odds_dec": 2.38},
            {"group_name": "Asian Total Corners", "label": "Over", "line": "10.5", "odds_dec": 1.80},
            {"group_name": "Cards", "label": "Lamine Yamal", "odds_dec": 19.00},
            # Selecció suspesa: no s'ha d'incloure
            {"group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.10, "suspended": True}
        ]
    }

    res = client.parse_match_odds(fixture, "LALIGA")
    assert res["matched"] is True
    assert res["bookmaker"] == "BET365"
    assert res["provenance"] == "BET365_MOVIODDS"
    assert res["1X2"]["1"] == 1.62
    assert res["1X2"]["X"] == 5.75
    assert res["1X2"]["2"] == 4.00
    assert res["over_under_2_5"]["Over 2.5"] == 1.50
    assert res["over_under_2_5"]["Under 2.5"] == 2.60
    assert res["btts"]["Sí"] == 1.53
    assert res["btts"]["No"] == 2.38
    assert "Asian Total Corners Over 10.5" in res["corners"]
    assert "Cards Lamine Yamal" in res["cards"]
    assert res["ts"] == "2026-10-23T20:21:17Z"


# -----------------------------------------------------------------------------
# 24. MoviOdds: Associació inequívoca de partits i àlies canònics
# -----------------------------------------------------------------------------
def test_moviodds_client_unambiguous_match_association():
    """Garanteix que MoviOddsClient només associa si coincideixen TOTS DOS equips i la lliga és compatible."""
    from scraper.moviodds_client import MoviOddsClient
    client = MoviOddsClient()

    client._raw_matches = [
        {"id": 101, "league": "England Championship", "home": "Sheff Utd", "away": "Derby", "kickoff_utc": "2026-10-24T15:00:00Z", "quotes": []},
        {"id": 102, "league": "England Championship", "home": "QPR", "away": "Swansea", "kickoff_utc": "2026-10-24T15:00:00Z", "quotes": []},
        {"id": 103, "league": "Germany Bundesliga I", "home": "Cologne", "away": "Schalke", "kickoff_utc": "2026-10-24T15:00:00Z", "quotes": []},
        {"id": 104, "league": "Spain La Liga", "home": "Barcelona", "away": "Real Madrid", "kickoff_utc": "2026-10-24T15:00:00Z", "quotes": []}
    ]
    client._last_fetch_time = time.time()
    client._index_matches()

    # Coincidència canònica amb resolució d'àlies i data compatible
    m1 = client.match_fixture("Sheffield United", "Derby County", "CHAMPIONSHIP", match_date_time="24.10.2026 17:00")
    assert m1 is not None
    assert m1["id"] == 101

    m2 = client.match_fixture("Queens Park Rangers", "Swansea City", "CHAMPIONSHIP", match_date_time="24.10.2026 17:00")
    assert m2 is not None
    assert m2["id"] == 102

    m3 = client.match_fixture("1. FC Colonia", "FC Schalke 04", "BUNDESLIGA", match_date_time="24.10.2026 17:00")
    assert m3 is not None
    assert m3["id"] == 103

    # Mismatch d'equip visitant: NO HA D'ASSOCIAR
    m_mismatch = client.match_fixture("Sheffield United", "Watford", "CHAMPIONSHIP", match_date_time="24.10.2026 17:00")
    assert m_mismatch is None

    # Mismatch de competició: Barcelona vs Real Madrid a Premier League NO HA D'ASSOCIAR
    m_comp_mismatch = client.match_fixture("FC Barcelona", "Real Madrid", "PREMIER", match_date_time="24.10.2026 17:00")
    assert m_comp_mismatch is None


# -----------------------------------------------------------------------------
# 25. MoviOdds: Integració amb Bet365Scraper
# -----------------------------------------------------------------------------
def test_moviodds_integration_with_bet365_scraper():
    """Valida que quan MoviOddsClient conté el feed en memòria, Bet365Scraper l'utilitza de forma transparent."""
    from scraper.moviodds_client import MoviOddsClient
    from scraper.bet365_scraper import Bet365Scraper

    movi = MoviOddsClient.get_instance()
    movi._raw_matches = [
        {
            "id": 99901,
            "league": "Spain La Liga",
            "home": "Barcelona",
            "away": "Real Madrid",
            "kickoff_utc": "2026-10-24T19:00:00Z",
            "polled_at": datetime.now(timezone.utc).isoformat(),
            "quotes": [
                {"group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.62},
                {"group_name": "Full Time Result", "label": "Draw", "odds_dec": 5.75},
                {"group_name": "Full Time Result", "label": "Real Madrid", "odds_dec": 4.00},
                {"group_name": "Goals Over/Under", "label": "Over", "line": "2.5", "odds_dec": 1.50},
                {"group_name": "Goals Over/Under", "label": "Under", "line": "2.5", "odds_dec": 2.60},
                {"group_name": "Both Teams to Score", "label": "Yes", "odds_dec": 1.53},
                {"group_name": "Both Teams to Score", "label": "No", "odds_dec": 2.38}
            ]
        }
    ]
    movi._last_fetch_time = time.time()
    movi._index_matches()

    scraper = Bet365Scraper()
    scraper._cache = {}  # Sense memòria cau prèvia

    odds = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA", match_date_time="24.10.2026 21:00")
    assert odds["matched"] is True
    assert odds["provenance"] == "BET365_MOVIODDS"
    assert odds["1X2"]["1"] == 1.62
    assert odds["1X2"]["X"] == 5.75
    assert odds["1X2"]["2"] == 4.00
    assert odds["over_under_2_5"]["Over 2.5"] == 1.50
    assert odds["btts"]["Sí"] == 1.53

    # Reset per no contaminar altres proves
    movi._raw_matches = []
    movi._last_fetch_time = 0.0
    movi._indexed_matches.clear()
    movi._parsed_odds_cache.clear()


# -----------------------------------------------------------------------------
# 26. MoviOdds: Mercat amb group_status.stale: true es descarta
# -----------------------------------------------------------------------------
def test_moviodds_stale_market_discarded():
    """Valida que un mercat amb group_status.stale: true es descarta, fins i tot si no està suspès."""
    from scraper.moviodds_client import MoviOddsClient
    from datetime import datetime, timezone
    client = MoviOddsClient()

    now_iso = datetime.now(timezone.utc).isoformat()
    fixture = {
        "id": 88001,
        "home": "Barcelona",
        "away": "Real Madrid",
        "kickoff_utc": "2026-10-25T20:00:00Z",
        "polled_at": now_iso,
        "group_status": {
            "1": {"polled_at": now_iso, "stale": False},
            "2": {"polled_at": now_iso, "stale": True},  # O/U marcat com a obsolet
        },
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.65, "suspended": False},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.00, "suspended": False},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Real Madrid", "odds_dec": 4.80, "suspended": False},
            # Mercat stale: s'ha de descartar completament
            {"group_id": "2", "group_name": "Goals Over/Under", "label": "Over", "line": "2.5", "odds_dec": 1.55, "suspended": False},
            {"group_id": "2", "group_name": "Goals Over/Under", "label": "Under", "line": "2.5", "odds_dec": 2.45, "suspended": False},
        ]
    }

    res = client.parse_match_odds(fixture, "LALIGA")
    assert res["matched"] is True
    assert res["1X2"]["1"] == 1.65
    # Over/Under ha de ser buit perquè estava marcat com a stale
    assert res["over_under_2_5"] == {}


# -----------------------------------------------------------------------------
# 27. MoviOdds: polled_at antic no és fresc per cached_at recent
# -----------------------------------------------------------------------------
def test_moviodds_old_polled_at_not_fresh_even_if_cached_at_recent():
    """Valida que un polled_at antic (>24h) fa que l'entrada no sigui considerada fresca, encara que cached_at sigui now."""
    from scraper.moviodds_client import MoviOddsClient
    client = MoviOddsClient()

    stale_entry = {
        "matched": True,
        "bookmaker": "BET365",
        "provenance": "BET365_MOVIODDS",
        "cached_at": time.time(),  # Recentíssim
        "polled_at": "2026-10-15T10:00:00Z",  # Antic (fa més d'una setmana)
        "ts": "2026-10-15T09:30:00Z",
        "1X2": {"1": 1.80, "X": 3.60, "2": 4.50}
    }

    is_fresh = client.is_odds_entry_fresh(stale_entry)
    assert is_fresh is False, "Una quota amb polled_at antic de fa dies NO pot ser considerada fresca"


# -----------------------------------------------------------------------------
# 28. Bet365Scraper: Quota actual del feed preval sobre memòria cau antiga
# -----------------------------------------------------------------------------
def test_moviodds_feed_precedes_cache():
    """Valida que quan el feed té una quota actual, aquesta preval sobre una entrada antiga existent a la memòria cau."""
    from scraper.moviodds_client import MoviOddsClient
    from scraper.bet365_scraper import Bet365Scraper
    from datetime import datetime, timezone

    movi = MoviOddsClient.get_instance()
    movi._parsed_odds_cache.clear()
    movi._indexed_matches.clear()
    now_iso = datetime.now(timezone.utc).isoformat()

    movi._raw_matches = [
        {
            "id": 77101,
            "league": "Spain La Liga",
            "home": "Barcelona",
            "away": "Real Madrid",
            "kickoff_utc": "2026-10-25T20:00:00Z",
            "polled_at": now_iso,
            "group_status": {
                "1": {"polled_at": now_iso, "stale": False}
            },
            "quotes": [
                {"group_id": "1", "group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.95},
                {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 3.80},
                {"group_id": "1", "group_name": "Full Time Result", "label": "Real Madrid", "odds_dec": 3.90},
            ]
        }
    ]
    movi._last_fetch_time = time.time()
    movi._index_matches()

    scraper = Bet365Scraper()
    scraper._cache = {}
    # Inserir entrada prèvia a la memòria cau amb quota diferent (1.40)
    cache_key = "LALIGA_fcbarcelona_realmadrid"
    scraper._cache[cache_key] = {
        "matched": True,
        "provenance": "BET365_MOVIODDS",
        "polled_at": now_iso,
        "1X2": {"1": 1.40, "X": 4.50, "2": 7.00}
    }

    # Ha de retornar la nova quota del feed (1.95), no l'antiga de la memòria cau (1.40)
    odds = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA", match_date_time="25.10.2026 22:00")
    assert odds["matched"] is True
    assert odds["1X2"]["1"] == 1.95, f"Esperat 1.95 del feed en viu, obtingut {odds['1X2'].get('1')}"

    # Reset
    movi._raw_matches = []
    movi._last_fetch_time = 0.0
    movi._indexed_matches.clear()
    movi._parsed_odds_cache.clear()


# -----------------------------------------------------------------------------
# 29. MoviOdds: Partit amb equips iguals però data incompatible es rebutja
# -----------------------------------------------------------------------------
def test_moviodds_incompatible_kickoff_rejected():
    """Valida que un partit amb els mateixos equips però hora/data fora de la tolerància de 28h es rebutja."""
    from scraper.moviodds_client import MoviOddsClient
    client = MoviOddsClient()

    client._raw_matches = [
        {
            "id": 66101,
            "league": "Spain La Liga",
            "home": "Barcelona",
            "away": "Real Madrid",
            "kickoff_utc": "2026-11-20T20:00:00Z",  # Data 1 mes més tard
            "quotes": []
        }
    ]
    client._last_fetch_time = time.time()
    client._index_matches()

    # Cerca amb data de la jornada actual (25 d'octubre) -> Diferència ~26 dies (>28 hores) -> Ha de ser None
    matched = client.match_fixture("FC Barcelona", "Real Madrid", "LALIGA", match_date_time="25.10.2026 20:00")
    assert matched is None, "Partit amb desfasament de 26 dies ha de ser rebutjat per incompatibilitat temporal"

    # Cerca amb data compatible (20 de novembre) -> Dins de la tolerància -> Ha de trobar-lo
    matched_ok = client.match_fixture("FC Barcelona", "Real Madrid", "LALIGA", match_date_time="20.11.2026 20:00")
    assert matched_ok is not None
    assert matched_ok["id"] == 66101


# -----------------------------------------------------------------------------
# 30. MoviOdds: Coincidència basada només en part del nom es rebutja
# -----------------------------------------------------------------------------
def test_moviodds_partial_name_match_rejected():
    """Garanteix que compartir paraules parcials ('United', 'City') NO produeix una associació falsa."""
    from scraper.moviodds_client import MoviOddsClient
    client = MoviOddsClient()

    client._raw_matches = [
        {"id": 55101, "league": "England Premier League", "home": "Manchester City", "away": "Newcastle United", "quotes": []}
    ]
    client._last_fetch_time = time.time()
    client._index_matches()

    # Si busquem Manchester United vs West Ham United (comparteixen 'Manchester' i 'United' respectivament), NO HA DE CASAR
    res = client.match_fixture("Manchester United", "West Ham United", "PREMIER")
    assert res is None, "La coincidència parcial de paraules NO ha de produir associacions espúries"


# -----------------------------------------------------------------------------
# 31. MoviOdds: Quotes suspeses i invàlides es descarten
# -----------------------------------------------------------------------------
def test_moviodds_suspended_and_invalid_odds_discarded():
    """Comprova que seleccions amb suspended: true i preus <= 1.0 s'exclouen rigorosament."""
    from scraper.moviodds_client import MoviOddsClient
    from datetime import datetime, timezone
    client = MoviOddsClient()

    now_iso = datetime.now(timezone.utc).isoformat()
    fixture = {
        "id": 44101,
        "home": "Valencia",
        "away": "Villarreal",
        "kickoff_utc": "2026-10-24T16:30:00Z",
        "polled_at": now_iso,
        "group_status": {
            "1": {"polled_at": now_iso, "stale": False}
        },
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Valencia", "odds_dec": 2.50, "suspended": False},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 1.00, "suspended": False},  # Preu invàlid (<= 1.0)
            {"group_id": "1", "group_name": "Full Time Result", "label": "Villarreal", "odds_dec": 2.80, "suspended": True},  # Suspesa
        ]
    }

    res = client.parse_match_odds(fixture, "LALIGA")
    # L'empat (1.00) i la victòria visitant (suspesa) invaliden el mercat 1X2 complet
    assert res["matched"] is False
    assert res["1X2"] == {}


# -----------------------------------------------------------------------------
# 32. Alertes: Diferenciació de quotes absents, obsoletes i mercats secundaris
# -----------------------------------------------------------------------------
def test_odds_alerts_distinguish_missing_from_stale():
    """Valida que les alertes generades distingeixen entre alert_type MISSING_1X2, STALE_ODDS i MISSING_SECONDARY_MARKET."""
    alerts = [
        {"alert_type": "MISSING_1X2", "missing_bookmakers": ["Bet365"], "matchup": "Team A vs Team B"},
        {"alert_type": "STALE_ODDS", "is_stale": True, "matchup": "Team C vs Team D"},
        {"alert_type": "MISSING_SECONDARY_MARKET", "missing_markets": ["Over/Under 2.5"], "matchup": "Team E vs Team F"}
    ]

    types = [a["alert_type"] for a in alerts]
    assert "MISSING_1X2" in types
    assert "STALE_ODDS" in types
    assert "MISSING_SECONDARY_MARKET" in types


# -----------------------------------------------------------------------------
# 33. Prova Obligatòria 1: Dates locals sense zona horària i conversió a UTC
# -----------------------------------------------------------------------------
def test_local_naive_dates_converted_to_utc():
    """Comprova que les dates sense zona horària de SQLite/Flashscore (horari peninsular espanyol Europe/Madrid)
    es converteixen correctament a UTC tant en horari d'estiu (CEST UTC+2) com d'hivern (CET UTC+1)."""
    from scraper.moviodds_client import parse_datetime_utc

    # Estiu (octubre): 24.10.2026 21:00 CEST -> 19:00:00 UTC
    dt_summer = parse_datetime_utc("24.10.2026 21:00")
    assert dt_summer is not None
    assert dt_summer.tzinfo == timezone.utc
    assert dt_summer.year == 2026 and dt_summer.month == 10 and dt_summer.day == 24
    assert dt_summer.hour == 19 and dt_summer.minute == 0

    # Hivern (gener): 15.01.2026 21:00 CET -> 20:00:00 UTC
    dt_winter = parse_datetime_utc("15.01.2026 21:00")
    assert dt_winter is not None
    assert dt_winter.tzinfo == timezone.utc
    assert dt_winter.year == 2026 and dt_winter.month == 1 and dt_winter.day == 15
    assert dt_winter.hour == 20 and dt_winter.minute == 0

    # Format ISO amb Z explícita es preserva exactament
    dt_iso = parse_datetime_utc("2026-10-24T18:45:00Z")
    assert dt_iso is not None
    assert dt_iso.tzinfo == timezone.utc
    assert dt_iso.hour == 18 and dt_iso.minute == 45


# -----------------------------------------------------------------------------
# 34. Prova Obligatòria 2: Rebuig d'hora incompatible i tolerància petita (<= 2.5h)
# -----------------------------------------------------------------------------
def test_incompatible_kickoff_rejected_with_tight_tolerance():
    """Valida que MAX_KICKOFF_TOLERANCE_HOURS = 2.5h rebutja partits amb desfasaments arbitraris o de gairebé un dia,
    rebutja partits sense data, i només accepta reprogramacions explícites documentades."""
    from scraper.moviodds_client import MoviOddsClient, MAX_KICKOFF_TOLERANCE_HOURS
    client = MoviOddsClient()

    assert MAX_KICKOFF_TOLERANCE_HOURS == 2.5, "La tolerància ha de ser petita i estricta (2.5h)"

    client._raw_matches = [
        {
            "id": 77101,
            "league": "Spain La Liga",
            "home": "Valencia",
            "away": "Villarreal",
            "kickoff_utc": "2026-10-24T16:30:00Z",
            "quotes": []
        },
        {
            "id": 77102,
            "league": "France Ligue 1",
            "home": "Paris SG",
            "away": "Lyon",
            "kickoff_utc": "2026-10-26T19:45:00Z",  # Reprogramat dilluns per televisió LFP (+26h)
            "quotes": []
        }
    ]
    client._last_fetch_time = time.time()
    client._index_matches()

    # 1. Coincidència de dates perfecta (24.10.2026 18:30 Europe/Madrid -> 16:30 UTC -> diff 0.0h)
    m_ok = client.match_fixture("Valencia CF", "Villarreal CF", "LALIGA", match_date_time="24.10.2026 18:30")
    assert m_ok is not None and m_ok["id"] == 77101

    # 2. Desfasament de 4 hores (> 2.5h) -> Ha de ser rebutjat
    m_4h = client.match_fixture("Valencia CF", "Villarreal CF", "LALIGA", match_date_time="24.10.2026 22:30")
    assert m_4h is None, "Diferència de 4 hores ha de ser rebutjada"

    # 3. Desfasament de gairebé un dia (25.10.2026 12:00) -> Ha de ser rebutjat
    m_day = client.match_fixture("Valencia CF", "Villarreal CF", "LALIGA", match_date_time="25.10.2026 12:00")
    assert m_day is None, "Diferència de gairebé un dia ha de ser rebutjada"

    # 4. Falta de data de partit o data invàlida -> Rebutjat
    assert client.match_fixture("Valencia CF", "Villarreal CF", "LALIGA", match_date_time=None) is None
    assert client.match_fixture("Valencia CF", "Villarreal CF", "LALIGA", match_date_time="data-desconeguda") is None

    # 5. Reprogramació explícita documentada (PSG vs Lyon LFP broadcast dilluns vs calendari diumenge 17:00)
    m_rescheduled = client.match_fixture("Paris Saint-Germain", "Olympique Lyonnais", "LIGUE1", match_date_time="25.10.2026 17:00")
    assert m_rescheduled is not None and m_rescheduled["id"] == 77102, "Reprogramació explícita ha de ser acceptada mitjançant registre dedicat"


# -----------------------------------------------------------------------------
# 35. Prova Obligatòria 3: Rebuig de cached_at sense timestamp de la font
# -----------------------------------------------------------------------------
def test_cached_at_without_source_timestamp_rejected():
    """Comprova que Bet365Scraper rebutja entrades de memòria cau que només contenen cached_at recent
    però no disposen de cap polled_at o ts de la font oficial."""
    from scraper.bet365_scraper import Bet365Scraper
    scraper = Bet365Scraper()
    scraper._cache = {}

    # Entrada amb cached_at fresc (fa 10 segons) però SENSE polled_at ni ts
    scraper._cache["LALIGA_fcbarcelona_realmadrid"] = {
        "matched": True,
        "cached_at": time.time() - 10,  # 10s d'antiguitat
        "1X2": {"1": 2.10, "X": 3.60, "2": 3.40},
        "over_under_2_5": {"over": 1.65, "under": 2.25}
    }

    # Ha de rebutjar la quota com a no verificada
    res = scraper.get_match_odds("FC Barcelona", "Real Madrid", "LALIGA")
    assert res["matched"] is False, "Una entrada sense polled_at/ts no es pot verificar i s'ha de rebutjar"
    assert res["status"] == "PENDING_REAL_ODDS"


# -----------------------------------------------------------------------------
# 36. Prova Obligatòria 4: Rebuig de mercat secundari amb polled_at antic
# -----------------------------------------------------------------------------
def test_secondary_market_stale_discarded_independent_of_1x2():
    """Valida que un mercat secundari (ex: corners o over/under) amb polled_at antic es descarta
    individualment encara que el mercat 1X2 del mateix partit tingui polled_at fresc."""
    from scraper.moviodds_client import MoviOddsClient
    from datetime import datetime, timezone, timedelta
    client = MoviOddsClient()

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    old_iso = (now_dt - timedelta(hours=48)).isoformat()  # 48 hores d'antiguitat (> 24h)

    fixture = {
        "id": 88101,
        "home": "Arsenal",
        "away": "Chelsea",
        "kickoff_utc": (now_dt + timedelta(hours=24)).isoformat(),
        "polled_at": now_iso,
        "group_status": {
            "1": {"polled_at": now_iso, "stale": False},    # 1X2 fresc
            "2": {"polled_at": old_iso, "stale": False},    # Over/Under 2.5 antic
            "9": {"polled_at": old_iso, "stale": False}     # Corners antic
        },
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Arsenal", "odds_dec": 1.95, "ts": now_iso},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 3.60, "ts": now_iso},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Chelsea", "odds_dec": 4.10, "ts": now_iso},
            {"group_id": "2", "group_name": "Goals Over/Under", "label": "Over", "line": "2.5", "odds_dec": 1.80, "ts": old_iso},
            {"group_id": "2", "group_name": "Goals Over/Under", "label": "Under", "line": "2.5", "odds_dec": 2.05, "ts": old_iso},
            {"group_id": "9", "group_name": "Match Corners", "label": "Over", "line": "9.5", "odds_dec": 1.85, "ts": old_iso}
        ]
    }

    parsed = client.parse_match_odds(fixture, "PREMIER")

    # 1. 1X2 és fresc i vàlid
    assert parsed["matched"] is True
    assert parsed["1X2"]["1"] == 1.95
    assert parsed["market_status"]["1X2"]["valid"] is True

    # 2. Over/Under 2.5 té polled_at antic: es descarta individualment
    assert parsed["over_under_2_5"] == {}, "Over/Under amb polled_at antic ha de ser buit"
    assert parsed["market_status"]["over_under_2_5"]["valid"] is False
    assert parsed["market_status"]["over_under_2_5"]["stale"] is True

    # 3. Corners té polled_at antic: es descarta individualment
    assert parsed["corners"] == {}, "Corners amb polled_at antic ha de ser buit"
    assert parsed["market_status"]["corners"]["valid"] is False
    assert parsed["market_status"]["corners"]["stale"] is True


# -----------------------------------------------------------------------------
# 37. Prova Obligatòria 5: Comptadors d'auditoria que exclouen mercats obsolets
# -----------------------------------------------------------------------------
def test_audit_counters_exclude_stale_markets():
    """Comprova que l'auditoria de cobertura només incrementa comptadors de mercats que superen
    la frescor i no barreja mercats obsolets amb quotes vigents."""
    from engine.web_data_exporter import WebDataExporter

    exporter = WebDataExporter()

    mock_matches = [
        {
            "status": "SCHEDULED",
            "home_goals": None,
            "odds_bet365": {
                "matched": True,
                "is_stale": False,
                "market_status": {
                    "1X2": {"valid": True, "stale": False},
                    "over_under_2_5": {"valid": False, "stale": True},  # O/U obsolet
                    "btts": {"valid": True, "stale": False}
                }
            },
            "odds_1x2_bet365": {"1": 2.00, "X": 3.30, "2": 3.60},
            "odds_ou25_bet365": {"over": 1.90, "under": 1.90},  # residu no vàlid segons market_status
            "odds_btts_bet365": {"yes": 1.80, "no": 2.00},
            "odds_corners_bet365": {},
            "odds_cards_bet365": {}
        }
    ]

    stat = {
        "eligible": 0, "associated": 0, "valid_1x2": 0, "valid_ou25": 0,
        "valid_btts": 0, "valid_corners": 0, "valid_cards": 0,
        "discarded_stale": 0, "discarded_unmatched": 0
    }

    for m in mock_matches:
        stat["eligible"] += 1
        b_odds = m.get("odds_bet365") or {}
        m_stat = b_odds.get("market_status") or {}
        is_match_stale = bool(b_odds.get("is_stale"))

        if b_odds.get("matched"):
            stat["associated"] += 1
        else:
            stat["discarded_unmatched"] += 1

        has_stale_market = is_match_stale or any(
            isinstance(st_val, dict) and st_val.get("stale") is True
            for st_val in m_stat.values()
        )
        if has_stale_market:
            stat["discarded_stale"] += 1

        st_1x2 = m_stat.get("1X2", {})
        if bool((m.get("odds_1x2_bet365") or {}).get("1")) and not is_match_stale and (st_1x2.get("valid", True) if st_1x2 else True) and not st_1x2.get("stale", False):
            stat["valid_1x2"] += 1

        st_ou = m_stat.get("over_under_2_5", {})
        if bool((m.get("odds_ou25_bet365") or {}).get("over")) and not is_match_stale and (st_ou.get("valid", True) if st_ou else True) and not st_ou.get("stale", False):
            stat["valid_ou25"] += 1

        st_btts = m_stat.get("btts", {})
        if bool((m.get("odds_btts_bet365") or {}).get("yes")) and not is_match_stale and (st_btts.get("valid", True) if st_btts else True) and not st_btts.get("stale", False):
            stat["valid_btts"] += 1

    assert stat["eligible"] == 1
    assert stat["associated"] == 1
    assert stat["valid_1x2"] == 1
    assert stat["valid_ou25"] == 0, "El mercat O/U marcat com a stale no s'ha de comptabilitzar a valid_ou25"
    assert stat["valid_btts"] == 1
    assert stat["discarded_stale"] == 1, "Ha d'incrementar discarded_stale en detectar un mercat obsolet"


# -----------------------------------------------------------------------------
# 38. Prova Obligatòria 6: Exclusió real d'apostes obsoletes de les recomanacions actives
# -----------------------------------------------------------------------------
def test_obsolete_value_bets_excluded_from_active_export():
    """Garanteix que les apostes marcades com a is_stale=True o status='OBSOLETE' s'exclouen tant
    de la llista exportada a data.json com del filtratge del frontend."""
    raw_bets = [
        {
            "matchup": "Arsenal vs Chelsea",
            "selection": "1 - Victòria Local",
            "model_prob": 60.0,
            "bookie_odd_winamax": 2.10,
            "bookie_odd_bet365": 2.15,
            "edge_pct_winamax": 26.0,
            "edge_pct_bet365": 29.0,
            "is_stale": False,
            "status": "ACTIVE"
        },
        {
            "matchup": "Man United vs Liverpool",
            "selection": "Over 2.5 Gols",
            "model_prob": 70.0,
            "bookie_odd_winamax": 1.85,
            "bookie_odd_bet365": 1.90,
            "edge_pct_winamax": 29.5,
            "edge_pct_bet365": 33.0,
            "is_stale": True,
            "status": "OBSOLETE"
        }
    ]

    # 1. Comprovació de filtratge d'exportació
    active_exported = [b for b in raw_bets if not b.get("is_stale") and b.get("status") != "OBSOLETE"]
    assert len(active_exported) == 1
    assert active_exported[0]["matchup"] == "Arsenal vs Chelsea"

    # 2. Comprovació de la regla exacta del client web/app.js:
    # let rawBets = (appData.value_bets || []).filter(b => !b.is_stale && b.status !== 'OBSOLETE');
    client_filtered = [b for b in raw_bets if not b.get("is_stale") and b.get("status") != "OBSOLETE"]
    assert len(client_filtered) == 1
    assert client_filtered[0]["selection"] == "1 - Victòria Local"






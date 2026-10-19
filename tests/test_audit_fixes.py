"""
tests/test_audit_fixes.py
=========================
Regression tests per als 4 problemes detectats a l'auditoria:
1. Combinades del dia: seleccions i partits duplicats descartats rigorosament.
2. Partit Getafe-Rayo: format net de mercats quan els equips són diccionaris.
3. HyperMotion: deduplicació canònica de partits a SQLite per clau natural i URL.
4. Secció Novetats: idempotència del registre de canvis sense re-importar partits històrics ja existents.
"""

import pytest
import sqlite3
import tempfile
import json
from pathlib import Path
from database.db_manager import DatabaseManager
from engine.value_bet_engine import ValueBetEngine
from engine.combo_bet_engine import ComboBetEngine
from engine.changelog_manager import ChangelogManager


# -----------------------------------------------------------------------------
# 1. TEST VALUE BET ENGINE: Noms nets d'equips (Getafe - Rayo)
# -----------------------------------------------------------------------------
def test_value_bet_engine_dict_and_str_team_names():
    """Verifica que els mercats es generen amb noms nets i mai amb cadenes de diccionaris {'id': ...}."""
    engine = ValueBetEngine()

    # Cas detectat: equips passats com a diccionaris {'id': ..., 'name': ...}
    pred_dict = {
        "home_team": {"id": "GET", "name": "Getafe CF"},
        "away_team": {"id": "RAY", "name": "Rayo Vallecano"},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 55.0}},
        "goals": {
            "prob_1X2": {"1": 42.6, "X": 28.9, "2": 28.5},
            "over_under": {"over_2_5": 46.6, "under_2_5": 53.4},
            "btts": {"yes": 52.3, "no": 47.7},
            "team_goals": {"home_scores": 70.0, "away_scores": 65.0}
        }
    }

    odds = {
        "1X2": {"1": 2.50, "X": 3.10, "2": 3.00},
        "double_chance": {"1X": 1.35, "X2": 1.50, "12": 1.43},
        "over_under_2_5": {"Over": 2.10, "Under": 1.75},
        "btts": {"Sí": 1.95, "No": 1.85}
    }

    result = engine.analyze_match_betting(pred_dict, odds)
    single_markets = result.get("single_markets", [])
    assert len(single_markets) > 0

    for m in single_markets:
        name = m.get("name", "")
        # Cap mercat ha de contenir la representació en cadena d'un diccionari
        assert "{'id':" not in name, f"Mercat mal format amb dict string: {name}"
        assert '{"id":' not in name, f"Mercat mal format amb dict string: {name}"
        assert "Getafe CF" in name or "Rayo Vallecano" in name or "Empat" in name or "Gols" in name or "Córners" in name or "Targetes" in name or "Ambdós" in name

    # Comprovació específica del mercat 12
    dc_12 = next((m for m in single_markets if "12 -" in m["name"]), None)
    assert dc_12 is not None
    assert dc_12["name"] == "12 - Getafe CF o Rayo Vallecano"


# -----------------------------------------------------------------------------
# 2. TEST COMBO BET ENGINE: Cap combinada amb seleccions o partits duplicats
# -----------------------------------------------------------------------------
def test_combo_bet_engine_rejects_duplicate_selections_and_matches():
    """
    Simula una llista de partits que conté un partit repetit (com Tottenham Hotspur vs Coventry)
    i verifica que cap combinada (Safe, Semi, Risky) inclou seleccions repetides ni partits duplicats.
    """
    engine = ComboBetEngine()

    dummy_single_markets = [
        {"name": "1X - Tottenham Hotspur o Empat", "selection_name": "1X - Tottenham Hotspur o Empat", "category": "Doble Oportunitat", "bookie_odd": 1.15, "model_prob": 86.3},
        {"name": "Més de 1.5 Gols", "selection_name": "Més de 1.5 Gols", "category": "Gols", "bookie_odd": 1.25, "model_prob": 82.0},
        {"name": "12 - Tottenham Hotspur o Coventry", "selection_name": "12 - Tottenham Hotspur o Coventry", "category": "Doble Oportunitat", "bookie_odd": 1.21, "model_prob": 77.6}
    ]

    tot_match_1 = {
        "id": "PREMIER_J7_TOT_COV",
        "competition_id": "PREMIER",
        "matchup": "Tottenham Hotspur vs Coventry",
        "home_team": {"id": "TOT", "name": "Tottenham Hotspur"},
        "away_team": {"id": "COV", "name": "Coventry"},
        "date": "19.10.2026 19:00",
        "bet_analysis": {"single_markets": dummy_single_markets}
    }

    # Còpia idèntica / duplicada del mateix partit provinent d'una altra font
    tot_match_dup = {
        "id": "PREMIER_2026-2027_TOTCOV",
        "competition_id": "PREMIER",
        "matchup": "Tottenham Hotspur vs Coventry",
        "home_team": {"id": "TOT", "name": "Tottenham Hotspur"},
        "away_team": {"id": "COV", "name": "Coventry"},
        "date": "19.10.2026 19:00",
        "bet_analysis": {"single_markets": dummy_single_markets}
    }

    celta_match = {
        "id": "HYPERMOTION_J10_CDV_TEN",
        "competition_id": "HYPERMOTION",
        "matchup": "Celta de Vigo B vs CD Tenerife",
        "home_team": {"id": "CDV", "name": "Celta de Vigo B"},
        "away_team": {"id": "TEN", "name": "CD Tenerife"},
        "date": "19.10.2026 18:30",
        "bet_analysis": {
            "single_markets": [
                {"name": "1X - Celta de Vigo B o Empat", "selection_name": "1X - Celta de Vigo B o Empat", "category": "Doble Oportunitat", "bookie_odd": 1.45, "model_prob": 72.7},
                {"name": "Menys de 2.5 Gols", "selection_name": "Menys de 2.5 Gols", "category": "Gols", "bookie_odd": 1.87, "model_prob": 59.0}
            ]
        }
    }

    monza_match = {
        "id": "SERIEA_J7_MON_CAG",
        "competition_id": "SERIEA",
        "matchup": "AC Monza vs Cagliari Calcio",
        "home_team": {"id": "MON", "name": "AC Monza"},
        "away_team": {"id": "CAG", "name": "Cagliari Calcio"},
        "date": "19.10.2026 16:30",
        "bet_analysis": {
            "single_markets": [
                {"name": "12 - AC Monza o Cagliari Calcio", "selection_name": "12 - AC Monza o Cagliari Calcio", "category": "Doble Oportunitat", "bookie_odd": 1.40, "model_prob": 71.7},
                {"name": "Ambdós Equips Marquen (BTTS Sí)", "selection_name": "Ambdós Equips Marquen (BTTS Sí)", "category": "BTTS", "bookie_odd": 1.93, "model_prob": 54.1}
            ]
        }
    }

    # Passar la llista amb el duplicat flagrant
    day_matches = [tot_match_1, tot_match_dup, celta_match, monza_match]

    suite = engine.generate_daily_combos_suite(day_matches)

    for profile_cat in ["safe", "semi", "risky"]:
        combos = suite.get(profile_cat, [])
        for combo in combos:
            if not combo or not combo.get("legs"):
                continue
            legs = combo["legs"]
            leg_keys = [engine._get_leg_key(l) for l in legs]
            # Cap aposta pot tenir la mateixa selecció repetida!
            assert len(leg_keys) == len(set(leg_keys)), f"Seleccions duplicades trobades a {combo.get('profile')}: {leg_keys}"

            # Validar que compleix la regla is_valid_combo_legs
            assert engine.is_valid_combo_legs(legs, allow_duos=True), f"Combinada no vàlida a {combo.get('profile')}"


# -----------------------------------------------------------------------------
# 3. TEST IDEMPOTÈNCIA DE GENERACIÓ DE COMBINADES
# -----------------------------------------------------------------------------
def test_combo_generation_is_idempotent():
    """Verifica que una segona execució del generador sobre els mateixos partits dóna resultats consistents."""
    engine = ComboBetEngine()

    matches = [
        {
            "id": "LALIGA_J9_GET_RAY",
            "competition_id": "LALIGA",
            "matchup": "Getafe CF vs Rayo Vallecano",
            "home_team": {"id": "GET", "name": "Getafe CF"},
            "away_team": {"id": "RAY", "name": "Rayo Vallecano"},
            "date": "19.10.2026 21:00",
            "bet_analysis": {
                "single_markets": [
                    {"name": "12 - Getafe CF o Rayo Vallecano", "category": "Doble Oportunitat", "bookie_odd": 1.43, "model_prob": 70.7},
                    {"name": "Menys de 2.5 Gols", "category": "Gols", "bookie_odd": 1.65, "model_prob": 62.0}
                ]
            }
        },
        {
            "id": "PREMIER_J7_TOT_COV",
            "competition_id": "PREMIER",
            "matchup": "Tottenham Hotspur vs Coventry",
            "home_team": {"id": "TOT", "name": "Tottenham Hotspur"},
            "away_team": {"id": "COV", "name": "Coventry"},
            "date": "19.10.2026 19:00",
            "bet_analysis": {
                "single_markets": [
                    {"name": "1X - Tottenham Hotspur o Empat", "category": "Doble Oportunitat", "bookie_odd": 1.15, "model_prob": 86.3},
                    {"name": "Més de 1.5 Gols", "category": "Gols", "bookie_odd": 1.25, "model_prob": 82.0}
                ]
            }
        }
    ]

    suite1 = engine.generate_daily_combos_suite(matches)
    suite2 = engine.generate_daily_combos_suite(matches)

    # El nombre de combinades generades a cada categoria ha de ser idèntic
    assert len(suite1["safe"]) == len(suite2["safe"])
    assert len(suite1["semi"]) == len(suite2["semi"])
    assert len(suite1["risky"]) == len(suite2["risky"])

    # Cap combinada ha de tenir seleccions duplicades
    for cat in ["safe", "semi", "risky"]:
        for c in suite2[cat]:
            if c and c.get("legs"):
                keys = [engine._get_leg_key(l) for l in c["legs"]]
                assert len(keys) == len(set(keys))


# -----------------------------------------------------------------------------
# 4. TEST SQLITE DEDUPLICACIÓ CANÒNICA (Celta B - Tenerife)
# -----------------------------------------------------------------------------
def test_db_save_match_deduplication_different_id_formats():
    """
    Comprova que desar el mateix partit dues vegades amb formats d'ID diferents
    (ex: HYPERMOTION_J10_CDV_TEN vs HYPERMOTION_2026-2027_YLuihUR1) actualitza la fila
    existent i NO crea un registre duplicat a la base de dades.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db_path = tf.name

    db = DatabaseManager(temp_db_path)

    # 1. Inserir primer partit amb ID tradicional
    m1 = {
        "id": "HYPERMOTION_J10_CDV_TEN",
        "competition_id": "HYPERMOTION",
        "season": "2026-2027",
        "jornada": 10,
        "date_time": "19.10.2026 20:30",
        "home_team_id": "CDV",
        "away_team_id": "TEN",
        "status": "SCHEDULED",
        "url": "https://www.flashscore.es/partido/YLuihUR1/#/estadisticas-del-partido/0"
    }
    db.save_match(m1)

    with db.get_connection() as conn:
        cnt = conn.cursor().execute("SELECT count(*) FROM matches WHERE competition_id='HYPERMOTION' AND jornada=10").fetchone()[0]
        assert cnt == 1

    # 2. Inserir el mateix partit provinent de LiveCrawler amb ID basat en codi Flashscore
    m2 = {
        "id": "HYPERMOTION_2026-2027_YLuihUR1",
        "competition_id": "HYPERMOTION",
        "season": "2026-2027",
        "jornada": 10,
        "date_time": "19.10.2026 20:30",
        "home_team_id": "CDV",
        "away_team_id": "TEN",
        "status": "LIVE",
        "home_goals": 1,
        "away_goals": 0,
        "url": "https://www.flashscore.es/partido/YLuihUR1/#/estadisticas-del-partido/0"
    }
    db.save_match(m2)

    # 3. Comprovar que SEGUEIX havent-hi exactament 1 sola fila a SQLite
    with db.get_connection() as conn:
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, status, home_goals, away_goals FROM matches WHERE competition_id='HYPERMOTION' AND jornada=10").fetchall()
        assert len(rows) == 1, f"S'ha generat un duplicat a SQLite! Files trobades: {len(rows)}"
        row = rows[0]
        # L'estat s'ha d'haver actualitzat correctament a LIVE amb els nous gols
        assert row["status"] == "LIVE"
        assert row["home_goals"] == 1
        assert row["away_goals"] == 0


# -----------------------------------------------------------------------------
# 5. TEST CHANGELOG MANAGER: Idempotència i sense partits repetits
# -----------------------------------------------------------------------------
def test_changelog_manager_does_not_duplicate_past_matches():
    """
    Verifica que ChangelogManager no torna a presentar partits antics ja registrats
    quan el pipeline s'executa repetidament.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tf:
        json.dump([
            {
                "date": "2026-10-18",
                "timestamp": "2026-10-18T10:00:00",
                "title": "Actualització 18/10",
                "items": ["⚽ S'han ingesta resultats oficials de 1 partit:"],
                "matches": [
                    {
                        "match_id": "LALIGA_J9_FCB_GET",
                        "competition_id": "LALIGA",
                        "jornada": 9,
                        "home_team": "FC Barcelona",
                        "away_team": "Getafe CF",
                        "score": "3 - 0"
                    }
                ]
            }
        ], tf)
        temp_changelog_path = tf.name

    cm = ChangelogManager(temp_changelog_path)

    # Execució del dia 19/10: intent d'enviar el mateix partit que ja es va informar el 18/10
    match_already_reported = {
        "match_id": "LALIGA_J9_FCB_GET",
        "competition_id": "LALIGA",
        "jornada": 9,
        "home_team": "FC Barcelona",
        "away_team": "Getafe CF",
        "score": "3 - 0"
    }

    match_new = {
        "match_id": "LALIGA_J9_GET_RAY",
        "competition_id": "LALIGA",
        "jornada": 9,
        "home_team": "Getafe CF",
        "away_team": "Rayo Vallecano",
        "score": "1 - 1"
    }

    cm.record_pipeline_execution(
        date_str="2026-10-19",
        ingested_matches=[match_already_reported, match_new]
    )

    entries = cm.load_entries()
    entry_today = next(e for e in entries if e["date"] == "2026-10-19")
    reported_today = entry_today["matches"]

    # Només s'ha d'haver acceptat el partit nou (GET_RAY), mai el que ja es va reportar ahir!
    assert len(reported_today) == 1
    assert reported_today[0]["match_id"] == "LALIGA_J9_GET_RAY"

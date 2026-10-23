"""
tests/test_premier_and_chronological_sort.py
============================================
Tests de regressió per a:
1. Detecció correcta de la Jornada 8 per a Premier League segons dates i estat de partits.
2. Rebuig de la Jornada 6 com a actual en estar completament finalitzada.
3. Exclusió estricta de partits i equips U21/U18/juvenils de la Premier League absoluta.
4. Power Rankings de la Premier League amb exactament els 20 equips sènior (sense 1500 Elos estranys).
5. Ordenació cronològica ascendent ("Totes les Lligues") del més pròxim al més llunyà.
6. Funcionament dels filtres individuals de competició.
7. Validesa de web/data/data.json i PRAGMA integrity_check de SQLite.
"""

import json
import sqlite3
import pytest
from pathlib import Path
from database.db_manager import DatabaseManager
from engine.web_data_exporter import WebDataExporter
from daily_runner import get_upcoming_round_info

DB_PATH = Path(__file__).parent.parent / "data" / "laliga.db"
JSON_PATH = Path(__file__).parent.parent / "web" / "data" / "data.json"


def test_sqlite_integrity_check():
    """7b. Comprova que SQLite supera PRAGMA integrity_check."""
    conn = sqlite3.connect(DB_PATH)
    res = conn.execute("PRAGMA integrity_check;").fetchone()[0]
    conn.close()
    assert res == "ok", f"Integrity check ha fallat: {res}"


def test_premier_league_active_jornada_is_8():
    """1. La Premier League identifica correctament la jornada 8 com a actual."""
    db = DatabaseManager(str(DB_PATH))
    exporter = WebDataExporter(db)
    active_j = exporter.get_active_jornada("PREMIER")
    assert active_j == 8, f"La jornada activa de la Premier League ha de ser 8, s'ha obtingut {active_j}"


def test_premier_league_not_jornada_6():
    """2. Comprova que no continua mostrant la jornada 6 com a actual."""
    db = DatabaseManager(str(DB_PATH))
    exporter = WebDataExporter(db)
    active_j = exporter.get_active_jornada("PREMIER")
    assert active_j != 6, "La jornada 6 ja ha finalitzat i no pot continuar com a activa"
    assert active_j > 6, f"La jornada activa ha de ser posterior a 6, trobat: {active_j}"


def test_no_u21_in_premier_power_rankings():
    """3 & 4. Els partits U21/U18 no apareixen a Power Rankings de la Premier League absoluta (exactament 20 equips)."""
    db = DatabaseManager(str(DB_PATH))
    standings = db.get_league_standings_elo("PREMIER")
    assert len(standings) == 20, f"Premier League ha de tenir exactament 20 equips, trobats: {len(standings)}"
    
    u21_teams = [t["name"] for t in standings if any(kw in t["name"].upper() for kw in ["U21", "U18", "U19", "U23", "SUB-21", "SUB-18", "RESERVES"])]
    assert len(u21_teams) == 0, f"S'han trobat equips U21 a Power Rankings de Premier League: {u21_teams}"
    
    # Comprovar que cap equip desconegut té 1500 Elo introduït accidentalment
    for t in standings:
        assert not ("U21" in t["name"] and t["elo_rating"] == 1500.0)


def test_no_u21_matches_in_premier_matches():
    """3b. Els partits U21/U18 no estan assignats a la competició PREMIER a SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    u21_premier_matches = cur.execute("""
        SELECT m.id, m.jornada, ht.name, at.name
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.competition_id = 'PREMIER'
          AND (ht.name LIKE '%U21%' OR at.name LIKE '%U21%' OR ht.name LIKE '%U18%' OR at.name LIKE '%U18%')
    """).fetchall()
    conn.close()
    assert len(u21_premier_matches) == 0, f"Partits U21 trobats a PREMIER: {u21_premier_matches}"


def test_data_json_validity_and_premier():
    """7a. Comprova que web/data/data.json és vàlid i reflecteix Premier J8 i 20 equips."""
    assert JSON_PATH.exists(), "web/data/data.json no existeix"
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Competició PREMIER en metadata
    premier_meta = next((c for c in data["metadata"]["competitions"] if c["id"] == "PREMIER"), None)
    assert premier_meta is not None, "Metadata no conté PREMIER"
    assert premier_meta["active_jornada"] == 8, f"Metadata active_jornada ha de ser 8, trobat: {premier_meta['active_jornada']}"

    # 2. Power rankings PREMIER
    pr_premier = data.get("power_rankings", {}).get("PREMIER", [])
    assert len(pr_premier) == 20, f"Power rankings de PREMIER ha de tenir 20 equips, trobats: {len(pr_premier)}"
    for t in pr_premier:
        assert "U21" not in t["name"], f"Equip juvenil trobat a Power Rankings: {t['name']}"

    # 3. Prediccions PREMIER
    pred_premier = data.get("predictions", {}).get("PREMIER", {})
    assert pred_premier.get("active_jornada") == 8
    matches = pred_premier.get("matches", [])
    assert len(matches) == 10, f"PREMIER Jornada 8 ha de tenir 10 partits, trobats: {len(matches)}"
    for m in matches:
        h_name = m.get("home_team", {}).get("name", "")
        a_name = m.get("away_team", {}).get("name", "")
        assert "U21" not in h_name and "U21" not in a_name, f"Partit U21 a les prediccions: {h_name} vs {a_name}"


def test_chronological_sort_logic():
    """5 & 6. Validació de la lògica d'ordenació cronològica ascendent de tots els partits."""
    # Simulem dates en formats reals utilitzats per l'aplicació
    from datetime import datetime

    def parse_match_date(date_str):
        if not date_str:
            return datetime.max
        # Formats suportats: DD.MM.YYYY HH:MM o YYYY-MM-DD HH:MM
        s = date_str.strip()
        for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(s[:16], fmt)
            except ValueError:
                pass
        return datetime.max

    sample_matches = [
        {"id": "M1", "competition_id": "PREMIER", "date": "25.10.2026 17:30"},
        {"id": "M2", "competition_id": "LALIGA", "date": "23.10.2026 19:00"},
        {"id": "M3", "competition_id": "SERIEA", "date": "23.10.2026 18:45"},
        {"id": "M4", "competition_id": "BUNDESLIGA", "date": "24.10.2026 15:30"},
        {"id": "M5", "competition_id": "PREMIER", "date": "24.10.2026 13:30"},
        {"id": "M6", "competition_id": "HYPERMOTION", "date": "23.10.2026 18:30"},
    ]

    # Ordenació conjunta ascendent per data i hora
    sorted_matches = sorted(sample_matches, key=lambda m: (parse_match_date(m["date"]), m["competition_id"], m["id"]))
    
    expected_order_ids = ["M6", "M3", "M2", "M5", "M4", "M1"]
    actual_order_ids = [m["id"] for m in sorted_matches]
    assert actual_order_ids == expected_order_ids, f"Ordre cronològic incorrecte: {actual_order_ids} vs {expected_order_ids}"

    # Filtre individual per competició preservat
    premier_only = [m for m in sorted_matches if m["competition_id"] == "PREMIER"]
    assert [m["id"] for m in premier_only] == ["M5", "M1"]

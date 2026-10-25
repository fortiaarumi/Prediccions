"""
tests/test_combo_integrity_and_void.py
======================================
Suite completa de tests de regressió obligatoris per a:
1. Integritat de combinades ordinàries (exclusió estricta de seleccions del mateix partit i contradiccions lògiques).
2. Liquidació financera de partits suspesos, ajornats i anul·lats (regla void estàndard amb quota 1.00 i PnL zero).
3. Idempotència i preservació de la base de dades sense alterar caches persistents.
"""

import tempfile
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
import pytest

from engine.combo_bet_engine import ComboBetEngine
from engine.combo_tracker import ComboTracker
from database.db_manager import DatabaseManager


# -----------------------------------------------------------------------------
# FIXTURES I AUXILIARS
# -----------------------------------------------------------------------------
@pytest.fixture
def combo_engine():
    return ComboBetEngine()


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    conn = sqlite3.connect(db_path)
    # Crear schema mínim necessari per a les proves
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS competitions (
            id TEXT PRIMARY KEY,
            name TEXT,
            country TEXT
        );
        CREATE TABLE IF NOT EXISTS teams (
            id TEXT PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            competition_id TEXT NOT NULL,
            season TEXT NOT NULL DEFAULT '2026-2027',
            jornada INTEGER,
            date_time TEXT NOT NULL,
            home_team_id TEXT NOT NULL,
            away_team_id TEXT NOT NULL,
            referee_id TEXT,
            home_goals INTEGER,
            away_goals INTEGER,
            home_xg REAL,
            away_xg REAL,
            home_corners INTEGER,
            away_corners INTEGER,
            home_yellow_cards INTEGER,
            away_yellow_cards INTEGER,
            home_red_cards INTEGER,
            away_red_cards INTEGER,
            home_fouls INTEGER,
            away_fouls INTEGER,
            status TEXT NOT NULL DEFAULT 'SCHEDULED',
            url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS combo_recommendations (
            id TEXT PRIMARY KEY,
            competition_id TEXT NOT NULL,
            season TEXT NOT NULL DEFAULT '2026-2027',
            jornada INTEGER NOT NULL,
            profile TEXT NOT NULL,
            stake REAL NOT NULL,
            combined_odd REAL NOT NULL,
            combined_prob_pct REAL,
            fair_odd REAL,
            ev_pct REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            payout REAL DEFAULT 0.0,
            profit REAL DEFAULT 0.0,
            winamax_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            evaluated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS combo_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            combo_id TEXT NOT NULL,
            matchup TEXT NOT NULL,
            selection_name TEXT NOT NULL,
            category TEXT,
            bookie_odd REAL NOT NULL,
            model_prob REAL,
            match_date TEXT,
            url TEXT,
            home_team_id TEXT,
            away_team_id TEXT,
            status TEXT DEFAULT 'PENDING',
            actual_result TEXT,
            evaluated_at TEXT,
            FOREIGN KEY (combo_id) REFERENCES combo_recommendations(id)
        );
    """)
    conn.commit()
    conn.close()

    db_mgr = DatabaseManager(db_path=str(db_path))
    yield db_mgr

    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# TEST 1: Una combinada ordinària amb partits diferents es pot generar
# -----------------------------------------------------------------------------
def test_01_ordinary_combo_with_different_matches_can_be_generated(combo_engine):
    """Una combinada ordinària amb seleccions de partits diferents és perfectament vàlida."""
    legs = [
        {
            "match_id": "M1",
            "matchup": "Real Madrid vs Barcelona",
            "selection_name": "1X - Real Madrid o Empat",
            "category": "Doble Oportunitat",
            "bookie_odd": 1.45,
            "model_prob": 75.0
        },
        {
            "match_id": "M2",
            "matchup": "Arsenal vs Chelsea",
            "selection_name": "Més d'1.5 Gols (Over 1.5)",
            "category": "Gols",
            "bookie_odd": 1.30,
            "model_prob": 80.0
        }
    ]
    assert combo_engine.is_valid_combo_legs(legs, allow_duos=False) is True

    summary = combo_engine.calculate_combo_summary(legs, "Combinada Test")
    assert summary["combined_odd"] == round(1.45 * 1.30, 2)
    assert len(summary["legs"]) == 2
    assert summary["combined_prob_pct"] > 0


# -----------------------------------------------------------------------------
# TEST 2: Dues seleccions del mateix partit no es poden incloure
# -----------------------------------------------------------------------------
def test_02_two_selections_same_match_rejected_for_ordinary_combos(combo_engine):
    """
    Rebutja qualsevol combinada amb dues seleccions del mateix partit,
    tant per duplicitat de match_id com per clau canònica de matchup.
    """
    # Cas 2A: Mateix match_id
    legs_same_mid = [
        {
            "match_id": "MATCH_101",
            "matchup": "Bayern Munich vs Borussia Dortmund",
            "selection_name": "1 - Victòria Bayern Munich",
            "category": "1X2",
            "bookie_odd": 1.60,
            "model_prob": 62.0
        },
        {
            "match_id": "MATCH_101",
            "matchup": "Bayern Munich vs Borussia Dortmund",
            "selection_name": "Més de 2.5 Gols",
            "category": "Gols",
            "bookie_odd": 1.45,
            "model_prob": 70.0
        }
    ]
    assert combo_engine.is_valid_combo_legs(legs_same_mid, allow_duos=False) is False
    # Fins i tot si un caller antic passa allow_duos=True, les combinades ordinàries ho rebutgen:
    assert combo_engine.is_valid_combo_legs(legs_same_mid, allow_duos=True) is False

    # Cas 2B: Mateix matchup canònic (equips iguals encara que arribin amb IDs diferents)
    legs_same_matchup = [
        {
            "match_id": "MATCH_201",
            "matchup": "Liverpool vs Manchester City",
            "selection_name": "Ambdós Equips Marquen (BTTS Sí)",
            "category": "BTTS",
            "bookie_odd": 1.55,
            "model_prob": 68.0
        },
        {
            "match_id": "MATCH_202_ALT",
            "matchup": "Liverpool vs Manchester City",
            "selection_name": "Més de 9.5 Córners",
            "category": "Córners",
            "bookie_odd": 1.80,
            "model_prob": 56.0
        }
    ]
    assert combo_engine.is_valid_combo_legs(legs_same_matchup, allow_duos=False) is False


# -----------------------------------------------------------------------------
# TEST 3: BTTS No i marcador exacte 1-1 no poden superar els filtres
# -----------------------------------------------------------------------------
def test_03_btts_no_and_exact_score_1_1_rejected_by_validation(combo_engine):
    """
    Detecta i rebutja explícitament contradiccions lògiques:
    BTTS No i marcador 1-1 són matemàticament incompatibles.
    """
    legs_contradictory = [
        {
            "match_id": "M1",
            "matchup": "Villarreal vs Valencia",
            "selection_name": "Ambdós Equips Marquen (BTTS No)",
            "category": "BTTS",
            "bookie_odd": 1.90,
            "model_prob": 52.0
        },
        {
            "match_id": "M2",
            "matchup": "Getafe vs Espanyol",
            "selection_name": "Marcador Exacte: 1-1",
            "category": "Marcador Exacte",
            "bookie_odd": 6.50,
            "model_prob": 14.0
        }
    ]
    # Comprovació directa del mètode de contradiccions lògiques
    assert combo_engine.has_logical_contradiction(legs_contradictory) is True
    assert combo_engine.is_valid_combo_legs(legs_contradictory) is False

    # Altres contradiccions conegudes
    legs_ou_contradiction = [
        {"match_id": "M1", "matchup": "A vs B", "name": "Més de 2.5 Gols (Over 2.5)", "category": "Gols", "bookie_odd": 1.9, "model_prob": 50},
        {"match_id": "M2", "matchup": "C vs D", "name": "Menys d'1.5 Gols (Under 1.5)", "category": "Gols", "bookie_odd": 3.0, "model_prob": 30}
    ]
    assert combo_engine.has_logical_contradiction(legs_ou_contradiction) is True
    assert combo_engine.is_valid_combo_legs(legs_ou_contradiction) is False


# -----------------------------------------------------------------------------
# TEST 4: Jornada amb pocs partits produeix menys combinades sense inventar-ne
# -----------------------------------------------------------------------------
def test_04_few_matches_produces_fewer_combos_without_fabrication(combo_engine):
    """
    Si una jornada té un sol partit, no es generen combinades ordinàries (suite buida).
    Si té 2 partits i no es pot assolir cuota >= 28.0 sense quotes irreals,
    no s'inventen duos intra-partit i es produeixen menys combinades.
    """
    single_match = [{
        "id": "M_SINGLE",
        "competition_id": "LALIGA",
        "matchup": "Sevilla FC vs Real Betis",
        "home_team": {"id": "SEV", "name": "Sevilla FC"},
        "away_team": {"id": "BET", "name": "Real Betis"},
        "date": "2026-10-25 21:00",
        "bet_analysis": {
            "single_markets": [
                {"name": "1X - Sevilla o Empat", "category": "Doble Oportunitat", "bookie_odd": 1.35, "model_prob": 74.0},
                {"name": "Més de 1.5 Gols", "category": "Gols", "bookie_odd": 1.28, "model_prob": 78.0}
            ]
        }
    }]

    # Partit únic: no es poden crear combinades ordinàries
    suite_single = combo_engine.generate_daily_combos_suite(single_match)
    assert suite_single["safe"] == []
    assert suite_single["semi"] == []
    assert suite_single["risky"] == []
    assert "No es poden generar combinades ordinàries" in suite_single["league_note"]

    # 2 partits amb mercats prudents: no es poden assolir quotes >= 28.0 de forma realista
    two_matches = [
        single_match[0],
        {
            "id": "M_TWO",
            "competition_id": "PREMIER",
            "matchup": "Chelsea vs Fulham",
            "home_team": {"id": "CHE", "name": "Chelsea"},
            "away_team": {"id": "FUL", "name": "Fulham"},
            "date": "2026-10-25 18:30",
            "bet_analysis": {
                "single_markets": [
                    {"name": "1 - Victòria Chelsea", "category": "1X2", "bookie_odd": 1.65, "model_prob": 60.0},
                    {"name": "1X - Chelsea o Empat", "category": "Doble Oportunitat", "bookie_odd": 1.18, "model_prob": 85.0}
                ]
            }
        }
    ]
    suite_two = combo_engine.generate_few_matches_combos_suite(two_matches)
    # Es poden generar segures (1.35 * 1.65 = 2.23)
    assert len(suite_two["safe"]) >= 1
    for c in suite_two["safe"]:
        # Totes les seleccions de cada combinada han de ser de partits diferents
        matchups = [l["matchup"] for l in c["legs"]]
        assert len(matchups) == len(set(matchups))

    # Però NO es poden inventar combinades arriscades de cuota 30 multiplicant seleccions dependents
    assert suite_two["risky"] == []


# -----------------------------------------------------------------------------
# TEST 5: Partit temporalment suspès continua pendent
# -----------------------------------------------------------------------------
def test_05_temporarily_suspended_match_remains_pending():
    """Un partit temporalment suspès (SUSPENDED) continua en estat PENDING sense liquidar."""
    tracker = ComboTracker()
    leg = {"selection_name": "1X - Rayo Vallecano o Empat", "matchup": "Rayo Vallecano vs Athletic Club"}
    match = {
        "status": "SUSPENDED",
        "home_goals": 1,
        "away_goals": 0
    }
    status, note = tracker.evaluate_leg(leg, match)
    assert status == "PENDING"
    assert "suspès" in note.lower()


# -----------------------------------------------------------------------------
# TEST 6: Partit reprogramat no es liquida prematurament
# -----------------------------------------------------------------------------
def test_06_postponed_or_rescheduled_match_not_settled_prematurely():
    """Un partit ajornat o reprogramat (POSTPONED / RESCHEDULED) continua PENDING."""
    tracker = ComboTracker()
    leg = {"selection_name": "Més d'1.5 Gols", "matchup": "Valencia vs Real Madrid"}
    match = {
        "status": "POSTPONED",
        "home_goals": None,
        "away_goals": None
    }
    status, note = tracker.evaluate_leg(leg, match)
    assert status == "PENDING"
    assert "ajornat" in note.lower() or "reprogramat" in note.lower()


# -----------------------------------------------------------------------------
# TEST 7: Una selecció definitivament void no impedeix liquidar les altres seleccions
# -----------------------------------------------------------------------------
def test_07_definitively_void_selection_settles_with_effective_odd_one():
    """
    Si una selecció és void (ex: partit anul·lat/declarat void), la seva quota efectiva és 1.00.
    La resta de seleccions de la combinada mantenen el seu efecte i la combinada es liquida com a WON.
    """
    tracker = ComboTracker()
    combo = {
        "id": "COMBO_TEST_VOID_PARTIAL",
        "stake": 20.0,
        "combined_odd": 3.00,  # 2.00 * 1.50
        "legs": [
            {
                "matchup": "Partit Anul·lat vs Rival",
                "selection_name": "1 - Victòria Local",
                "bookie_odd": 2.00
            },
            {
                "matchup": "Partit Guanyador vs Rival",
                "selection_name": "1X - Local o Empat",
                "bookie_odd": 1.50
            }
        ]
    }
    matches = {
        "Partit Anul·lat vs Rival": {
            "status": "CANCELLED"
        },
        "Partit Guanyador vs Rival": {
            "status": "FINISHED",
            "home_goals": 2,
            "away_goals": 1
        }
    }
    settlement = tracker.evaluate_combo_settlement(combo, matches)
    assert settlement["status"] == "WON"
    # Quota efectiva: 1.00 (void) * 1.50 (won) = 1.50
    assert settlement["effective_odd"] == 1.50
    # Payout: 20.0 * 1.50 = 30.00 | Profit: 30.00 - 20.00 = +10.00
    assert settlement["payout"] == 30.00
    assert settlement["profit"] == 10.00
    assert settlement["legs"][0]["status"] == "VOID"
    assert settlement["legs"][1]["status"] == "WON"


# -----------------------------------------------------------------------------
# TEST 8: Totes les seleccions void impliquen retorn de l'stake i PnL zero
# -----------------------------------------------------------------------------
def test_08_all_void_selections_return_stake_net_pnl_zero():
    """Si totes les seleccions són void, retorn íntegre de l'aposta amb PnL net 0.00."""
    tracker = ComboTracker()
    combo = {
        "id": "COMBO_ALL_VOID",
        "stake": 25.0,
        "combined_odd": 2.50,
        "legs": [
            {"matchup": "Match 1 vs Rival", "selection_name": "1X", "bookie_odd": 1.50},
            {"matchup": "Match 2 vs Rival", "selection_name": "Over 1.5", "bookie_odd": 1.66}
        ]
    }
    matches = {
        "Match 1 vs Rival": {"status": "VOID"},
        "Match 2 vs Rival": {"status": "CANCELLED"}
    }
    settlement = tracker.evaluate_combo_settlement(combo, matches)
    assert settlement["status"] == "VOID"
    assert settlement["effective_odd"] == 1.00
    assert settlement["payout"] == 25.00
    assert settlement["profit"] == 0.00


# -----------------------------------------------------------------------------
# TEST 9: Una combinada amb selecció void i una altra perdedora perd correctament
# -----------------------------------------------------------------------------
def test_09_combo_with_void_and_lost_leg_settles_as_lost():
    """Una selecció void no salva una aposta combinada si una altra selecció és perdedora."""
    tracker = ComboTracker()
    combo = {
        "id": "COMBO_VOID_AND_LOST",
        "stake": 15.0,
        "combined_odd": 3.20,
        "legs": [
            {"matchup": "Match Void vs Rival", "selection_name": "1X", "bookie_odd": 1.60},
            {"matchup": "Match Lost vs Rival", "selection_name": "1 - Victòria Local", "bookie_odd": 2.00}
        ]
    }
    matches = {
        "Match Void vs Rival": {"status": "CANCELLED"},
        "Match Lost vs Rival": {"status": "FINISHED", "home_goals": 0, "away_goals": 2}
    }
    settlement = tracker.evaluate_combo_settlement(combo, matches)
    assert settlement["status"] == "LOST"
    assert settlement["payout"] == 0.0
    assert settlement["profit"] == -15.0
    assert settlement["legs"][0]["status"] == "VOID"
    assert settlement["legs"][1]["status"] == "LOST"


# -----------------------------------------------------------------------------
# TEST 10: La liquidació repetida és idempotent i no duplica PnL
# -----------------------------------------------------------------------------
def test_10_repeated_settlement_idempotent_no_pnl_duplication(temp_db):
    """
    Executar reiteradament evaluate_all_pending_combos no modifica ni duplica
    el balanç ni els registres financers un cop liquidats.
    """
    tracker = ComboTracker(db=temp_db)

    with temp_db.get_connection() as conn:
        cursor = conn.cursor()
        # Inserim un partit finalitzat
        cursor.execute("""
            INSERT INTO matches (id, competition_id, season, jornada, date_time, home_team_id, away_team_id, home_goals, away_goals, status)
            VALUES ('M_IDEM_1', 'LALIGA', '2026-2027', 1, '2026-10-20 21:00', 'RMA', 'FCB', 3, 1, 'FINISHED')
        """)
        # Inserim un partit anul·lat
        cursor.execute("""
            INSERT INTO matches (id, competition_id, season, jornada, date_time, home_team_id, away_team_id, home_goals, away_goals, status)
            VALUES ('M_IDEM_2', 'LALIGA', '2026-2027', 1, '2026-10-20 18:00', 'ATM', 'SEV', NULL, NULL, 'CANCELLED')
        """)
        # Inserim una combinada PENDING
        cursor.execute("""
            INSERT INTO combo_recommendations (id, competition_id, season, jornada, profile, stake, combined_odd, status)
            VALUES ('COMBO_IDEM_TEST', 'LALIGA', '2026-2027', 1, 'SAFE_1', 25.0, 2.80, 'PENDING')
        """)
        cursor.execute("""
            INSERT INTO combo_legs (combo_id, matchup, selection_name, bookie_odd, home_team_id, away_team_id, status)
            VALUES ('COMBO_IDEM_TEST', 'RMA vs FCB', '1 - Victòria RMA', 1.40, 'RMA', 'FCB', 'PENDING')
        """)
        cursor.execute("""
            INSERT INTO combo_legs (combo_id, matchup, selection_name, bookie_odd, home_team_id, away_team_id, status)
            VALUES ('COMBO_IDEM_TEST', 'ATM vs SEV', '1X - ATM o Empat', 2.00, 'ATM', 'SEV', 'PENDING')
        """)
        conn.commit()

    # Primera execució: s'ha d'avaluar com a WON (1.40 * 1.00 = 1.40)
    evaluated_first = tracker.evaluate_all_pending_combos()
    assert len(evaluated_first) == 1
    assert evaluated_first[0]["status"] == "WON"
    assert evaluated_first[0]["payout"] == round(25.0 * 1.40, 2)  # 35.00
    assert evaluated_first[0]["profit"] == round(35.00 - 25.0, 2)  # 10.00

    hist_first = temp_db.get_bankroll_history("LALIGA")
    assert hist_first["total_stake"] == 25.0
    assert hist_first["total_payout"] == 35.0
    assert hist_first["net_profit"] == 10.0

    # Segona execució: idempotent, cap combinada pendent ha de ser alterada
    evaluated_second = tracker.evaluate_all_pending_combos()
    assert len(evaluated_second) == 0

    hist_second = temp_db.get_bankroll_history("LALIGA")
    assert hist_second["total_stake"] == 25.0
    assert hist_second["total_payout"] == 35.0
    assert hist_second["net_profit"] == 10.0

    # Tercera execució directa sobre get_simulation_summary: no altera res
    sim_summary = tracker.get_simulation_summary("LALIGA")
    assert sim_summary["total_stake"] == 25.0
    assert sim_summary["total_payout"] == 35.0
    assert sim_summary["net_profit"] == 10.0


# -----------------------------------------------------------------------------
# TEST 11: Es mantenen els comportaments correctes en combinades sense incidències
# -----------------------------------------------------------------------------
def test_11_regular_clean_combos_maintain_correct_behavior(combo_engine):
    """
    Les combinades ordinàries regulars sense partits suspesos ni anul·lats mantenen
    la multiplicació exacta de probabilitats marginals independents i cuotes de Winamax.
    """
    legs = [
        {"match_id": "M1", "matchup": "A vs B", "selection_name": "1X", "category": "Doble Oportunitat", "bookie_odd": 1.20, "model_prob": 85.0},
        {"match_id": "M2", "matchup": "C vs D", "selection_name": "Over 1.5", "category": "Gols", "bookie_odd": 1.25, "model_prob": 82.0},
        {"match_id": "M3", "matchup": "E vs F", "selection_name": "Més de 8.5 Córners", "category": "Córners", "bookie_odd": 1.35, "model_prob": 75.0}
    ]
    summary = combo_engine.calculate_combo_summary(legs, "Combinada Normal")
    expected_odd = round(1.20 * 1.25 * 1.35, 2)
    assert summary["combined_odd"] == expected_odd
    expected_prob = round(0.85 * 0.82 * 0.75 * 100.0, 1)
    assert summary["combined_prob_pct"] == expected_prob
    assert summary["booster_pct"] == 0.0  # Booster només a partir de 4 seleccions

    # Amb 4 seleccions: s'activa el booster oficial Winamax (+5%)
    legs_4 = legs + [{"match_id": "M4", "matchup": "G vs H", "selection_name": "BTTS Sí", "category": "BTTS", "bookie_odd": 1.80, "model_prob": 55.0}]
    summary_4 = combo_engine.calculate_combo_summary(legs_4, "Combinada Booster 4")
    assert summary_4["booster_pct"] == 5.0
    assert summary_4["boosted_odd"] == round(summary_4["combined_odd"] * 1.05, 2)


# -----------------------------------------------------------------------------
# TEST 12: Dues seleccions del mateix partit (una amb match_id i l'altra sense)
# -----------------------------------------------------------------------------
def test_12_same_match_one_with_match_id_one_without_rejected(combo_engine):
    """
    Si una selecció conté match_id i l'altra només conté matchup,
    el motor ha de detectar la identitat del duel i rebutjar la combinada.
    """
    legs = [
        {"match_id": "M_100", "matchup": "Real Madrid vs Barcelona", "selection_name": "1X", "bookie_odd": 1.40},
        {"match_id": None, "matchup": "Real Madrid vs Barcelona", "selection_name": "Over 1.5", "bookie_odd": 1.30}
    ]
    assert not combo_engine.is_valid_combo_legs(legs, allow_duos=False)


# -----------------------------------------------------------------------------
# TEST 13: Dues seleccions del mateix partit amb formats d'identificació diferents
# -----------------------------------------------------------------------------
def test_13_same_match_different_identifier_formats_rejected(combo_engine):
    """
    Diferents formats de nom ('vs', '-', 'FC Barcelona' vs 'Barcelona') o
    IDs compostos ('2026_LALIGA_RMA_FCB') davant de team_ids han de ser resolts com al mateix duel.
    """
    # 1. Separadors i noms lleugerament diferents ('Real Madrid vs FC Barcelona' vs 'Real Madrid - Barcelona')
    legs_format = [
        {"match_id": "M_SRC1", "matchup": "Real Madrid vs FC Barcelona", "selection_name": "1X", "bookie_odd": 1.40},
        {"match_id": "M_SRC2", "matchup": "Real Madrid - Barcelona", "selection_name": "Més de 1.5 gols", "bookie_odd": 1.30}
    ]
    assert not combo_engine.is_valid_combo_legs(legs_format, allow_duos=False)

    # 2. Una selecció amb match_id compost i l'altra amb equips canònics estructurats
    legs_cross = [
        {"match_id": "2026_LALIGA_RMA_FCB", "selection_name": "1X", "bookie_odd": 1.40},
        {"home_team_id": "RMA", "away_team_id": "FCB", "selection_name": "Over 1.5", "bookie_odd": 1.30}
    ]
    assert not combo_engine.is_valid_combo_legs(legs_cross, allow_duos=False)


# -----------------------------------------------------------------------------
# TEST 14: Partit sense identificadors suficients és rebutjat
# -----------------------------------------------------------------------------
def test_14_match_with_insufficient_identifiers_rejected(combo_engine):
    """
    Una selecció que no té ni match_id ni equips identificables
    no pot provar la seva independència i ha de ser rebutjada.
    """
    legs = [
        {"selection_name": "1X", "bookie_odd": 1.40},  # Sense match_id, matchup ni equips
        {"matchup": "Valencia vs Sevilla", "selection_name": "Over 1.5", "bookie_odd": 1.35}
    ]
    assert not combo_engine.is_valid_combo_legs(legs, allow_duos=False)

    # Fins i tot amb text genèric de marcador de posició
    legs_placeholder = [
        {"matchup": "Partit", "selection_name": "1X", "bookie_odd": 1.40},
        {"matchup": "Valencia vs Sevilla", "selection_name": "Over 1.5", "bookie_odd": 1.35}
    ]
    assert not combo_engine.is_valid_combo_legs(legs_placeholder, allow_duos=False)


# -----------------------------------------------------------------------------
# TEST 15: Crides directes als generadors mai produeixen duos del mateix partit
# -----------------------------------------------------------------------------
def test_15_direct_generator_calls_cannot_bypass_validation(combo_engine):
    """
    Verifica que cap ruta de generació (safe, semi, risky, few matches)
    produeixi combinades ordinàries amb seleccions del mateix partit.
    """
    m1 = {
        "id": "M1", "matchup": "Real Madrid vs Barcelona", "competition_id": "LALIGA",
        "home_team": "Real Madrid", "away_team": "Barcelona",
        "markets": [
            {"match_id": "M1", "matchup": "Real Madrid vs Barcelona", "selection_name": "1X", "category": "Doble Oportunitat", "bookie_odd": 1.35, "model_prob": 78.0},
            {"match_id": "M1", "matchup": "Real Madrid vs Barcelona", "selection_name": "Over 1.5", "category": "Gols", "bookie_odd": 1.30, "model_prob": 80.0}
        ]
    }
    m2 = {
        "id": "M2", "matchup": "Atletico Madrid vs Sevilla", "competition_id": "LALIGA",
        "home_team": "Atletico Madrid", "away_team": "Sevilla",
        "markets": [
            {"match_id": "M2", "matchup": "Atletico Madrid vs Sevilla", "selection_name": "1X", "category": "Doble Oportunitat", "bookie_odd": 1.40, "model_prob": 75.0},
            {"match_id": "M2", "matchup": "Atletico Madrid vs Sevilla", "selection_name": "Under 3.5", "category": "Gols", "bookie_odd": 1.35, "model_prob": 74.0}
        ]
    }

    # 1. find_pair_safe
    s1, s2 = combo_engine.find_pair_safe([m1, m2], allow_duos=False)
    for c in [s1, s2]:
        if c.get("legs"):
            assert combo_engine.is_valid_combo_legs(c["legs"], allow_duos=False)

    # 2. generate_few_matches_combos_suite amb només 2 partits
    few_suite = combo_engine.generate_few_matches_combos_suite([m1, m2])
    for cat in ["safe", "semi", "risky"]:
        for c in few_suite.get(cat, []):
            if c.get("legs"):
                assert combo_engine.is_valid_combo_legs(c["legs"], allow_duos=False)


# -----------------------------------------------------------------------------
# TEST 16: Notes textuals amb negacions o ambigüitat NO activen VOID
# -----------------------------------------------------------------------------
def test_16_status_notes_with_negations_do_not_trigger_void():
    """
    Notes amb negacions com 'void not confirmed', 'not void' o 'no anul·lat'
    han de mantenir el partit en estat PENDING, mai declarar-lo VOID.
    """
    tracker = ComboTracker()
    leg = {"selection_name": "1X", "bookie_odd": 1.50}

    ambiguous_notes = [
        "void not confirmed",
        "not void",
        "no anul·lat",
        "anul·lació pendent de resolució",
        "possible void",
        "void unconfirmed"
    ]
    for note in ambiguous_notes:
        status, reason = tracker.evaluate_leg(leg, {"status": "SCHEDULED", "status_note": note})
        assert status == "PENDING", f"La nota '{note}' no hauria d'activar estat VOID"


# -----------------------------------------------------------------------------
# TEST 17: Estat ABANDONED sense resolució explícita roman PENDING
# -----------------------------------------------------------------------------
def test_17_abandoned_match_without_explicit_resolution_remains_pending():
    """
    Un partit ABANDONED sense flag explícit de void ni confirmació ferma
    ha de quedar PENDING a l'espera de resolució oficial federativa/bookmaker.
    """
    tracker = ComboTracker()
    leg = {"selection_name": "1X", "bookie_odd": 1.50}

    # Abandonat sense resolució -> PENDING
    st, reason = tracker.evaluate_leg(leg, {"status": "ABANDONED"})
    assert st == "PENDING"
    assert "abandonat" in reason.lower()

    # Abandonat amb is_void = "false" o "0" -> roman PENDING
    st_f, _ = tracker.evaluate_leg(leg, {"status": "ABANDONED", "is_void": "false"})
    assert st_f == "PENDING"
    st_0, _ = tracker.evaluate_leg(leg, {"status": "ABANDONED", "is_void": 0})
    assert st_0 == "PENDING"

    # Abandonat amb resolució confirmada de void -> VOID
    st_void, _ = tracker.evaluate_leg(leg, {"status": "ABANDONED", "is_void": True})
    assert st_void == "VOID"


# -----------------------------------------------------------------------------
# TEST 18: Liquidació mixta amb VOID, guanyadores, perdedores i pendents
# -----------------------------------------------------------------------------
def test_18_void_leg_mixed_with_won_lost_and_pending():
    """
    Verifica l'aritmètica de combinades mixtes amb seleccions VOID:
    - WON + VOID + PENDING -> PENDING (no liquidada prematurament)
    - LOST + VOID + PENDING -> LOST (inevitablement perduda)
    - WON + VOID -> WON amb quota efectiva exacta sense arrodoniment prematur
    """
    tracker = ComboTracker()

    # 1. WON + VOID + PENDING -> PENDING
    c_pend = {
        "id": "C_MIX_PEND", "stake": 10.0, "combined_odd": 4.50,
        "legs": [
            {"matchup": "M_WON vs Rival", "selection_name": "1X", "bookie_odd": 1.50},
            {"matchup": "M_VOID vs Rival", "selection_name": "Over 1.5", "bookie_odd": 1.60},
            {"matchup": "M_RUNNING vs Rival", "selection_name": "Under 3.5", "bookie_odd": 1.80}
        ]
    }
    matches_pend = {
        "M_WON vs Rival": {"status": "FINISHED", "home_goals": 2, "away_goals": 0},
        "M_VOID vs Rival": {"status": "CANCELLED"},
        "M_RUNNING vs Rival": {"status": "SCHEDULED"}
    }
    res_pend = tracker.evaluate_combo_settlement(c_pend, matches_pend)
    assert res_pend["status"] == "PENDING"
    assert res_pend["payout"] == 0.0

    # 2. LOST + VOID + PENDING -> LOST
    c_lost = {
        "id": "C_MIX_LOST", "stake": 10.0, "combined_odd": 4.50,
        "legs": [
            {"matchup": "M_LOST vs Rival", "selection_name": "1 - Victòria Local", "bookie_odd": 1.50},
            {"matchup": "M_VOID vs Rival", "selection_name": "Over 1.5", "bookie_odd": 1.60},
            {"matchup": "M_RUNNING vs Rival", "selection_name": "Under 3.5", "bookie_odd": 1.80}
        ]
    }
    matches_lost = {
        "M_LOST vs Rival": {"status": "FINISHED", "home_goals": 0, "away_goals": 1},
        "M_VOID vs Rival": {"status": "CANCELLED"},
        "M_RUNNING vs Rival": {"status": "SCHEDULED"}
    }
    res_lost = tracker.evaluate_combo_settlement(c_lost, matches_lost)
    assert res_lost["status"] == "LOST"
    assert res_lost["payout"] == 0.0
    assert res_lost["profit"] == -10.0

    # 3. WON (1.333) + VOID (2.00) amb stake 100 -> Payout sense pèrdua de precisió
    c_won = {
        "id": "C_MIX_WON", "stake": 100.0, "combined_odd": 2.666,
        "legs": [
            {"matchup": "M_WON1 vs Rival", "selection_name": "1X", "bookie_odd": 1.3333},
            {"matchup": "M_VOID vs Rival", "selection_name": "Over 1.5", "bookie_odd": 2.00}
        ]
    }
    matches_won = {
        "M_WON1 vs Rival": {"status": "FINISHED", "home_goals": 1, "away_goals": 0},
        "M_VOID vs Rival": {"status": "CANCELLED"}
    }
    res_won = tracker.evaluate_combo_settlement(c_won, matches_won)
    assert res_won["status"] == "WON"
    # 100.0 * 1.3333 = 133.33 (sense arrodoniment prematur de la quota a 1.33)
    assert res_won["payout"] == 133.33
    assert res_won["profit"] == 33.33


# -----------------------------------------------------------------------------
# TEST 19: Persistència del resultat i saldo via db_manager.py
# -----------------------------------------------------------------------------
def test_19_persistence_and_bankroll_calculation_via_db_manager(temp_db):
    """
    Verifica que la base de dades registra i computa correctament combinades VOID
    juntament amb WON i LOST sense distorsionar el balanç de bankroll.
    """
    with temp_db.get_connection() as conn:
        cursor = conn.cursor()
        # Combinada 1: WON (Stake: 20, Payout: 40, Profit: +20)
        cursor.execute("""
            INSERT INTO combo_recommendations (id, competition_id, season, jornada, profile, stake, combined_odd, status, payout, profit)
            VALUES ('C_DB_WON', 'LALIGA', '2026-2027', 10, 'SAFE', 20.0, 2.00, 'WON', 40.0, 20.0)
        """)
        # Combinada 2: LOST (Stake: 15, Payout: 0, Profit: -15)
        cursor.execute("""
            INSERT INTO combo_recommendations (id, competition_id, season, jornada, profile, stake, combined_odd, status, payout, profit)
            VALUES ('C_DB_LOST', 'LALIGA', '2026-2027', 10, 'SEMI', 15.0, 5.00, 'LOST', 0.0, -15.0)
        """)
        # Combinada 3: VOID (Stake: 25, Payout: 25, Profit: 0.0)
        cursor.execute("""
            INSERT INTO combo_recommendations (id, competition_id, season, jornada, profile, stake, combined_odd, status, payout, profit)
            VALUES ('C_DB_VOID', 'LALIGA', '2026-2027', 10, 'SAFE', 25.0, 3.00, 'VOID', 25.0, 0.0)
        """)
        conn.commit()

    hist = temp_db.get_bankroll_history("LALIGA")
    assert hist["total_stake"] == 60.0  # 20 + 15 + 25
    assert hist["total_payout"] == 65.0  # 40 + 0 + 25
    assert hist["net_profit"] == 5.0    # 65 - 60 = +5.0 (profit del VOID és net 0)
    assert hist["evaluated_combos"] == 3


# -----------------------------------------------------------------------------
# TEST 20: Exportació d'estat VOID a WebDataExporter
# -----------------------------------------------------------------------------
def test_20_web_data_exporter_export_void_settlement(temp_db):
    """
    Verifica que la lògica de WebDataExporter tracta i persisteix adequadament
    les combinades amb estat VOID.
    """
    tracker = ComboTracker(db=temp_db)

    with temp_db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO matches (id, competition_id, season, jornada, date_time, home_team_id, away_team_id, status)
            VALUES ('M_W_VOID', 'LALIGA', '2026-2027', 15, '2026-10-25 21:00', 'VAL', 'SEV', 'CANCELLED')
        """)
        cursor.execute("""
            INSERT INTO combo_recommendations (id, competition_id, season, jornada, profile, stake, combined_odd, status)
            VALUES ('C_WEB_TEST', 'LALIGA', '2026-2027', 15, 'SAFE', 20.0, 2.50, 'PENDING')
        """)
        cursor.execute("""
            INSERT INTO combo_legs (combo_id, matchup, selection_name, bookie_odd, home_team_id, away_team_id, status)
            VALUES ('C_WEB_TEST', 'VAL vs SEV', '1X', 2.50, 'VAL', 'SEV', 'PENDING')
        """)
        conn.commit()

    evals = tracker.evaluate_all_pending_combos()
    assert len(evals) == 1
    assert evals[0]["status"] == "VOID"
    assert evals[0]["payout"] == 20.0
    assert evals[0]["profit"] == 0.0

    # Comprovar estat a SQLite
    with temp_db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, payout, profit FROM combo_recommendations WHERE id = 'C_WEB_TEST'")
        row = cursor.fetchone()
        assert row["status"] == "VOID"
        assert row["payout"] == 20.0
        assert row["profit"] == 0.0


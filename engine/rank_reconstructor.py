"""
engine/rank_reconstructor.py
============================
Mòdul de Reconstrucció Segura i Determinista dels Rànquings Històrics (Tri-Ranks).

Reconstrueix 'off_rank', 'def_rank' i 'general_rank' a partir de les condicions
inicials canòniques i la seqüència cronològica de partits acabats a la base de dades.
Preserva l'Elo i la resta d'atributs de team_ratings intactes.
"""

import sqlite3
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple
from model.rank_engine import RankEngine

# Mitjanes de lliga utilitzades com a referència d'esperança ((home_avg + away_avg) / 2)
LEAGUE_AVG_GOALS = {
    "LALIGA": 1.27,
    "PREMIER": 1.40,
    "HYPERMOTION": 1.20,
    "CHAMPIONSHIP": 1.33,
    "BUNDESLIGA": 1.50,
    "LIGUE1": 1.30,
    "SERIEA": 1.30
}

LEAGUE_TEAMS_COUNT = {
    "LALIGA": 20,
    "PREMIER": 20,
    "HYPERMOTION": 22,
    "CHAMPIONSHIP": 24,
    "BUNDESLIGA": 18,
    "LIGUE1": 18,
    "SERIEA": 20
}

def parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.min
    for fmt in ("%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except Exception:
            pass
    return datetime.min

def get_canonical_initial_ratings() -> Dict[str, Dict[str, float]]:
    """
    Retorna els rànquings base inicials canònics segons la definició de seed_initial_data.
    """
    initial_map: Dict[str, Dict[str, float]] = {}

    # LaLiga (1a Divisió)
    initial_map["FCB"] = {"general": 1.0, "off": 1.0, "def": 1.5}
    initial_map["RMA"] = {"general": 2.0, "off": 1.5, "def": 1.5}
    initial_map["ATM"] = {"general": 3.0, "off": 3.0, "def": 2.5}
    for tid in ["VIL", "ATH", "RSO", "BET"]:
        initial_map[tid] = {"general": 5.5, "off": 5.5, "def": 5.5}
    for tid in ["SEV", "VAL", "OSA", "CEL", "RAY", "GET", "ALA", "ESP"]:
        initial_map[tid] = {"general": 11.0, "off": 11.0, "def": 11.0}

    # Premier League
    for tid in ["MCI", "ARS", "LIV"]:
        initial_map[tid] = {"general": 1.5, "off": 1.5, "def": 1.5}
    for tid in ["AVL", "TOT", "CHE", "NEW"]:
        initial_map[tid] = {"general": 4.0, "off": 4.0, "def": 4.0}
    for tid in ["MUN", "WHU", "BHA", "BOU", "FUL"]:
        initial_map[tid] = {"general": 8.0, "off": 8.0, "def": 8.0}
    for tid in ["WOL", "CRY", "BRE", "EVE", "NFO"]:
        initial_map[tid] = {"general": 13.0, "off": 13.0, "def": 13.0}
    for tid in ["IPS", "LEI", "SOU"]:
        initial_map[tid] = {"general": 18.0, "off": 18.0, "def": 18.0}

    # Hypermotion (2a Divisió)
    for tid in ["GRA", "ALM", "CAD", "ZAR", "EIB", "ROV", "SPG", "LEV"]:
        initial_map[tid] = {"general": 5.0, "off": 5.0, "def": 5.0}

    # Championship (2a Anglesa)
    for tid in ["BNL", "SHU", "WBA", "NOR", "MID", "WAT"]:
        initial_map[tid] = {"general": 4.0, "off": 4.0, "def": 4.0}
    for tid in ["BLA", "BRC", "SWA", "STK", "QPR", "MIL", "PNE", "DER", "BIR", "WRE", "CDF"]:
        initial_map[tid] = {"general": 12.0, "off": 12.0, "def": 12.0}
    for tid in ["POR", "BOL", "CHA", "LIN"]:
        initial_map[tid] = {"general": 20.0, "off": 20.0, "def": 20.0}

    # Bundesliga (18 equips -> neutre 9.0)
    for tid in ["BAY", "B04", "BVB", "RBL", "SGE", "VFB", "SCF", "BMG", "SVW", "FCA", "TSG", "M05", "FCU", "KOE", "HSV", "S04", "SCP", "SVE", "WOB"]:
        initial_map[tid] = {"general": 9.0, "off": 9.0, "def": 9.0}

    # Ligue 1 (18 equips -> neutre 9.0)
    for tid in ["PSG", "ASM", "LIL", "OM", "OL", "RCL", "OGC", "SRFC", "BRS", "TFC", "RCS", "HAC", "AJA", "SCO", "FCL", "EST", "PFC", "LMA"]:
        initial_map[tid] = {"general": 9.0, "off": 9.0, "def": 9.0}

    # Serie A (20 equips -> neutre 10.0)
    for tid in ["INT", "ACM", "JUV", "ATA", "NAP", "ASR", "LAZ", "FIO", "BFC", "TOR", "MNZ", "GEN", "UDI", "CAG", "USL", "PAR", "COM", "VEN", "SAS", "FRO"]:
        initial_map[tid] = {"general": 10.0, "off": 10.0, "def": 10.0}

    return initial_map

def compute_replay(db_path: Path) -> Tuple[Dict[str, Dict[str, float]], List[Dict[str, Any]]]:
    """
    Executa la simulació pura en memòria sense modificar la base de dades.
    Retorna els rànquings finals i la llista de partits processats.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    canonical_initial = get_canonical_initial_ratings()

    # Assegurar que tots els equips existents a team_ratings tinguin valor inicial
    cur.execute("SELECT team_id FROM team_ratings")
    all_team_ids = [r["team_id"] for r in cur.fetchall()]
    
    current_ranks: Dict[str, Dict[str, float]] = {}
    for tid in all_team_ids:
        if tid in canonical_initial:
            current_ranks[tid] = dict(canonical_initial[tid])
        else:
            current_ranks[tid] = {"general": 14.0, "off": 14.0, "def": 14.0}

    # Carregar partits finalitzats amb marcadors
    cur.execute("""
        SELECT id, competition_id, jornada, date_time, home_team_id, away_team_id,
               home_goals, away_goals, home_xg, away_xg
        FROM matches
        WHERE status = 'FINISHED' AND home_goals IS NOT NULL AND away_goals IS NOT NULL
    """)
    matches = [dict(r) for r in cur.fetchall()]
    conn.close()

    for m in matches:
        m["dt"] = parse_date(m["date_time"])
    matches.sort(key=lambda x: (x["dt"], x["jornada"], x["id"]))

    rank_engines = {c: RankEngine(league_teams_count=LEAGUE_TEAMS_COUNT[c]) for c in LEAGUE_TEAMS_COUNT}

    for m in matches:
        comp = m["competition_id"]
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        hg = int(m["home_goals"])
        ag = int(m["away_goals"])
        h_xg = float(m["home_xg"]) if m["home_xg"] is not None else float(hg)
        a_xg = float(m["away_xg"]) if m["away_xg"] is not None else float(ag)
        exp_ref = LEAGUE_AVG_GOALS.get(comp, 1.27)
        re = rank_engines.get(comp, rank_engines["LALIGA"])

        # Assegurar presència a current_ranks
        if hid not in current_ranks:
            current_ranks[hid] = {"general": 14.0, "off": 14.0, "def": 14.0}
        if aid not in current_ranks:
            current_ranks[aid] = {"general": 14.0, "off": 14.0, "def": 14.0}

        h_gen, h_off, h_def = re.update_tri_ranks(
            current_off_rank=current_ranks[hid]["off"],
            current_def_rank=current_ranks[hid]["def"],
            goals_scored=hg, goals_conceded=ag,
            xg_for=h_xg, xg_against=a_xg,
            expected_goals_model=exp_ref
        )
        a_gen, a_off, a_def = re.update_tri_ranks(
            current_off_rank=current_ranks[aid]["off"],
            current_def_rank=current_ranks[aid]["def"],
            goals_scored=ag, goals_conceded=hg,
            xg_for=a_xg, xg_against=h_xg,
            expected_goals_model=exp_ref
        )

        current_ranks[hid] = {"general": h_gen, "off": h_off, "def": h_def}
        current_ranks[aid] = {"general": a_gen, "off": a_off, "def": a_def}

    return current_ranks, matches

def apply_reconstructed_ranks(db_path: Path, ranks: Dict[str, Dict[str, float]], create_backup: bool = True) -> str:
    """
    Aplica els rànquings reconstruïts a team_ratings en una única transacció atòmica.
    Crea una còpia de seguretat prèvia si create_backup és True.
    """
    backup_path = ""
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = str(db_path.parent / f"{db_path.stem}_backup_pre_rank_replay_{ts}.db")
        shutil.copy(str(db_path), backup_path)

    conn = sqlite3.connect(str(db_path), timeout=60.0)
    cur = conn.cursor()

    try:
        cur.execute("BEGIN TRANSACTION")
        for tid, r in ranks.items():
            cur.execute("""
                UPDATE team_ratings 
                SET general_rank = ?, off_rank = ?, def_rank = ?, updated_at = datetime('now')
                WHERE team_id = ?
            """, (r["general"], r["off"], r["def"], tid))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e
    finally:
        conn.close()

    return backup_path

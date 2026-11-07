"""
tests/test_data_isolation.py
============================
Comprovació reproduïble que valida que les operacions del sistema i l'execució
de proves NO modifiquen cap dels fitxers de dades de treball reals:
- data/laliga.db
- data/winamax_cache_tid_31.json
- data/bet365_odds_cache.json
"""

import hashlib
from pathlib import Path
from database.db_manager import DatabaseManager
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

PROTECTED_PATHS = [
    DATA_DIR / "laliga.db",
    DATA_DIR / "winamax_cache_tid_31.json",
    DATA_DIR / "bet365_odds_cache.json",
]

def hash_file(path: Path) -> str:
    assert path.exists(), f"El fitxer {path} ha d'existir"
    return hashlib.sha256(path.read_bytes()).hexdigest()

def test_data_isolation_under_live_operations():
    """Valida la immutabilitat dels 3 fitxers de dades davant d'instanciació i operacions."""
    hashes_before = {p.name: hash_file(p) for p in PROTECTED_PATHS}

    # 1. Operacions amb DatabaseManager (lectura i escriptura aïllada)
    db = DatabaseManager()
    with db.get_connection() as conn:
        conn.cursor().execute("SELECT count(*) FROM competitions").fetchone()

    # 2. Operacions amb WinamaxScraper
    w = WinamaxScraper(headless=True)
    w.get_match_odds("Lecce", "Genoa", "SERIEA")

    # 3. Operacions amb Bet365Scraper
    b = Bet365Scraper(headless=True)
    b.get_match_odds("Lecce", "Genoa", "SERIEA")
    # Intent d'escriptura de memòria cau
    b._save_cache()

    hashes_after = {p.name: hash_file(p) for p in PROTECTED_PATHS}

    for name in hashes_before:
        assert hashes_before[name] == hashes_after[name], (
            f"VIOLACIÓ D'AÏLLAMENT: El fitxer real data/{name} ha estat modificat!\n"
            f"Abans: {hashes_before[name]}\n"
            f"Després: {hashes_after[name]}"
        )


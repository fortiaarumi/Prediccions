"""
scrape.py
=========
Comanda ràpida per a l'scrapper i consulta de marcadors multi-lliga.

ÚS FÀCIL I DIRECTE:
-------------------
1. Scrappejar una jornada (descarrega partits, xG, estadístiques i actualitza l'Elo):
   python scrape.py 1                 -> Jornada 1 de LaLiga EA Sports
   python scrape.py 1 premier         -> Jornada 1 de Premier League
   python scrape.py 1 hypermotion     -> Jornada 1 de LaLiga Hypermotion (2a)
   python scrape.py 1 all             -> Jornada 1 de totes 3 lligues alhora
   python scrape.py jornada 1 premier -> També admet la paraula 'jornada'

2. Consultar resultats ja ingestas a la base de dades (sense esperar a scrappejar):
   python scrape.py show 1            -> Mostra els resultats de la J1 de LaLiga
   python scrape.py show 1 premier    -> Mostra els resultats de la J1 de Premier
   python scrape.py show 1 all        -> Mostra els resultats de la J1 de totes les lligues
   python scrape.py summary           -> Resum general de partits i jornades a SQLite
"""

import sys
import io
import argparse
from pathlib import Path

# UTF-8 a la consola de Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.db_manager import DatabaseManager
from scraper.live_crawler import LiveCrawler

LEAGUE_ALIASES = {
    "1": "LALIGA",
    "LALIGA": "LALIGA",
    "EA": "LALIGA",
    "ESPANYOLA": "LALIGA",
    "PRIMERA": "LALIGA",
    "PREMIER": "PREMIER",
    "EPL": "PREMIER",
    "ANGLATERRA": "PREMIER",
    "HYPERMOTION": "HYPERMOTION",
    "SEGONA": "HYPERMOTION",
    "2A": "HYPERMOTION",
    "ALL": "ALL",
    "TOTES": "ALL",
}

def resolve_league(name: str) -> str:
    clean = str(name).strip().upper()
    return LEAGUE_ALIASES.get(clean, "LALIGA")

def show_summary():
    db = DatabaseManager()
    conn = db.get_connection()
    c = conn.cursor()
    rows = c.execute("""
        SELECT competition_id, jornada, COUNT(*) as total,
               SUM(CASE WHEN status='FINISHED' THEN 1 ELSE 0 END) as jugats,
               SUM(CASE WHEN status='SCHEDULED' THEN 1 ELSE 0 END) as pendents
        FROM matches
        GROUP BY competition_id, jornada
        ORDER BY competition_id, jornada
    """).fetchall()
    conn.close()

    print("\n" + "=" * 80)
    print("   📊 RESUM DE PARTITS REGISTRATS A LA BASE DE DADES (data/laliga.db)")
    print("=" * 80)
    if not rows:
        print("   (La base de dades encara no conté partits)")
        return

    current_comp = None
    for r in rows:
        comp = r["competition_id"]
        if comp != current_comp:
            current_comp = comp
            print(f"\n   🏆 {comp}:")
        print(f"      • Jornada {r['jornada']:2d}: {r['jugats']:2d} finalitzats / {r['pendents']:2d} programats  (Total: {r['total']})")

    print("\n" + "-" * 80)
    print("   💡 Per consultar els marcadors exactes d'una jornada:")
    print("      python scrape.py show 1 premier")
    print("      python scrape.py show 1 laliga")
    print("   💡 Per scrappejar una nova jornada a internet:")
    print("      python scrape.py 6 premier")
    print("=" * 80 + "\n")

def show_matches(jornada: int, league: str = "LALIGA"):
    comp_target = resolve_league(league)
    db = DatabaseManager()
    conn = db.get_connection()
    c = conn.cursor()

    if comp_target == "ALL":
        query = """
            SELECT m.*, r.name as ref_name, r.yellow_cards_avg, r.strictness_index 
            FROM matches m 
            LEFT JOIN referees r ON m.referee_id = r.id 
            WHERE m.jornada = ? 
            ORDER BY m.competition_id, m.date_time
        """
        params = (jornada,)
    else:
        query = """
            SELECT m.*, r.name as ref_name, r.yellow_cards_avg, r.strictness_index 
            FROM matches m 
            LEFT JOIN referees r ON m.referee_id = r.id 
            WHERE m.jornada = ? AND m.competition_id = ? 
            ORDER BY m.date_time
        """
        params = (jornada, comp_target)

    rows = c.execute(query, params).fetchall()
    conn.close()

    print("\n" + "=" * 98)
    print(f"   📋 MARCADORS I ESTADÍSTIQUES INGESTAES · JORNADA {jornada} ({comp_target})")
    print("=" * 98)

    if not rows:
        print(f"   ℹ️ No hi ha cap partit registrat per a la Jornada {jornada} ({comp_target}).")
        print(f"      Pots descarregar-los executant: python scrape.py {jornada} {comp_target.lower()}")
        print("=" * 98 + "\n")
        return

    print(f"   {'DATA':<11} | {'LOCAL':<16} {'RES':^7} {'VISITANT':<16} | {'xG':^9} | {'TARG':^5} | {'CORN':^5} | {'ÀRBITRE OFICIAL':<22}")
    print("   " + "-" * 94)

    for r in rows:
        h = r["home_team_id"]
        a = r["away_team_id"]
        dt = (r["date_time"] or "")[:10]
        status = r["status"]
        ref = r["ref_name"] or "Àrbitre Mitjà"
        if len(ref) > 21:
            ref = ref[:19] + ".."

        if status == "FINISHED" and r["home_goals"] is not None:
            res_str = f"{r['home_goals']} - {r['away_goals']}"
            h_xg = f"{r['home_xg']:.2f}" if r["home_xg"] is not None else "-"
            a_xg = f"{r['away_xg']:.2f}" if r["away_xg"] is not None else "-"
            xg_str = f"{h_xg}-{a_xg}"
            cards = f"{(r['home_yellow_cards'] or 0)+(r['away_yellow_cards'] or 0)}"
            corners = f"{(r['home_corners'] or 0)+(r['away_corners'] or 0)}"
        else:
            res_str = " vs "
            xg_str = "  -  "
            cards = "-"
            corners = "-"

        print(f"   {dt:<11} | {h:<16} {res_str:^7} {a:<16} | {xg_str:^9} | {cards:^5} | {corners:^5} | {ref:<22}")

    print("=" * 98 + "\n")

def run_scraping(jornada: int, league: str = "LALIGA"):
    comp_target = resolve_league(league)
    comps = ["LALIGA", "PREMIER", "HYPERMOTION"] if comp_target == "ALL" else [comp_target]

    crawler = LiveCrawler(headless=True)
    for c in comps:
        print(f"\n[*] Executant crawler oficial per a {c} - Jornada {jornada}...")
        crawler.run_live_pipeline(jornada=jornada, competition_id=c)

def main():
    raw_args = [a for a in sys.argv[1:] if a.lower() != "jornada"]

    if not raw_args:
        show_summary()
        return

    first = raw_args[0].lower()

    if first in ("summary", "--summary", "-s", "stats"):
        show_summary()
        return

    if first in ("show", "--show", "view", "veure"):
        if len(raw_args) < 2:
            print("[!] Has d'especificar la jornada: python scrape.py show <jornada> [lliga]")
            return
        try:
            j = int(raw_args[1])
            comp = raw_args[2] if len(raw_args) > 2 else "LALIGA"
            show_matches(jornada=j, league=comp)
        except ValueError:
            print("[!] El número de jornada ha de ser un enter (ex: python scrape.py show 1)")
        return

    # Si el primer argument és directament el número de jornada:
    try:
        j = int(first)
        comp = raw_args[1] if len(raw_args) > 1 else "LALIGA"
        run_scraping(jornada=j, league=comp)
    except ValueError:
        print(f"[!] Argument desconegut: '{first}'")
        print("    Exemples d'ús:")
        print("      python scrape.py 1                (scrappeja J1 LaLiga)")
        print("      python scrape.py 1 premier        (scrappeja J1 Premier)")
        print("      python scrape.py 1 hypermotion    (scrappeja J1 Hypermotion)")
        print("      python scrape.py 1 all            (scrappeja J1 totes)")
        print("      python scrape.py show 1           (mostra marcadors J1)")
        print("      python scrape.py summary          (resum base de dades)")

if __name__ == "__main__":
    main()

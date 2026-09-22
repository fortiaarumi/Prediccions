"""
main.py
=======
Punt d'entrada principal (CLI) per al Sistema de Prediccions de La Lliga.

Comandes d'ús:
    python main.py --predict-jornada 5
    python main.py --predict-match "Real Madrid" "Rayo Vallecano" --date "2026-09-12"
"""

import sys
import argparse
import io
from pathlib import Path

# Sortida UTF-8 per a Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.db_manager import DatabaseManager
from scraper.live_crawler import LiveCrawler
from scraper.referee_resolver import RefereeResolver
from scraper.winamax_scraper import WinamaxScraper
from engine.jornada_predictor import JornadaPredictor
from engine.match_predictor import MatchPredictor
from engine.value_bet_engine import ValueBetEngine
from engine.pdf_report_generator import PDFReportGenerator

def cmd_scrape_jornada(jornada: int, league: str = "LALIGA"):
    crawler = LiveCrawler(headless=True)
    comps = ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP"] if league.upper() == "ALL" else [league.upper()]
    for c in comps:
        crawler.run_live_pipeline(jornada, competition_id=c)

def cmd_predict_jornada(jornada: int, league: str = "LALIGA"):
    db = DatabaseManager()
    db.seed_initial_data()
    predictor = JornadaPredictor(db=db)
    comps = ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP"] if league.upper() == "ALL" else [league.upper()]
    for c in comps:
        predictor.predict_jornada(jornada=jornada, competition_id=c)

def cmd_predict_match(home: str, away: str, date: str = None, referee_name: str = None):
    db = DatabaseManager()
    db.seed_initial_data()
    
    home_id = db.find_team_id(home)
    away_id = db.find_team_id(away)

    if not home_id or not away_id:
        raise ValueError(f"[ERROR CRÍTIC]: L'equip '{home}' o '{away}' no existeix a la base de dades de LaLiga.")

    # Resolució intel·ligent d'àrbitre: si l'usuari no n'ha especificat cap, cercar l'oficial
    ref_resolver = RefereeResolver(db=db)
    if not referee_name or referee_name == "REF_DEFAULT":
        ref_resolution = ref_resolver.resolve_referee(home, away, match_date=date)
        ref_data = ref_resolution["ref_data"]
    else:
        ref_id = db.find_referee_id(referee_name)
        ref_data = db.get_referee(ref_id)
        ref_resolution = {
            "id": ref_id,
            "name": ref_data["name"],
            "is_generic": ref_id == "REF_DEFAULT",
            "ref_data": ref_data
        }

    home_data = db.get_team_rating(home_id)
    away_data = db.get_team_rating(away_id)

    # 1. Predicció del Model Estadístic
    predictor = MatchPredictor()
    pred = predictor.predict_single_match(home_data, away_data, ref_data, match_context={"date": date})
    pred["date"] = date
    pred["referee_resolution"] = ref_resolution

    # 2. Extracció de Cuotes en Viu de Winamax Espanya
    winamax = WinamaxScraper(headless=True)
    odds = winamax.get_match_odds(home_data["name"], away_data["name"])

    # 3. Anàlisi de Valor Esperat (+EV%) i Apostes Rendibles
    value_engine = ValueBetEngine(kelly_fraction=0.25)
    bet_analysis = value_engine.analyze_match_betting(pred, odds)

    # 4. Generació de l'Informe PDF
    pdf_gen = PDFReportGenerator()
    pdf_path = pdf_gen.generate_match_report(pred, odds, bet_analysis)

    # Sortida neta i elegant per terminal
    print("\n" + "=" * 80)
    print(f"   [+] PREDICCIÓ GENERADA: {pred['matchup']} ({date or 'Pendent'})")
    print("=" * 80)
    ref_tag = f"{ref_data['name']} (Oficial CTA)" if not ref_resolution.get("is_generic") else "Pendent CTA (Àrbitre Mitjà Standard)"
    print(f"   • Veredicte principal 1X2:  {pred['verdict_1x2']}")
    p1 = pred['goals']['prob_1X2']['1']
    px = pred['goals']['prob_1X2']['X']
    p2 = pred['goals']['prob_1X2']['2']
    print(f"   • Probabilitats (1X2):      1: {p1:.1f}% | X: {px:.1f}% | 2: {p2:.1f}%")
    print(f"   • Gols esperats (xG):       {pred['goals']['expected_goals_home']} (Local) vs {pred['goals']['expected_goals_away']} (Visitant)")
    print(f"   • Àrbitre assignat:         {ref_tag}")
    
    vbs = bet_analysis.get("value_bets", [])
    if vbs:
        print(f"   • Oportunitats Winamax:     🔥 S'han trobat {len(vbs)} apostes amb valor esperat (+EV%)!")
        for idx, vb in enumerate(vbs[:3], start=1):
            print(f"     #{idx} [{vb.get('category')}] {vb['name']} @ {vb['bookie_odd']} (+{vb['ev_pct']:.1f}% EV)")
    else:
        print(f"   • Oportunitats Winamax:     ℹ️ Sense avantatge clar (+EV > 0) a les cuotes obertes.")

    print("-" * 80)
    print(f"   📄 INFORME COMPLET EN PDF DESAT A:")
    print(f"      {pdf_path.resolve()}")
    print("=" * 80 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Sistema Oficial de Prediccions de LaLiga + Winamax Odds")
    parser.add_argument("--scrape-jornada", type=int, help="Descobreix els enllaços, corre l'scraper i actualitza rànquings")
    parser.add_argument("--predict-jornada", type=int, help="Prediu tots els partits de la Jornada N i genera informe PDF")
    parser.add_argument("--league", type=str, default="LALIGA", help="Competició: LALIGA, PREMIER, HYPERMOTION, CHAMPIONSHIP o ALL (per defecte LALIGA)")
    parser.add_argument("--predict-match", nargs=2, metavar=("HOME", "AWAY"), help="Prediu un partit individual amb cuotes Winamax i genera PDF")
    parser.add_argument("--date", type=str, help="Data del partit (YYYY-MM-DD)")
    parser.add_argument("--referee", type=str, default=None, help="Nom o ID de l'àrbitre (opcional: si s'omet, es resol automàticament)")

    args = parser.parse_args()

    if args.scrape_jornada is not None:
        cmd_scrape_jornada(args.scrape_jornada, league=args.league)
    elif args.predict_jornada is not None:
        cmd_predict_jornada(args.predict_jornada, league=args.league)
    elif args.predict_match:
        cmd_predict_match(args.predict_match[0], args.predict_match[1], args.date, args.referee)
    else:
        cmd_predict_jornada(1, league=args.league)

if __name__ == "__main__":
    main()

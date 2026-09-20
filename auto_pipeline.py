"""
auto_pipeline.py
================
Orquestrador Autònom Complet per a Prediccions de Futbol Multi-Lliga.

Executa de forma 100% autònoma i seqüencial:
1. Avalua resultats de jornades anteriors i actualitza el simulador 'Què hagués passat si...'.
2. Rastreja i prediu els partits de:
   • LaLiga EA Sports
   • Premier League
   • LaLiga Hypermotion (2a Divisió)
3. Extreu cuotes reals en directe a Winamax Espanya per a cada competició.
4. Genera les Mega-Combinades Multi-Lliga transfrontereres.
5. Genera els 4 informes PDF d'alta qualitat a la carpeta 'reports/':
   • informe_jornada_{N}_laliga.pdf
   • informe_jornada_{N}_premier.pdf
   • informe_jornada_{N}_hypermotion.pdf
   • informe_jornada_{N}_multilliga.pdf
6. Envia automàticament els 4 informes PDF adjunts per correu electrònic a tots els
   destinataris configurats a 'config/recipients.txt'.
"""

import sys
import io
import argparse
from pathlib import Path
from typing import List, Dict, Any

# Suport de caràcters UTF-8 a la consola de Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.db_manager import DatabaseManager
from engine.jornada_predictor import JornadaPredictor
from engine.combo_bet_engine import ComboBetEngine
from engine.combo_tracker import ComboTracker
from engine.pdf_report_generator import PDFReportGenerator
from notifier.email_sender import EmailSender
from scraper.jornada_resolver import JornadaResolver

def run_pipeline(
    jornada: Optional[int] = None,
    leagues: List[str] = None,
    send_email: bool = True,
    season: str = "2026-2027"
):
    if not leagues:
        leagues = ["LALIGA", "PREMIER", "HYPERMOTION"]

    # Detecció intel·ligent de calendari per a cada lliga
    league_jornadas = {}
    for comp in leagues:
        if jornada is not None:
            league_jornadas[comp] = jornada
        else:
            league_jornadas[comp] = JornadaResolver.detect_current_jornada(comp)

    active_rounds_str = ", ".join(f"{c}: J{j}" for c, j in league_jornadas.items())
    print("\n" + "=" * 80)
    print(f"   🚀 INICIANT PIPELINE AUTÒNOM DE PREDICCIONS")
    print(f"   Jornades a disputar: {active_rounds_str}")
    print("=" * 80)

    # -------------------------------------------------------------
    # FASE 1: INICIALITZACIÓ I TRACKING 'QUÈ HAGUÉS PASSAT SI...'
    # -------------------------------------------------------------
    db = DatabaseManager()
    db.seed_initial_data()
    tracker = ComboTracker(db=db)

    print("\n[*] FASE 1: Avaluació d'apostes anteriors i simulador financer...")
    eval_results = tracker.evaluate_all_pending_combos()
    if eval_results:
        print(f"    • S'han avaluat {len(eval_results)} combinades noves amb resultats oficials.")
    else:
        print("    • No hi havia combinades pendents d'avaluar o els partits encara no han acabat.")

    sim_summary = tracker.get_simulation_summary()
    print("-" * 80)
    print("   📊 TAULER FINANCER ACUMULAT ('QUÈ HAGUÉS PASSAT SI...')")
    print(f"      • Inversió Total:       {sim_summary['total_stake']:.2f} €  (25€ Segura / 5€ Arriscada)")
    print(f"      • Retorn Brut Acumulat: {sim_summary['total_payout']:.2f} €")
    pnl_sign = "+" if sim_summary['net_profit'] >= 0 else ""
    print(f"      • Benefici Net (PnL):   {pnl_sign}{sim_summary['net_profit']:.2f} €")
    print(f"      • Rendibilitat (ROI):   {sim_summary['roi_pct']:+.1f}%")
    print(f"      • Taxa Encert Segura:   {sim_summary['safe']['hit_rate_pct']:.1f}% ({sim_summary['safe']['won']}/{sim_summary['safe']['total']})")
    print(f"      • Taxa Encert Arriscada:{sim_summary['risky']['hit_rate_pct']:.1f}% ({sim_summary['risky']['won']}/{sim_summary['risky']['total']})")
    print("-" * 80)

    # -------------------------------------------------------------
    # FASE 2: PREDICCIONS I INFORMES INDIVIDUALS PER LLIGA
    # -------------------------------------------------------------
    predictor = JornadaPredictor(db=db)
    generated_pdfs: List[Path] = []
    all_predicted_matches: List[Dict[str, Any]] = []

    main_jornada = max(league_jornadas.values()) if league_jornadas else 7

    for comp in leagues:
        comp_j = league_jornadas[comp]
        print(f"\n[*] FASE 2: Processant competició: {comp} (Jornada {comp_j})...")
        try:
            res = predictor.predict_jornada(
                jornada=comp_j,
                season=season,
                competition_id=comp,
                include_odds=True,
                generate_pdf=True
            )
            if res.get("pdf_path"):
                generated_pdfs.append(Path(res["pdf_path"]))
            all_predicted_matches.extend(res.get("predicted_matches", []))
        except Exception as e:
            print(f"[!] Error analitzant {comp}: {e}")

    # -------------------------------------------------------------
    # FASE 3: MEGA-COMBINADA I INFORME EXECUTIU MULTI-LLIGA
    # -------------------------------------------------------------
    if len(leagues) > 1 and all_predicted_matches:
        print("\n[*] FASE 3: Generant Mega-Combinades transfrontereres i Informe Multi-Lliga...")
        combo_engine = ComboBetEngine()
        multileague_combos = combo_engine.analyze_multileague_combos(all_predicted_matches)

        # Desa les combinades multi-lliga per a seguiment financer
        safe_multi = multileague_combos.get("safe_combo", {})
        risky_multi = multileague_combos.get("risky_combo", {})

        if safe_multi and safe_multi.get("legs"):
            db.save_combo_recommendation("MULTI", season, main_jornada, "SAFE", 25.0, safe_multi)
        if risky_multi and risky_multi.get("legs"):
            db.save_combo_recommendation("MULTI", season, main_jornada, "RISKY", 5.0, risky_multi)

        # Seleccionar els partits més rellevants del cap de setmana (els 4 millors favorits/duels)
        highlight_matches = sorted(
            all_predicted_matches,
            key=lambda x: max(x.get("goals", {}).get("prob_1X2", {}).values() or [0]),
            reverse=True
        )[:5]

        pdf_gen = PDFReportGenerator()
        multi_pdf_path = pdf_gen.generate_multileague_report(
            jornada=main_jornada,
            multileague_combos=multileague_combos,
            simulation_summary=sim_summary,
            highlight_matches=highlight_matches
        )
        generated_pdfs.append(multi_pdf_path)
        print(f"[+] Informe Multi-Lliga generat amb èxit a: {multi_pdf_path}")

    # -------------------------------------------------------------
    # FASE 4: ENVIAMENT AUTOMÀTIC PER CORREU ELECTRÒNIC
    # -------------------------------------------------------------
    if send_email:
        print("\n[*] FASE 4: Enviament d'informes per correu electrònic...")
        email_sender = EmailSender()
        email_sender.send_reports(
            pdf_paths=generated_pdfs,
            jornada=jornada,
            simulation_summary=sim_summary
        )
    else:
        print("\n[*] FASE 4: Enviament per correu desactivat (--no-email).")

    print("\n" + "=" * 80)
    print("   🎉 [PIPELINE COMPLETAT AMB ÈXIT]")
    print(f"   Total fitxers PDF generats a 'reports/': {len(generated_pdfs)}")
    for p in generated_pdfs:
        print(f"      • {p.name}")
    print("=" * 80 + "\n")

def update_completed_results(jornada: Optional[int] = None, leagues: List[str] = None):
    """
    Funció especial per córrer els dimarts al matí:
    Descarrega autònomament els nous marcadors oficials, xG i estadístiques
    de LaLiga, Premier i Hypermotion, i actualitza el balanç financer de les combinades.
    """
    from scraper.live_crawler import LiveCrawler
    if not leagues:
        leagues = ["LALIGA", "PREMIER", "HYPERMOTION"]

    db = DatabaseManager()
    db.seed_initial_data()
    crawler = LiveCrawler(headless=True)

    print("\n" + "=" * 80)
    print("   🔄 ACTUALITZACIÓ DE RESULTATS I BALANÇ ('QUÈ HAGUÉS PASSAT SI...')")
    print("=" * 80)

    for comp in leagues:
        target_j = jornada if jornada is not None else JornadaResolver.detect_current_jornada(comp)
        print(f"\n[*] Comprovant resultats jugats per a {comp} (fins a Jornada {target_j})...")
        for j in range(max(1, target_j - 1), target_j + 1):
            try:
                crawler.run_live_pipeline(jornada=j, competition_id=comp)
            except Exception as e:
                print(f"[!] Error actualitzant {comp} J{j}: {e}")

    tracker = ComboTracker(db=db)
    evals = tracker.evaluate_all_pending_combos()
    sim_summary = tracker.get_simulation_summary()

    print("-" * 80)
    print(f"   [ÈXIT] S'han avaluat {len(evals)} combinades amb els nous resultats oficials.")
    print(f"   • Balanç actualitzat:  {sim_summary['net_profit']:+.2f} € (ROI: {sim_summary['roi_pct']:+.1f}%)")
    print("=" * 80 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Pipeline Autònom de Prediccions de Futbol Multi-Lliga")
    parser.add_argument("--jornada", type=int, default=None, help="Número de jornada (si no s'indica, es detecta automàticament per a cada lliga)")
    parser.add_argument("--leagues", nargs="+", default=["LALIGA", "PREMIER", "HYPERMOTION"], help="Lligues a incloure (ex: LALIGA PREMIER HYPERMOTION)")
    parser.add_argument("--no-email", action="store_true", help="Genera els informes PDF però no envia correus")
    parser.add_argument("--season", type=str, default="2026-2027", help="Temporada oficial")
    parser.add_argument("--update-results", action="store_true", help="Només descarrega resultats de la jornada i actualitza el PnL (per als dimarts)")

    args = parser.parse_args()

    if args.update_results:
        update_completed_results(jornada=args.jornada, leagues=args.leagues)
    else:
        run_pipeline(
            jornada=args.jornada,
            leagues=args.leagues,
            send_email=not args.no_email,
            season=args.season
        )

if __name__ == "__main__":
    main()


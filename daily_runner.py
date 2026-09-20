"""
daily_runner.py
===============
Orquestrador Autònom Diari per al Núvol (GitHub Actions) i Local.

Comportament intel·ligent:
1. S'executa cada dia a una hora programada.
2. PAS 1 (Scraping post-jornada):
   - Si una jornada ha acabat de jugar-se, descarrega automàticament els resultats finals,
     xG, targetes i córners, actualitza l'Elo i recalcula el balanç financer ('Què hagués passat si...').
3. PAS 2 (Predicció pre-jornada en la finestra òptima d'àrbitres):
   - Detecta quan comença el primer partit de la propera jornada.
   - Només prediu quan falta poc per començar (finestra de 28h o el mateix dia), garantint que
     totes les designacions arbitrals del CTA / PGMOL ja siguin oficials.
   - Si una jornada ja s'ha predit i enviat, L'ENDEMÀ NO LA TORNA A CÓRRER.
4. PAS 3 (Filtre estricte de Combinades Multi-Lliga i Copes):
   - IGNORA SEMPRE partits de copa (Copa del Rei, FA Cup, EFL Cup). Només lligues regulars.
   - Només envia la Mega-Combinada Multi-Lliga quan juguen MULTIPLES LLIGUES al mateix temps
     (cap de setmana o intersetmanal conjunta). Si només juga una lliga sola, només s'envia
     l'informe individual d'aquella lliga.
"""

import sys
import io
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# UTF-8 a la consola
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "data" / "pipeline_state.json"

from database.db_manager import DatabaseManager
from scraper.jornada_resolver import JornadaResolver
from scraper.live_crawler import LiveCrawler, FLASHSCORE_COMPETITIONS
from engine.jornada_predictor import JornadaPredictor
from engine.combo_bet_engine import ComboBetEngine
from engine.combo_tracker import ComboTracker
from engine.pdf_report_generator import PDFReportGenerator
from notifier.email_sender import EmailSender

COMPETITIONS = ["LALIGA", "PREMIER", "HYPERMOTION"]

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "LALIGA": {"last_predicted_jornada": 0, "last_predicted_date": "", "last_scraped_jornada": 0},
        "PREMIER": {"last_predicted_jornada": 0, "last_predicted_date": "", "last_scraped_jornada": 0},
        "HYPERMOTION": {"last_predicted_jornada": 0, "last_predicted_date": "", "last_scraped_jornada": 0},
        "updated_at": ""
    }

def save_state(state: Dict[str, Any]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def get_upcoming_round_info(comp_id: str, db: DatabaseManager = None) -> Optional[Dict[str, Any]]:
    """
    Obté la jornada immediata següent i la data del primer partit, FILTRANT STRICTAMENT
    per lliga regular (ignorant copes) i partits futurs que superin la darrera jornada finalitzada.
    """
    comp = comp_id.upper()
    base_url = FLASHSCORE_COMPETITIONS.get(comp)
    if not base_url:
        return None

    # 1. Obtenir la darrera jornada jugada a SQLite per no predir el passat
    _db = db if db else DatabaseManager()
    with _db.get_connection() as conn:
        c = conn.cursor()
        max_finished = c.execute(
            "SELECT MAX(jornada) FROM matches WHERE competition_id = ? AND status = 'FINISHED'",
            (comp,)
        ).fetchone()[0] or 0

    cmd = ['curl', '-s', f'{base_url}/partidos/']
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

    blocks = res.stdout.split('~')
    current_round = None
    round_matches = []
    now = datetime.now()

    for b in blocks:
        if 'ER÷' in b:
            m_round = re.search(r'ER÷([^¬~]+)', b)
            if m_round:
                current_round = m_round.group(1).strip()

        # Filtrar partits de Copa (Copa del Rey, EFL Cup, FA Cup, etc.)
        if current_round:
            cr_lower = current_round.lower()
            if any(cup in cr_lower for cup in ['copa', 'cup', 'fa cup', 'trophy', 'supercopa', 'playoff']):
                continue

        if 'AA÷' in b and 'AD÷' in b:
            # Comprovar que sigui una Jornada regular: "Jornada N", "Round N", "Matchday N"
            m_num = re.search(r'(?:Jornada|Round|Matchday)\s*(\d+)', current_round or '', re.IGNORECASE)
            if not m_num:
                continue

            jornada_num = int(m_num.group(1))
            # Només acceptar jornades posteriors o iguals a la darrera finalitzada
            if jornada_num < max_finished:
                continue

            fields = {}
            for token in re.split(r'[\xac\r\n\t]+', b):
                if '÷' in token or '\xf7' in token:
                    parts = re.split(r'[\xf7÷]', token, maxsplit=1)
                    if len(parts) == 2:
                        fields[parts[0]] = parts[1]

            ts_str = fields.get('AD', '')
            if ts_str.isdigit():
                match_dt = datetime.fromtimestamp(int(ts_str))
                # Només partits que encara s'hagin de jugar
                if match_dt > now - timedelta(hours=3):
                    round_matches.append({
                        "jornada": jornada_num,
                        "match_code": fields.get('AA'),
                        "home": fields.get('AE'),
                        "away": fields.get('AF'),
                        "datetime": match_dt
                    })

    if not round_matches:
        return None

    # Ordenar cronològicament
    round_matches.sort(key=lambda x: x["datetime"])
    first_match = round_matches[0]
    active_jornada = first_match["jornada"]
    earliest_matches = [m for m in round_matches if m["jornada"] == active_jornada]

    return {
        "competition_id": comp,
        "jornada": active_jornada,
        "first_match_dt": earliest_matches[0]["datetime"],
        "matches_count": len(earliest_matches)
    }

def run_daily_autonomous_check(force: bool = False):
    print("\n" + "=" * 80)
    print(f"   🤖 EXECUTOR AUTÒNOM DIARI DE PREDICCIONS · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 80)

    db = DatabaseManager()
    db.seed_initial_data()
    crawler = LiveCrawler(headless=True)
    tracker = ComboTracker(db=db)
    state = load_state()

    now = datetime.now()

    # -----------------------------------------------------------------
    # FASE 1: DESCARREGAR RESULTATS DE JORNADES COMPLETADES I ACTUALITZAR ELO
    # -----------------------------------------------------------------
    print("\n[*] FASE 1: Comprovació de marcadors de jornades acabades...")
    any_scraped = False
    for comp in COMPETITIONS:
        last_scraped = state[comp].get("last_scraped_jornada", 0)
        # Consultar si hi ha jornades amb partits jugats pendents d'actualitzar
        conn = db.get_connection()
        c = conn.cursor()
        max_j_row = c.execute("SELECT MAX(jornada) FROM matches WHERE competition_id = ? AND status = 'FINISHED'", (comp,)).fetchone()
        conn.close()

        latest_finished_j = max_j_row[0] if max_j_row and max_j_row[0] else 0
        if latest_finished_j > last_scraped:
            print(f"   • {comp}: S'han detectat nous resultats finalitzats a la Jornada {latest_finished_j}. Ingestant...")
            try:
                crawler.run_live_pipeline(jornada=latest_finished_j, competition_id=comp)
                state[comp]["last_scraped_jornada"] = latest_finished_j
                any_scraped = True
            except Exception as e:
                print(f"   [!] Error actualitzant {comp} J{latest_finished_j}: {e}")

    if any_scraped:
        print("[*] Avaluant combinades pendents amb els nous resultats...")
        evals = tracker.evaluate_all_pending_combos()
        sim_summary = tracker.get_simulation_summary()
        print(f"    • Balanç actual: {sim_summary['net_profit']:+.2f} € (ROI: {sim_summary['roi_pct']:+.1f}%)")
    else:
        print("   • No hi ha cap jornada nova pendent d'actualitzar.")

    # -----------------------------------------------------------------
    # FASE 2: DETECCIÓ D'INICI DE NOVA JORNADA I FINESTRA D'ÀRBITRES
    # -----------------------------------------------------------------
    print("\n[*] FASE 2: Comprovació de l'inici de noves jornades i calendari...")
    leagues_to_predict = []
    round_infos = {}

    for comp in COMPETITIONS:
        info = get_upcoming_round_info(comp, db=db)
        if not info:
            print(f"   • {comp}: No s'han trobat jornades regulars pendents a Flashscore.")
            continue

        j = info["jornada"]
        first_dt = info["first_match_dt"]
        hours_to_start = (first_dt - now).total_seconds() / 3600.0
        round_infos[comp] = info

        last_pred = state[comp].get("last_predicted_jornada", 0)

        # Si ja s'havia predit aquesta jornada: NO TORNAR A CÓRRER
        if last_pred >= j and not force:
            print(f"   • {comp} J{j}: [JA PREDITA] Va ser predita el {state[comp].get('last_predicted_date')}. S'omet per no repetir correus.")
            continue

        # Finestra d'or per a les designacions arbitrals:
        # Entre 0 i 30 hores abans del primer partit (o partits que comencen avui)
        # Així el CTA i PGMOL ja han fet públiques el 100% de les designacions oficials.
        if 0 <= hours_to_start <= 30.0 or force:
            print(f"   • {comp} J{j}: 🔥 COMENÇA AVIAT! (Primer partit: {first_dt.strftime('%d/%m %H:%M')}, en {hours_to_start:.1f}h). Àrbitres oficials assignats.")
            leagues_to_predict.append(comp)
        elif hours_to_start < 0:
            # La jornada ja ha començat; si no s'havia predit, predir partits restants
            print(f"   • {comp} J{j}: En curs (primer partit començat a les {first_dt.strftime('%d/%m %H:%M')}).")
            leagues_to_predict.append(comp)
        else:
            days_to_start = hours_to_start / 24.0
            print(f"   • {comp} J{j}: Falten {days_to_start:.1f} dies ({first_dt.strftime('%d/%m %H:%M')}). Esperant que s'apropi per tenir les designacions arbitrals oficials.")

    # -----------------------------------------------------------------
    # FASE 3: GENERACIÓ DE PREDICCIONS I INFORMES
    # -----------------------------------------------------------------
    if not leagues_to_predict:
        print("\n" + "-" * 80)
        print("   ℹ️ Cap lliga comença la seva jornada avui en la finestra d'enviament.")
        print("   L'executor finalitza sense enviar correus innecessaris.")
        print("=" * 80 + "\n")
        save_state(state)
        return

    print(f"\n[*] FASE 3: Generant prediccions per a: {', '.join(leagues_to_predict)}...")
    predictor = JornadaPredictor(db=db)
    generated_pdfs: List[Path] = []
    all_predicted_matches: List[Dict[str, Any]] = []

    for comp in leagues_to_predict:
        j = round_infos[comp]["jornada"]
        print(f"\n   >> Predient {comp} - Jornada {j}...")
        try:
            res = predictor.predict_jornada(
                jornada=j,
                competition_id=comp,
                include_odds=True,
                generate_pdf=True
            )
            if res.get("pdf_path"):
                generated_pdfs.append(Path(res["pdf_path"]))
            all_predicted_matches.extend(res.get("predicted_matches", []))

            # Actualitzar estat d'aquesta lliga
            state[comp]["last_predicted_jornada"] = j
            state[comp]["last_predicted_date"] = now.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"   [!] Error predient {comp} J{j}: {e}")

    # -----------------------------------------------------------------
    # FASE 4: MULTI-LLIGA COMBINADES (NOMÉS SI JUGUEN MÚLTIPLES LLIGUES)
    # -----------------------------------------------------------------
    # "cal pensar que per exemple si la lliga en juga una de intersetmeneal
    # però les altres no, no cal passar combinades, només quan juguin totes
    # al matiex cap de setmana o entre setmana"
    multiple_leagues_active = len(leagues_to_predict) >= 2

    if multiple_leagues_active and all_predicted_matches:
        print("\n[*] FASE 4: Múltiples lligues en joc simultani. Generant Mega-Combinada Multi-Lliga...")
        combo_engine = ComboBetEngine()
        multileague_combos = combo_engine.analyze_multileague_combos(all_predicted_matches)

        main_j = max(round_infos[c]["jornada"] for c in leagues_to_predict)

        safe_multi = multileague_combos.get("safe_combo", {})
        risky_multi = multileague_combos.get("risky_combo", {})
        if safe_multi and safe_multi.get("legs"):
            db.save_combo_recommendation("MULTI", "2026-2027", main_j, "SAFE", 25.0, safe_multi)
        if risky_multi and risky_multi.get("legs"):
            db.save_combo_recommendation("MULTI", "2026-2027", main_j, "RISKY", 5.0, risky_multi)

        highlight_matches = sorted(
            all_predicted_matches,
            key=lambda x: max(x.get("goals", {}).get("prob_1X2", {}).values() or [0]),
            reverse=True
        )[:5]

        pdf_gen = PDFReportGenerator()
        sim_summary = tracker.get_simulation_summary()
        multi_pdf = pdf_gen.generate_multileague_report(
            jornada=main_j,
            multileague_combos=multileague_combos,
            simulation_summary=sim_summary,
            highlight_matches=highlight_matches
        )
        generated_pdfs.append(multi_pdf)
        print(f"   [+] Informe Multi-Lliga generat amb èxit: {multi_pdf.name}")
    else:
        print("\n[*] FASE 4: Només juga una competició (jornada individual). S'omet l'informe combinat multi-lliga com s'ha demanat.")

    # -----------------------------------------------------------------
    # FASE 5: ENVIAMENT PER CORREU ELECTRÒNIC
    # -----------------------------------------------------------------
    if generated_pdfs:
        print("\n[*] FASE 5: Enviant informes generats per correu electrònic...")
        email_sender = EmailSender()
        active_j_str = ", ".join(f"{c} J{round_infos[c]['jornada']}" for c in leagues_to_predict)
        main_j = max(round_infos[c]["jornada"] for c in leagues_to_predict)
        res = email_sender.send_reports(
            pdf_paths=generated_pdfs,
            jornada=main_j
        )
        print(f"   [Èxit enviament correu]: {res}")

    save_state(state)
    print("\n" + "=" * 80)
    print("   ✅ CICLE DIARI COMPLETAT AMB ÈXIT")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    force_run = "--force" in sys.argv
    run_daily_autonomous_check(force=force_run)

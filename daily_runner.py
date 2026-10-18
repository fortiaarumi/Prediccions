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
COMPETITIONS = ["LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"]


def load_state() -> Dict[str, Any]:
    state = {
        comp: {"last_predicted_jornada": 0, "last_predicted_date": "", "last_scraped_jornada": 0}
        for comp in COMPETITIONS
    }
    state["updated_at"] = ""

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if k in state and isinstance(v, dict):
                        state[k].update(v)
                    elif k == "updated_at":
                        state[k] = v
        except Exception:
            pass
    return state

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
    try:
        comp = comp_id.upper()
        base_url = FLASHSCORE_COMPETITIONS.get(comp)
        if not base_url:
            return None

        # 1. Obtenir la darrera jornada completament finalitzada a SQLite per no predir el passat
        _db = db if db else DatabaseManager()
        state_file = ROOT / "data" / "pipeline_state.json"
        state_completed = 0
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                    state_completed = int(st.get(comp, {}).get("last_scraped_jornada", 0))
            except Exception:
                pass

        with _db.get_connection() as conn:
            c = conn.cursor()
            # Si SQLite ja té una jornada amb partits en curs (SCHEDULED o LIVE), aquesta és la jornada activa
            in_prog = c.execute("""
                SELECT jornada FROM matches
                WHERE competition_id = ? AND status IN ('SCHEDULED', 'LIVE')
                ORDER BY jornada ASC LIMIT 1
            """, (comp,)).fetchone()
            if in_prog:
                dt_row = c.execute("""
                    SELECT date_time FROM matches
                    WHERE competition_id = ? AND jornada = ? AND status IN ('SCHEDULED', 'LIVE')
                    ORDER BY date_time ASC LIMIT 1
                """, (comp, in_prog[0])).fetchone()
                first_dt = None
                if dt_row and dt_row[0]:
                    try:
                        clean_dt = str(dt_row[0]).strip()
                        if "." in clean_dt:
                            first_dt = datetime.strptime(clean_dt[:16], "%d.%m.%Y %H:%M")
                        elif "-" in clean_dt:
                            first_dt = datetime.strptime(clean_dt[:16], "%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                return {
                    "competition_id": comp,
                    "jornada": in_prog[0],
                    "first_match_dt": first_dt or datetime.now(),
                    "cluster_start_date": first_dt.date() if first_dt else datetime.now().date(),
                    "matches_count": 0
                }

            # Una jornada només es considera completament acabada si té suficients partits jugats i ZERO pendents
            c_sql = c.execute("""
                SELECT jornada FROM matches
                WHERE competition_id = ?
                GROUP BY jornada
                HAVING COUNT(CASE WHEN status = 'FINISHED' THEN 1 END) >= 7
                   AND COUNT(CASE WHEN status IN ('SCHEDULED', 'LIVE') THEN 1 END) = 0
                ORDER BY jornada DESC LIMIT 1
            """, (comp,)).fetchone()
            sql_completed = c_sql[0] if c_sql else 0
            max_completed = max(sql_completed, state_completed)

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

            # Filtrar partits de Copa (Copa del Rey, EFL Cup, FA Cup, DFB-Pokal, Coupe de France, Coppa Italia, etc.)
            if current_round:
                cr_lower = current_round.lower()
                if any(cup in cr_lower for cup in ['copa', 'cup', 'fa cup', 'trophy', 'supercopa', 'playoff', 'pokal', 'coupe', 'coppa']):
                    continue

            if 'AA÷' in b and 'AD÷' in b:
                # Comprovar que sigui una Jornada regular: "Jornada N", "Round N", "Matchday N", "Spieltag N", "Journée N", "Giornata N"
                m_num = re.search(r'(?:Jornada|Round|Matchday|Spieltag|Journ[eé]e|Giornata)\s*(\d+)', current_round or '', re.IGNORECASE)
                if not m_num:
                    continue

                jornada_num = int(m_num.group(1))
                # Només acceptar jornades posteriors a la darrera completada
                if jornada_num <= max_completed:
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

        # Determinar quina és la jornada regular en curs o immediata següent
        upcoming_jornadas = sorted(list({m["jornada"] for m in round_matches if m["jornada"] >= max_completed + 1}))
        if not upcoming_jornadas:
            upcoming_jornadas = sorted(list({m["jornada"] for m in round_matches}))
        if not upcoming_jornadas:
            return None

        # La jornada activa és la més propera en ordre numèric (evita salts a partits avançats aïllats de jornades llunyanes)
        target_jornada = upcoming_jornadas[0]
        j_matches = [m for m in round_matches if m["jornada"] == target_jornada]
        j_matches.sort(key=lambda x: x["datetime"])

        # Detectar el clúster principal de la jornada (ignorant partits avançats/aplaçats aïllats)
        from engine.web_data_exporter import WebDataExporter
        exporter_helper = WebDataExporter(db=_db)
        match_dates = [m["datetime"].date() for m in j_matches]
        cluster_start_date = exporter_helper._get_jornada_cluster_start_date(match_dates)

        if cluster_start_date:
            cluster_matches = [m for m in j_matches if m["datetime"].date() >= cluster_start_date]
            first_dt = min(m["datetime"] for m in cluster_matches) if cluster_matches else j_matches[0]["datetime"]
        else:
            first_dt = j_matches[0]["datetime"]

        return {
            "competition_id": comp,
            "jornada": target_jornada,
            "first_match_dt": first_dt,
            "cluster_start_date": cluster_start_date,
            "matches_count": len(j_matches)
        }
    except Exception as e:
        print(f"   [!] Error analitzant calendari Flashscore per a {comp_id}: {e}")
        return None

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
    all_newly_ingested_matches: List[Dict[str, Any]] = []
    all_evaluated_combos: List[Dict[str, Any]] = []
    all_new_combos: List[Dict[str, Any]] = []

    for comp in COMPETITIONS:
        last_scraped = state[comp].get("last_scraped_jornada", 0)

        # 1. Comprovar partits SCHEDULED a SQLite que ja haurien d'estar jugats
        conn = db.get_connection()
        c = conn.cursor()
        scheduled_j = [
            r[0] for r in c.execute(
                "SELECT DISTINCT jornada FROM matches WHERE competition_id = ? AND status = 'SCHEDULED'",
                (comp,)
            ).fetchall()
        ]
        conn.close()

        jornadas_to_check = set(scheduled_j)
        jornadas_to_check.add(last_scraped + 1)
        jornadas_to_check.add(last_scraped + 2)

        # Detectar la jornada activa o en curs a Flashscore per assegurar persistència de noves lligues
        u_info = get_upcoming_round_info(comp, db=db)
        if u_info and u_info.get("jornada"):
            act_j = int(u_info["jornada"])
            jornadas_to_check.add(act_j)
            if act_j > 1:
                jornadas_to_check.add(act_j - 1)

        for j_cand in sorted(list(jornadas_to_check)):
            if j_cand <= 0:
                continue
            try:
                fixtures = crawler.crawl_jornada_fixtures_from_web(j_cand, competition_id=comp)
                has_played = any(f.get("score_h") is not None for f in fixtures)
                if has_played:
                    print(f"   • {comp}: S'han detectat nous resultats a la Jornada {j_cand}. Ingestant partits i actualitzant Elo...")
                    new_m = crawler.run_live_pipeline(jornada=j_cand, competition_id=comp)
                    if new_m:
                        all_newly_ingested_matches.extend(new_m)

                    # Si tots els partits estan finalitzats, actualitzar estat
                    if fixtures and all(f.get("score_h") is not None for f in fixtures):
                        if j_cand > state[comp]["last_scraped_jornada"]:
                            state[comp]["last_scraped_jornada"] = j_cand
            except Exception as e:
                print(f"   [!] Error comprovant {comp} J{j_cand}: {e}")

    # Avaluar combinades pendents amb els nous resultats
    print("[*] Avaluant combinades pendents amb els nous resultats...")
    evals = tracker.evaluate_all_pending_combos()
    if evals:
        all_evaluated_combos.extend(evals)
    sim_summary = tracker.get_simulation_summary()
    print(f"    • Balanç actual: {sim_summary['net_profit']:+.2f} € (ROI: {sim_summary['roi_pct']:+.1f}%)")

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
        if 0 <= hours_to_start <= 30.0 or force:
            print(f"   • {comp} J{j}: 🔥 COMENÇA AVIAT! (Primer partit: {first_dt.strftime('%d/%m %H:%M')}, en {hours_to_start:.1f}h). Àrbitres oficials assignats.")
            leagues_to_predict.append(comp)
        elif hours_to_start < 0:
            print(f"   • {comp} J{j}: ⚽ En curs (primer partit començat a les {first_dt.strftime('%d/%m %H:%M')}). Combinades congelades oficials amb seguiment de progrés en directe.")
        else:
            days_to_start = hours_to_start / 24.0
            print(f"   • {comp} J{j}: Falten {days_to_start:.1f} dies ({first_dt.strftime('%d/%m %H:%M')}). Esperant que s'apropi per tenir les designacions arbitrals oficials.")

    # -----------------------------------------------------------------
    # FASE 3: GENERACIÓ DE PREDICCIONS I INFORMES
    # -----------------------------------------------------------------
    generated_pdfs: List[Path] = []
    all_predicted_matches: List[Dict[str, Any]] = []

    if not leagues_to_predict:
        print("\n" + "-" * 80)
        print("   ℹ️ Cap lliga comença la seva jornada avui en la finestra d'enviament.")
        print("   S'omet l'enviament de correu, però s'actualitzen les dades de la web i el balanç.")
        print("-" * 80)
    else:
        print(f"\n[*] FASE 3: Generant prediccions per a: {', '.join(leagues_to_predict)}...")
        predictor = JornadaPredictor(db=db)

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

                # Registrar noves combinades generades
                if res.get("combo_bets"):
                    cb = res["combo_bets"]
                    for sc in cb.get("safe", []):
                        if sc and sc.get("legs"):
                            all_new_combos.append({"competition_id": comp, "jornada": j, "profile": sc.get("profile", "SAFE"), "odd": sc.get("combined_odd", 1.0)})
                    for sm in cb.get("semi", []):
                        if sm and sm.get("legs"):
                            all_new_combos.append({"competition_id": comp, "jornada": j, "profile": sm.get("profile", "SEMI"), "odd": sm.get("combined_odd", 1.0)})
                    for rc in cb.get("risky", []):
                        if rc and rc.get("legs"):
                            all_new_combos.append({"competition_id": comp, "jornada": j, "profile": rc.get("profile", "RISKY"), "odd": rc.get("combined_odd", 1.0)})

                # Actualitzar estat d'aquesta lliga
                state[comp]["last_predicted_jornada"] = j
                state[comp]["last_predicted_date"] = now.strftime("%Y-%m-%d")
            except Exception as e:
                print(f"   [!] Error predient {comp} J{j}: {e}")

        # -----------------------------------------------------------------
        # FASE 4: COMBINADES DEL DIA (SUITE DE 6 COMBINADES DIÀRIES)
        # -----------------------------------------------------------------
        print("\n[*] FASE 4: Processament i avaluació de les Combinades del Dia...")
        today_iso = now.strftime("%Y-%m-%d")
        combo_engine = ComboBetEngine()

        def _norm_date(d_val):
            if not d_val: return ""
            dc = str(d_val).strip().split(" ")[0]
            if "." in dc:
                p = dc.split(".")
                if len(p) == 3:
                    if len(p[2]) == 4: return f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
                    elif len(p[0]) == 4: return f"{p[0]}-{p[1].zfill(2)}-{p[2].zfill(2)}"
            elif "-" in dc:
                p = dc.split("-")
                if len(p) == 3:
                    if len(p[2]) == 4: return f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
                    elif len(p[0]) == 4: return f"{p[0]}-{p[1].zfill(2)}-{p[2].zfill(2)}"
            return dc

        # Obtenir tots els partits d'avui de totes les 4 competicions (no només les que s'han predit en aquest cicle)
        from engine.web_data_exporter import WebDataExporter, COMPETITIONS_META
        exporter = WebDataExporter(db=db)

        all_today_matches = []
        today_match_keys = set()
        for m in all_predicted_matches:
            if _norm_date(m.get("date", "")) == today_iso:
                all_today_matches.append(m)
                h_id = m.get("home_team", {}).get("id") if isinstance(m.get("home_team"), dict) else m.get("home_team_id")
                a_id = m.get("away_team", {}).get("id") if isinstance(m.get("away_team"), dict) else m.get("away_team_id")
                today_match_keys.add(f"{h_id}_{a_id}")

        for comp_meta in COMPETITIONS_META:
            cid = comp_meta["id"]
            if cid not in leagues_to_predict:
                try:
                    pred_data = exporter.get_upcoming_predictions(cid)
                    for m in pred_data.get("matches", []):
                        if _norm_date(m.get("date", "")) == today_iso:
                            h_id = m.get("home_team", {}).get("id")
                            a_id = m.get("away_team", {}).get("id")
                            key = f"{h_id}_{a_id}"
                            if key not in today_match_keys:
                                today_match_keys.add(key)
                                all_today_matches.append(m)
                except Exception as e_c:
                    print(f"   [!] Error consultant partits d'avui per a {cid}: {e_c}")

        today_matches = all_today_matches

        if not today_matches and all_predicted_matches:
            earliest_dates = sorted(list({_norm_date(m.get("date", "")) for m in all_predicted_matches if m.get("date")}))
            print(f"   • Avui ({today_iso}) no hi ha partits programats. Propers dies amb partits: {', '.join(earliest_dates[:3])}")
        elif today_matches:
            comps_today = set(m.get("competition_id") for m in today_matches if m.get("competition_id"))
            print(f"   • S'han detectat {len(today_matches)} partits per al dia d'avui ({today_iso}) involucrant {len(comps_today)} lligues ({', '.join(comps_today)}).")
            if not db.has_daily_combos(today_iso, min_expected=6):
                db.delete_daily_combos_for_date(today_iso)
                daily_suite = combo_engine.generate_daily_combos_suite(today_matches)
                for idx, sc in enumerate(daily_suite.get("safe", []), 1):
                    if sc and sc.get("legs"):
                        db.save_daily_combo(today_iso, f"SAFE_{idx}", 25.0, sc)
                        all_new_combos.append({"competition_id": "DAILY", "jornada": 0, "profile": f"SAFE_{idx}", "odd": sc.get("combined_odd", 1.0)})
                for idx, sm in enumerate(daily_suite.get("semi", []), 1):
                    if sm and sm.get("legs"):
                        db.save_daily_combo(today_iso, f"SEMI_{idx}", 10.0, sm)
                        all_new_combos.append({"competition_id": "DAILY", "jornada": 0, "profile": f"SEMI_{idx}", "odd": sm.get("combined_odd", 1.0)})
                for idx, rc in enumerate(daily_suite.get("risky", []), 1):
                    if rc and rc.get("legs"):
                        db.save_daily_combo(today_iso, f"RISKY_{idx}", 5.0, rc)
                        all_new_combos.append({"competition_id": "DAILY", "jornada": 0, "profile": f"RISKY_{idx}", "odd": rc.get("combined_odd", 1.0)})
                print(f"   [+] Combinades del dia generades i congelades oficials per a {today_iso}!")
            else:
                print(f"   🔒 [JA GENERADES AVUI] Les 6 combinades del dia per a {today_iso} ja estaven congelades oficialment. Es preserven sense sobreescriure.")

    # -----------------------------------------------------------------
    # FASE 5: EXPORTACIÓ PER A LA WEB I SECCIÓ DE NOVETATS (SEMPRE S'EXECUTA)
    # -----------------------------------------------------------------
    print("\n[*] FASE 5: Actualitzant la secció de novetats i plataforma web a 'web/data/data.json'...")
    try:
        from engine.web_data_exporter import WebDataExporter
        exporter = WebDataExporter(db=db)
        exported_path = exporter.export_all(
            ingested_matches=all_newly_ingested_matches,
            evaluated_combos=all_evaluated_combos,
            new_combos=all_new_combos,
            force_combos=force
        )
        print(f"   [+] Web actualitzada amb èxit: {exported_path}")
    except Exception as e:
        print(f"   [!] Error actualitzant dades de la web: {e}")

    save_state(state)
    print("\n" + "=" * 80)
    print("   ✅ CICLE DIARI COMPLETAT AMB ÈXIT")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    force_run = "--force" in sys.argv
    try:
        run_daily_autonomous_check(force=force_run)
    except Exception as e:
        import traceback
        print(f"\n❌ [ERROR CRÍTIC EN EL PIPELINE DIARI]: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

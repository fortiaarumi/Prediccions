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
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

MADRID_TZ = ZoneInfo("Europe/Madrid")

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


def update_referees_for_round(comp_id: str, jornada: int, db: DatabaseManager) -> int:
    """Actualitza les designacions arbitrals oficials des de Flashscore abans de calcular les combinades."""
    try:
        from scraper.live_crawler import LiveCrawler
        from scraper.match_scraper import MatchScraper
        crawler = LiveCrawler(headless=True)
        fixtures = crawler.crawl_jornada_fixtures_from_web(jornada, competition_id=comp_id)
        scraper = MatchScraper(headless=True)
        updated = 0
        for f in fixtures:
            url = f.get("url") or ""
            if "partido" in url:
                m_code = crawler.extract_match_code(url)
                if m_code:
                    res = scraper._scrape_feed(m_code)
                    if res and res.get("referee") and res["referee"] != "REF_DEFAULT":
                        ref_id = db.get_or_create_referee(res["referee"], competition_id=comp_id)
                        with db.get_connection() as conn:
                            conn.execute(
                                "UPDATE matches SET referee_id = ? WHERE (url LIKE ? OR id LIKE ?) AND (referee_id IS NULL OR referee_id = 'REF_DEFAULT')",
                                (ref_id, f"%{m_code}%", f"%{m_code}%")
                            )
                            conn.commit()
                        updated += 1
        return updated
    except Exception as e:
        print(f"   [!] Error actualitzant designacions arbitrals per a {comp_id} J{jornada}: {e}")
        return 0


def has_sufficient_referees(comp_id: str, jornada: int, db: DatabaseManager) -> bool:
    """Verifica si hi ha informació arbitral suficient per a la jornada."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("""
                SELECT COUNT(*),
                       COUNT(CASE WHEN referee_id IS NOT NULL AND referee_id != 'REF_DEFAULT' THEN 1 END)
                FROM matches
                WHERE competition_id = ? AND jornada = ?
            """, (comp_id, jornada)).fetchone()
            if not row or row[0] == 0:
                return True
            total, with_refs = row
            return with_refs >= max(1, int(total * 0.4))
    except Exception:
        return True


def should_generate_league_combos(
    comp_id: str,
    jornada: int,
    first_match_dt: datetime,
    current_dt_madrid: Optional[datetime] = None,
    db: Optional[DatabaseManager] = None,
    state: Optional[Dict[str, Any]] = None,
    force: bool = False,
    check_referees: bool = True
) -> Tuple[bool, str]:
    """
    Determina si s'han de generar les combinades de lliga per a una competició i jornada,
    complint les regles de planificació:
    1. Dia natural del primer partit a Europe/Madrid (NO el dia anterior).
    2. Abans de l'inici del partit (si ja ha començat, no crear prematch tardà).
    3. Designacions arbitrals actualitzades i suficients.
    4. Congelació de combinades existents (no duplicar).
    """
    if current_dt_madrid is None:
        now_m = datetime.now(MADRID_TZ)
    elif current_dt_madrid.tzinfo is None:
        now_m = current_dt_madrid.replace(tzinfo=MADRID_TZ)
    else:
        now_m = current_dt_madrid.astimezone(MADRID_TZ)

    if first_match_dt.tzinfo is None:
        first_dt_m = first_match_dt.replace(tzinfo=MADRID_TZ)
    else:
        first_dt_m = first_match_dt.astimezone(MADRID_TZ)

    _db = db or DatabaseManager()

    # Regla 7: Combinada ja generada i guardada es manté congelada (no duplicar)
    if not force:
        saved_combos = _db.get_combos_for_jornada(comp_id, jornada)
        if saved_combos and len(saved_combos) >= 2:
            return False, f"ALREADY_SAVED: La combinada de {comp_id} J{jornada} ja està congelada a SQLite ({len(saved_combos)} combinades)."

        if state and state.get(comp_id, {}).get("last_predicted_jornada", 0) >= jornada:
            return False, f"ALREADY_PREDICTED: La jornada {jornada} de {comp_id} ja consta com a predita."

    # Regla 4: El dia anterior no generis la combinada de lliga
    if now_m.date() < first_dt_m.date() and not force:
        days_diff = (first_dt_m.date() - now_m.date()).days
        return False, f"WAITING_FOR_MATCHDAY: Falten {days_diff} dies per a l'inici ({first_dt_m.strftime('%d/%m/%Y %H:%M')}). Les combinades es generen el mateix dia d'inici per tenir els àrbitres oficials."

    # Regla 8: Si arriba l'hora d'inici sense haver-la pogut generar, no creïs una combinada prematch tardana
    if now_m >= first_dt_m and not force:
        return False, f"MATCH_ALREADY_STARTED: L'hora d'inici del primer partit ({first_dt_m.strftime('%d/%m/%Y %H:%M')}) ja ha passat. No es creen combinades prematch tardanes."

    # Regles 3, 5 i 6: El dia correcte abans del primer partit, comprovar àrbitres
    if check_referees and not force:
        has_refs = has_sufficient_referees(comp_id, jornada, _db)
        if not has_refs:
            updated = update_referees_for_round(comp_id, jornada, _db)
            has_refs = has_sufficient_referees(comp_id, jornada, _db)
            if not has_refs:
                return False, f"WAITING_FOR_REFEREES: Informació arbitral oficial insuficient per a {comp_id} J{jornada}. Es reintentarà abans del primer partit."

    hours_left = (first_dt_m - now_m).total_seconds() / 3600.0
    return True, f"READY: El primer partit comença avui a les {first_dt_m.strftime('%H:%M')} (falten {hours_left:.1f}h). Àrbitres confirmats."


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
            # Si SQLite ja té una jornada amb partits en curs (SCHEDULED o LIVE), ordenem per la data més propera
            # per evitar que un partit ajornat d'una jornada anterior usurpi la jornada activa.
            in_prog = c.execute("""
                SELECT m.jornada, min(
                    CASE
                        WHEN m.date_time LIKE '__.__.____ __:__'
                        THEN substr(m.date_time, 7, 4) || '-' || substr(m.date_time, 4, 2) || '-' || substr(m.date_time, 1, 2) || ' ' || substr(m.date_time, 12, 5)
                        ELSE m.date_time
                    END
                ) as min_dt
                FROM matches m
                WHERE m.competition_id = ? AND m.status IN ('SCHEDULED', 'LIVE')
                  AND m.home_team_id NOT IN (SELECT id FROM teams WHERE name LIKE '%U21%' OR name LIKE '%U18%' OR name LIKE '%U19%' OR name LIKE '%U23%' OR name LIKE '%Sub-21%' OR name LIKE '%Sub-18%' OR name LIKE '%Reserves%')
                  AND m.away_team_id NOT IN (SELECT id FROM teams WHERE name LIKE '%U21%' OR name LIKE '%U18%' OR name LIKE '%U19%' OR name LIKE '%U23%' OR name LIKE '%Sub-21%' OR name LIKE '%Sub-18%' OR name LIKE '%Reserves%')
                GROUP BY m.jornada
                ORDER BY min_dt ASC
                LIMIT 1
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
                SELECT m.jornada FROM matches m
                WHERE m.competition_id = ?
                  AND m.home_team_id NOT IN (SELECT id FROM teams WHERE name LIKE '%U21%' OR name LIKE '%U18%' OR name LIKE '%U19%' OR name LIKE '%U23%' OR name LIKE '%Sub-21%' OR name LIKE '%Sub-18%' OR name LIKE '%Reserves%')
                  AND m.away_team_id NOT IN (SELECT id FROM teams WHERE name LIKE '%U21%' OR name LIKE '%U18%' OR name LIKE '%U19%' OR name LIKE '%U23%' OR name LIKE '%Sub-21%' OR name LIKE '%Sub-18%' OR name LIKE '%Reserves%')
                GROUP BY m.jornada
                HAVING COUNT(CASE WHEN m.status = 'FINISHED' THEN 1 END) >= 7
                   AND COUNT(CASE WHEN m.status IN ('SCHEDULED', 'LIVE') THEN 1 END) = 0
                ORDER BY m.jornada DESC LIMIT 1
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

                h_check = fields.get('AE', '')
                a_check = fields.get('AF', '')
                if any(u in h_check.lower() or u in a_check.lower() for u in ['u21', 'u18', 'u19', 'u23', 'sub-21', 'sub-18', 'reserves']):
                    continue

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
    print("\n[*] FASE 2: Comprovació de l'inici de noves jornades i calendari (Horari Europe/Madrid)...")
    now_madrid = datetime.now(MADRID_TZ)
    leagues_to_predict = []
    round_infos = {}

    for comp in COMPETITIONS:
        info = get_upcoming_round_info(comp, db=db)
        if not info:
            print(f"   • {comp}: No s'han trobat jornades regulars pendents a Flashscore.")
            continue

        j = info["jornada"]
        first_dt = info["first_match_dt"]
        round_infos[comp] = info

        should_gen, reason = should_generate_league_combos(
            comp_id=comp,
            jornada=j,
            first_match_dt=first_dt,
            current_dt_madrid=now_madrid,
            db=db,
            state=state,
            force=force
        )

        if should_gen:
            print(f"   • {comp} J{j}: 🔥 COMENÇA AVUI! {reason}")
            leagues_to_predict.append(comp)
        else:
            print(f"   • {comp} J{j}: ⏳ SALT DE GENERACIÓ: {reason}")

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

        # Obtenir tots els partits d'avui de totes les 7 competicions (no només les que s'han predit en aquest cicle)
        from engine.web_data_exporter import WebDataExporter, COMPETITIONS_META
        exporter = WebDataExporter(db=db)

        # Neteja de seguretat: assegurar que mai quedi cap combinada futura (> today_iso) registrada per error
        purged_cnt = db.purge_future_daily_combos(today_iso)
        if purged_cnt > 0:
            print(f"   🧹 S'han netejat {purged_cnt} combinades de dies futurs bloquejades prematurament a SQLite.")

        def _get_match_key(m_dict):
            h = m_dict.get("home_team")
            a = m_dict.get("away_team")
            h_id = (h.get("id") or h.get("name") if isinstance(h, dict) else str(h or m_dict.get("home_team_id") or "")).strip().lower()
            a_id = (a.get("id") or a.get("name") if isinstance(a, dict) else str(a or m_dict.get("away_team_id") or "")).strip().lower()
            if h_id and a_id:
                return f"{h_id}_{a_id}"
            matchup = (m_dict.get("matchup") or "").strip().lower()
            return matchup or str(m_dict.get("id") or m_dict.get("match_id"))

        all_today_matches = []
        today_match_keys = set()
        for m in all_predicted_matches:
            if _norm_date(m.get("date", "")) == today_iso:
                k = _get_match_key(m)
                if k not in today_match_keys:
                    today_match_keys.add(k)
                    all_today_matches.append(m)

        for comp_meta in COMPETITIONS_META:
            cid = comp_meta["id"]
            if cid not in leagues_to_predict:
                try:
                    pred_data = exporter.get_upcoming_predictions(cid)
                    for m in pred_data.get("matches", []):
                        if _norm_date(m.get("date", "")) == today_iso:
                            k = _get_match_key(m)
                            if k not in today_match_keys:
                                today_match_keys.add(k)
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
            min_m = min(len(today_matches), 2)
            if not db.has_daily_combos(today_iso, min_expected=6, min_matches=min_m):
                print(f"   [*] Generant les 6 combinades oficials del dia per a {today_iso} (Primer pipeline 08:23h o fallback 15:23h)...")
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

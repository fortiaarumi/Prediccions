"""
scraper/live_crawler.py
=======================
CRAWLER OFICIAL MULTI-LLIGA EN DIRECTE (LALIGA, PREMIER LEAGUE, HYPERMOTION).

- Filtra estrictament per la jornada sol·licitada ('Jornada 1', 'Round 1', etc.).
- Comprovació intel·ligent: SI UN PARTIT JA ESTÀ GUARDAT COM A FINALITZAT (FINISHED),
  NO ES TORNA A DESCARREGAR NI ES TORNA A APLICAR L'ELO DOS COPS.
- Només descarrega i actualitza els partits nous o pendents que s'hagin jugat recentment.
- Genera data/{competition}_jornada_{N}_urls.txt amb enllaços directes a ESTADÍSTIQUES.
"""

import sys
import os
import re
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from database.db_manager import DatabaseManager
from scraper.match_scraper import MatchScraper
from model.rank_engine import RankEngine

DATA_DIR = Path(__file__).parent.parent / "data"

FLASHSCORE_COMPETITIONS = {
    "LALIGA": "https://www.flashscore.es/futbol/espana/laliga-ea-sports",
    "PREMIER": "https://www.flashscore.es/futbol/inglaterra/premier-league",
    "HYPERMOTION": "https://www.flashscore.es/futbol/espana/laliga-hypermotion",
    "CHAMPIONSHIP": "https://www.flashscore.es/futbol/inglaterra/championship",
    "BUNDESLIGA": "https://www.flashscore.es/futbol/alemania/bundesliga",
    "LIGUE1": "https://www.flashscore.es/futbol/francia/ligue-1",
    "SERIEA": "https://www.flashscore.es/futbol/italia/serie-a",
}

class LiveCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless

    @staticmethod
    def build_match_id(competition_id: str, season: str, match_code: Optional[str] = None, home_id: str = "", away_id: str = "", jornada: int = 0) -> str:
        """Construeix un identificador de partit resilient incloent competició i temporada."""
        if match_code:
            return f"{competition_id}_{season}_{match_code}"
        return f"{competition_id}_{season}_J{jornada}_{home_id}_{away_id}" if jornada else f"{competition_id}_{season}_{home_id}_{away_id}"

    _build_match_id = build_match_id

    def crawl_jornada_fixtures_from_web(self, jornada: int, competition_id: str = "LALIGA") -> List[Dict[str, Any]]:
        """Rastreja a internet filtrant estrictament pel bloc de la 'JORNADA {jornada}' o 'Round {jornada}'."""
        comp_id = competition_id.upper()
        base_url = FLASHSCORE_COMPETITIONS.get(comp_id, FLASHSCORE_COMPETITIONS["LALIGA"])
        print(f"\n[*] Cercant a Flashscore exactament els partits de la Jornada {jornada} ({comp_id})...")
        fixtures = []

        def parse_feed_blocks(html_text, feed_key):
            start_str1 = f"cjs.initialFeeds['{feed_key}'] = {{"
            start_str2 = f'cjs.initialFeeds["{feed_key}"] = {{'
            idx = html_text.find(start_str1)
            if idx == -1:
                idx = html_text.find(start_str2)
            if idx == -1:
                return []
            data_start = html_text.find("data: `", idx) + len("data: `")
            data_end = html_text.find("`", data_start)
            raw_feed = html_text[data_start:data_end]

            blocks = raw_feed.split('~')
            current_round = None
            items = []
            for b in blocks:
                fields = {}
                for token in re.split(r'[\xac\r\n\t]+', b):
                    if '÷' in token or '\xf7' in token:
                        parts = re.split(r'[\xf7÷]', token, maxsplit=1)
                        if len(parts) == 2:
                            fields[parts[0]] = parts[1]
                if 'ER' in fields:
                    current_round = fields['ER']
                if 'AA' in fields and 'AE' in fields and 'AF' in fields:
                    h_name = fields.get('AE', '')
                    a_name = fields.get('AF', '')
                    if comp_id in {"LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"}:
                        if re.search(r'\b(?:U21|U18|U19|U20|U23|Sub[- ]?21|Sub[- ]?18|Sub[- ]?19|Reserves|Youth)\b', f"{h_name} {a_name}", re.IGNORECASE):
                            continue
                    ts = fields.get('AD')
                    dt_str = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M') if ts and ts.isdigit() else 'TBD'
                    am_note = fields.get('AM', '')
                    ac_code = fields.get('AC', '')
                    is_susp = (
                        ac_code in ['37', '4', '10', '11'] or
                        any(w in am_note.lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
                    )
                    # El partit NOMÉS és finalitzat si prové de /resultados/ o té codi oficial de finalitzat (AC='3')
                    is_fin = (feed_key == 'results' or str(ac_code) == '3') and not is_susp
                    items.append({
                        'round_str': current_round or '',
                        'match_code': fields.get('AA'),
                        'home': fields.get('AE'),
                        'away': fields.get('AF'),
                        'score_h': fields.get('AG') if is_fin else None,
                        'score_a': fields.get('AH') if is_fin else None,
                        'date': dt_str,
                        'url': f"https://www.flashscore.es/partido/{fields.get('AA')}/#/estadisticas-del-partido/0",
                        'is_suspended': is_susp,
                        'suspension_reason': am_note,
                        'is_finished': is_fin,
                        'ac_code': ac_code
                    })
            return items

        # 1. Obtenir resultats jugats
        curl_bin = 'curl.exe' if os.name == 'nt' else 'curl'
        cmd_res = [curl_bin, '-s', f'{base_url}/resultados/']
        res_res = subprocess.run(cmd_res, capture_output=True, text=True, encoding='utf-8', errors='replace')
        all_matches = parse_feed_blocks(res_res.stdout, 'results')

        # 2. Obtenir fixtures pendents
        cmd_fix = [curl_bin, '-s', f'{base_url}/partidos/']
        res_fix = subprocess.run(cmd_fix, capture_output=True, text=True, encoding='utf-8', errors='replace')
        all_matches.extend(parse_feed_blocks(res_fix.stdout, 'fixtures'))

        # 3. Obtenir partits en joc (LIVE) o de la jornada d'avui que no figurin a fixtures/results
        comp_patterns = {
            "LALIGA": r'SPAIN:\s*LaLiga(?!\d| Women)',
            "PREMIER": r'ENGLAND:\s*Premier League(?!\s*(?:2|Cup|Women|U\d+|Sub|Summer|International|\d))',
            "HYPERMOTION": r'SPAIN:\s*(?:LaLiga2|LaLiga Hypermotion)',
            "CHAMPIONSHIP": r'ENGLAND:\s*Championship(?!\s*Women)',
            "BUNDESLIGA": r'GERMANY:\s*Bundesliga(?!\s*Women)',
            "LIGUE1": r'FRANCE:\s*Ligue 1(?!\s*Women)',
            "SERIEA": r'ITALY:\s*Serie A(?!\s*Women)',
        }
        pat = comp_patterns.get(comp_id)
        if pat:
            try:
                cmd_live = [curl_bin, '-s', '-H', 'x-fsign: SW9D1eZo', 'https://local-global.flashscore.ninja/2/x/feed/f_1_0_3_es_1']
                res_live = subprocess.run(cmd_live, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
                if res_live.stdout:
                    live_blocks = res_live.stdout.split('~')
                    in_comp = False
                    for b in live_blocks:
                        if 'ZA÷' in b:
                            in_comp = bool(re.search(pat, b, re.IGNORECASE))
                        if in_comp and 'AA÷' in b and 'AE÷' in b and 'AF÷' in b:
                            fields = {}
                            for token in re.split(r'[\xac\r\n\t]+', b):
                                if '÷' in token or '\xf7' in token:
                                    parts = re.split(r'[\xf7÷]', token, maxsplit=1)
                                    if len(parts) == 2:
                                        fields[parts[0]] = parts[1]
                            h_raw = fields.get('AE', '')
                            a_raw = fields.get('AF', '')
                            if comp_id in {"LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"}:
                                if re.search(r'\b(?:U21|U18|U19|U20|U23|Sub[- ]?21|Sub[- ]?18|Sub[- ]?19|Reserves|Youth)\b', f"{h_raw} {a_raw}", re.IGNORECASE):
                                    continue
                            ts = fields.get('AD')
                            dt_str = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M') if ts and ts.isdigit() else 'TBD'
                            am_note = fields.get('AM', '')
                            ac_code = str(fields.get('AC', ''))
                            is_susp = (
                                ac_code in ['37', '4', '10', '11'] or
                                any(w in am_note.lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
                            )
                            is_live = ac_code in ['2', '6', '7', '8', '12', '13']
                            is_fin = (ac_code == '3') and not is_susp and not is_live
                            m_code = fields.get('AA')
                            existing = next((m for m in all_matches if m.get('match_code') == m_code), None)
                            if existing:
                                existing['is_live'] = is_live
                                existing['is_finished'] = is_fin
                                existing['is_suspended'] = is_susp
                                existing['ac_code'] = ac_code
                                if am_note:
                                    existing['suspension_reason'] = am_note
                                if is_live or is_fin:
                                    existing['score_h'] = fields.get('AG')
                                    existing['score_a'] = fields.get('AH')
                            else:
                                live_round = None
                                try:
                                    from database.db_manager import DatabaseManager
                                    _db = DatabaseManager()
                                    with _db.get_connection() as _conn:
                                        _c = _conn.cursor()
                                        _row = _c.execute(
                                            "SELECT jornada FROM matches WHERE id LIKE ? OR (home_team_id = ? AND away_team_id = ?)",
                                            (f"%{m_code}%", _db.find_team_id(fields.get('AE')), _db.find_team_id(fields.get('AF')))
                                        ).fetchone()
                                        if _row:
                                            live_round = _row[0]
                                except Exception:
                                    pass

                                if not live_round and dt_str != 'TBD':
                                    m_date_prefix = dt_str.split(' ')[0]
                                    same_day_rounds = [
                                        m.get('round_str') for m in all_matches 
                                        if m.get('date', '').startswith(m_date_prefix) and m.get('round_str')
                                    ]
                                    if same_day_rounds:
                                        from collections import Counter
                                        live_round = Counter(same_day_rounds).most_common(1)[0][0]
                                    else:
                                        try:
                                            with _db.get_connection() as _conn2:
                                                _db_r = _conn2.cursor().execute(
                                                    "SELECT jornada FROM matches WHERE competition_id = ? AND date_time LIKE ? AND jornada > 0 ORDER BY jornada DESC LIMIT 1",
                                                    (comp_id, f"{m_date_prefix}%")
                                                ).fetchone()
                                                if _db_r:
                                                    live_round = _db_r[0]
                                        except Exception:
                                            pass

                                r_str = f"Jornada {live_round}" if isinstance(live_round, int) else (live_round or f"Jornada {jornada}")
                                all_matches.append({
                                    'round_str': r_str,
                                    'match_code': m_code,
                                    'home': fields.get('AE'),
                                    'away': fields.get('AF'),
                                    'score_h': fields.get('AG') if (is_fin or is_live) else None,
                                    'score_a': fields.get('AH') if (is_fin or is_live) else None,
                                    'date': dt_str,
                                    'url': f"https://www.flashscore.es/partido/{fields.get('AA')}/#/estadisticas-del-partido/0",
                                    'is_suspended': is_susp,
                                    'suspension_reason': am_note,
                                    'is_finished': is_fin,
                                    'is_live': is_live,
                                    'ac_code': ac_code
                                })
            except Exception:
                pass

        # Filtrar exactament per Jornada/Round N
        for m in all_matches:
            r_str = m.get('round_str', '')
            match_round_num = None
            m_num = re.search(r'(?:Jornada|Round|Matchday|Spieltag|Journ[eé]e|Giornata)\s*(\d+)|(\d+)\.?\s*(?:Spieltag|Journ[eé]e|Giornata)', r_str, re.IGNORECASE)
            if m_num:
                match_round_num = int(m_num.group(1) or m_num.group(2))

            if match_round_num == jornada:
                existing_idx = next((i for i, f in enumerate(fixtures) if f["match_code"] == m["match_code"] or (f["home"] == m["home"] and f["away"] == m["away"])), None)
                if existing_idx is not None:
                    if m.get("is_live") or m.get("is_finished"):
                        fixtures[existing_idx] = {
                            "competition_id": comp_id,
                            "home": m["home"],
                            "away": m["away"],
                            "date": m["date"],
                            "url": m["url"],
                            "match_code": m["match_code"],
                            "jornada": jornada,
                            "score_h": m.get("score_h"),
                            "score_a": m.get("score_a"),
                            "is_suspended": m.get("is_suspended", False),
                            "suspension_reason": m.get("suspension_reason", ""),
                            "is_finished": m.get("is_finished", False),
                            "is_live": m.get("is_live", False),
                            "ac_code": m.get("ac_code", "")
                        }
                else:
                    fixtures.append({
                        "competition_id": comp_id,
                        "home": m["home"],
                        "away": m["away"],
                        "date": m["date"],
                        "url": m["url"],
                        "match_code": m["match_code"],
                        "jornada": jornada,
                        "score_h": m.get("score_h"),
                        "score_a": m.get("score_a"),
                        "is_suspended": m.get("is_suspended", False),
                        "suspension_reason": m.get("suspension_reason", ""),
                        "is_finished": m.get("is_finished", False),
                        "is_live": m.get("is_live", False),
                        "ac_code": m.get("ac_code", "")
                    })

        print(f"[*] S'han trobat {len(fixtures)} partits oficials per a la Jornada {jornada} de {comp_id}.")

        # Escriure el fitxer d'enllaços oficials d'estadístiques
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        urls_file = DATA_DIR / f"{comp_id.lower()}_jornada_{jornada}_urls.txt"
        with open(urls_file, "w", encoding="utf-8") as f:
            f.write(f"# ENLLAÇOS OFICIALS EXTRASTS D'INTERNET PER A LA JORNADA {jornada} ({comp_id})\n")
            for fix in fixtures:
                f.write(f"{fix['home']} vs {fix['away']} | {fix['date']} | {fix['url']}\n")

        return fixtures

    def run_live_pipeline(self, jornada: int, competition_id: str = "LALIGA"):
        """
        Executa el flux complet per a la competició seleccionada.
        """
        comp_id = competition_id.upper()
        fixtures = self.crawl_jornada_fixtures_from_web(jornada, competition_id=comp_id)

        db = DatabaseManager()
        db.seed_initial_data()
        teams_count = 24 if comp_id == "CHAMPIONSHIP" else (22 if comp_id == "HYPERMOTION" else (18 if comp_id in ["BUNDESLIGA", "LIGUE1"] else 20))
        rank_engine = RankEngine(league_teams_count=teams_count)
        scraper = MatchScraper(headless=self.headless)

        print(f"\n[*] Processant els {len(fixtures)} partits de la Jornada {jornada} ({comp_id})...")

        conn = db.get_connection()
        cursor = conn.cursor()

        ingested_count = 0
        skipped_count = 0
        newly_ingested_matches = []

        for fix in fixtures:
            home = fix["home"]
            away = fix["away"]
            url = fix["url"]

            home_id = db.find_team_id(home) or db.get_or_create_team(home)
            away_id = db.find_team_id(away) or db.get_or_create_team(away)
            match_code = fix.get("match_code")
            season = "2026-2027"

            # 1. Comprovar si el partit ja estava guardat i finalitzat a la base de dades
            # Cerca per match_code a la URL, per clau canònica o per enfrontament natural (competició, temporada, equips)
            cursor.execute("""
                SELECT id, status, home_goals, away_goals, jornada, date_time
                FROM matches
                WHERE (url LIKE '%' || ? || '%' AND ? != '')
                   OR (competition_id = ? AND season = ? AND home_team_id = ? AND away_team_id = ?)
            """, (match_code or '', match_code or '', comp_id, season, home_id, away_id))
            existing = cursor.fetchone()

            is_live_fix = fix.get("is_live", False)
            if existing:
                match_id = existing["id"]
                if existing["status"] == "FINISHED" and existing["home_goals"] is not None:
                    print(f">> {home_id} vs {away_id}: [JA INGESTAT PRÈVIAMENT ({existing['home_goals']}-{existing['away_goals']})] - Saltant per no duplicar Elo.")
                    skipped_count += 1
                    continue
                elif existing["status"] == "LIVE" and is_live_fix:
                    h_cur = int(fix.get("score_h") or 0) if fix.get("score_h") is not None and str(fix.get("score_h")).isdigit() else existing["home_goals"]
                    a_cur = int(fix.get("score_a") or 0) if fix.get("score_a") is not None and str(fix.get("score_a")).isdigit() else existing["away_goals"]
                    cursor.execute("""
                        UPDATE matches SET home_goals = ?, away_goals = ?, date_time = ? WHERE id = ?
                    """, (h_cur, a_cur, fix.get("date") or existing["date_time"], match_id))
                    conn.commit()
                    print(f">> {home_id} vs {away_id}: [ACTUALITZAT EN DIRECTE ({h_cur}-{a_cur})] - Sense alterar Elo.")
                    skipped_count += 1
                    continue
                elif existing["status"] == "SCHEDULED" and is_live_fix:
                    h_cur = int(fix.get("score_h") or 0) if fix.get("score_h") is not None and str(fix.get("score_h")).isdigit() else 0
                    a_cur = int(fix.get("score_a") or 0) if fix.get("score_a") is not None and str(fix.get("score_a")).isdigit() else 0
                    cursor.execute("""
                        UPDATE matches SET status = 'LIVE', home_goals = ?, away_goals = ?, date_time = ? WHERE id = ?
                    """, (h_cur, a_cur, fix.get("date") or existing["date_time"], match_id))
                    conn.commit()
                    print(f">> {home_id} vs {away_id}: [TRANSICIÓ SCHEDULED -> LIVE ({h_cur}-{a_cur})] - Sense alterar Elo.")
                    skipped_count += 1
                    continue
                elif existing["status"] == "SCHEDULED" and not (fix.get("is_finished") or fix.get("score_h") is not None):
                    # Segueix programat, actualitzar data si cal
                    if fix.get("date") and fix.get("date") != existing["date_time"]:
                        cursor.execute("UPDATE matches SET date_time = ? WHERE id = ?", (fix.get("date"), match_id))
                        conn.commit()
                    skipped_count += 1
                    continue
            else:
                match_id = self.build_match_id(comp_id, season, match_code, home_id, away_id, jornada)

            print(f"\n>> Ingestant partit nou o pendent: {home} vs {away}")
            print(f"   URL d'Estadístiques: {url}")

            data = {}
            if url and "http" in url and (fix.get("score_h") is not None or fix.get("is_finished") or is_live_fix):
                data = scraper.scrape_url(url) or {}

            # Comprovar si el partit està suspès o ajornat
            is_suspended = (
                fix.get("is_suspended", False) or
                any(w in str(fix.get("suspension_reason") or "").lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon']) or
                any(w in str(data.get("suspension_reason") or "").lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
            )

            # Si el feed té el marcador
            h_goals = data.get("home_goals") if data.get("home_goals") is not None else fix.get("score_h")
            a_goals = data.get("away_goals") if data.get("away_goals") is not None else fix.get("score_a")

            # El partit NOMÉS és finalitzat si té resultat definitiu (no en joc/live ni suspès)
            is_definitive = (fix.get("is_finished", False) or data.get("is_finished", False) or (str(fix.get("ac_code", "")) == "3")) and not is_live_fix
            is_finished = (not is_suspended) and is_definitive and (h_goals is not None and str(h_goals).isdigit())
            ref_id = db.get_or_create_referee(data.get("referee"), competition_id=comp_id)

            if is_suspended:
                match_status = "SUSPENDED"
                h_goals_val = None
                a_goals_val = None
            elif is_finished:
                match_status = "FINISHED"
                h_goals_val = int(h_goals)
                a_goals_val = int(a_goals)
            elif is_live_fix:
                match_status = "LIVE"
                h_goals_val = int(h_goals) if h_goals is not None and str(h_goals).isdigit() else 0
                a_goals_val = int(a_goals) if a_goals is not None and str(a_goals).isdigit() else 0
            else:
                match_status = "SCHEDULED"
                h_goals_val = None
                a_goals_val = None

            match_record = {
                "id": match_id,
                "competition_id": comp_id,
                "season": "2026-2027",
                "jornada": jornada,
                "date_time": fix["date"] or data.get("date"),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "referee_id": ref_id,
                "home_goals": h_goals_val,
                "away_goals": a_goals_val,
                "home_xg": data.get("home_xg") if is_finished else None,
                "away_xg": data.get("away_xg") if is_finished else None,
                "home_corners": data.get("home_corners") if is_finished else None,
                "away_corners": data.get("away_corners") if is_finished else None,
                "home_yellow_cards": data.get("home_yellow_cards") if is_finished else None,
                "away_yellow_cards": data.get("away_yellow_cards") if is_finished else None,
                "home_red_cards": data.get("home_red_cards", 0) if is_finished else 0,
                "away_red_cards": data.get("away_red_cards", 0) if is_finished else 0,
                "status": match_status,
                "url": url or ""
            }

            db.save_match(match_record)
            ingested_count += 1

            if is_suspended:
                print(f"   ⏸️ [PARTIT SUSPÈS] {home} vs {away} - A l'espera del resultat (no s'inventen marcadors 0-0 ni es toca l'Elo).")
            elif is_live_fix:
                print(f"   🔴 [PARTIT EN DIRECTE] {home} {h_goals_val}-{a_goals_val} {away} - Partit en joc (sense alterar l'Elo).")
            elif is_finished:
                h_data = db.get_team_rating(home_id)
                a_data = db.get_team_rating(away_id)
                d_elo_h, d_elo_a = rank_engine.calculate_elo_change(
                    h_data["elo_rating"], a_data["elo_rating"],
                    int(h_goals), int(a_goals)
                )

                # --- Actualització de Rànquings Ofensiu/Defensiu (Tri-Rank) ---
                # xG del partit (fallback: gols reals si xG no disponible)
                match_xg_home = float(data.get("home_xg") or h_goals_val)
                match_xg_away = float(data.get("away_xg") or a_goals_val)
                # Referència: mitjana de gols de la lliga per al context d'avaluació ((home_avg + away_avg)/2)
                league_avg_goals = {
                    "LALIGA": 1.27,
                    "PREMIER": 1.40,
                    "HYPERMOTION": 1.20,
                    "CHAMPIONSHIP": 1.33,
                    "BUNDESLIGA": 1.50,
                    "LIGUE1": 1.30,
                    "SERIEA": 1.30
                }
                expected_goals_ref = league_avg_goals.get(comp_id, 1.27)

                h_gen, h_off, h_def = rank_engine.update_tri_ranks(
                    current_off_rank=float(h_data.get("off_rank", 14.0)),
                    current_def_rank=float(h_data.get("def_rank", 14.0)),
                    goals_scored=h_goals_val,
                    goals_conceded=a_goals_val,
                    xg_for=match_xg_home,
                    xg_against=match_xg_away,
                    expected_goals_model=expected_goals_ref
                )
                a_gen, a_off, a_def = rank_engine.update_tri_ranks(
                    current_off_rank=float(a_data.get("off_rank", 14.0)),
                    current_def_rank=float(a_data.get("def_rank", 14.0)),
                    goals_scored=a_goals_val,
                    goals_conceded=h_goals_val,
                    xg_for=match_xg_away,
                    xg_against=match_xg_home,
                    expected_goals_model=expected_goals_ref
                )

                cursor.execute(
                    "UPDATE team_ratings SET elo_rating = ?, general_rank = ?, off_rank = ?, def_rank = ?, rest_days = 0, updated_at = datetime('now') WHERE team_id = ?",
                    (h_data["elo_rating"] + d_elo_h, h_gen, h_off, h_def, home_id)
                )
                cursor.execute(
                    "UPDATE team_ratings SET elo_rating = ?, general_rank = ?, off_rank = ?, def_rank = ?, rest_days = 0, updated_at = datetime('now') WHERE team_id = ?",
                    (a_data["elo_rating"] + d_elo_a, a_gen, a_off, a_def, away_id)
                )
                conn.commit()

                ref_name = data.get("referee") or "Oficial CTA/PGMOL"
                newly_ingested_matches.append({
                    "match_id": match_id,
                    "competition_id": comp_id,
                    "jornada": jornada,
                    "home_team": home,
                    "away_team": away,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "home_goals": int(h_goals),
                    "away_goals": int(a_goals),
                    "score": f"{int(h_goals)} - {int(a_goals)}",
                    "home_xg": data.get("home_xg"),
                    "away_xg": data.get("away_xg"),
                    "home_yellow_cards": data.get("home_yellow_cards", 0),
                    "away_yellow_cards": data.get("away_yellow_cards", 0),
                    "home_red_cards": data.get("home_red_cards", 0),
                    "away_red_cards": data.get("away_red_cards", 0),
                    "home_corners": data.get("home_corners", 0),
                    "away_corners": data.get("away_corners", 0),
                    "referee": ref_name,
                    "elo_change_home": round(d_elo_h, 1),
                    "elo_change_away": round(d_elo_a, 1),
                    "new_elo_home": round(h_data["elo_rating"] + d_elo_h, 1),
                    "new_elo_away": round(a_data["elo_rating"] + d_elo_a, 1),
                    "date": fix["date"] or data.get("date")
                })

                print(f"   [NOU RESULTAT INGESTAT] {h_goals}-{a_goals} (xG: {data.get('home_xg')} - {data.get('away_xg')}) | Elo: {home_id} ({d_elo_h:+.1f}) / {away_id} ({d_elo_a:+.1f}) | Rank: {home_id} Off={h_off:.1f} Def={h_def:.1f} / {away_id} Off={a_off:.1f} Def={a_def:.1f}")
            else:
                print(f"   [PENDENT] Partit encara no jugat. Registrat a SQLite com a programat.")

        conn.close()
        print("\n" + "=" * 75)
        print(f"   [ÈXIT] Resum Jornada {jornada} ({comp_id}): {ingested_count} nous partits processats, {skipped_count} partits preservats sense tocar.")
        print("=" * 75 + "\n")
        return newly_ingested_matches

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scraper & Crawler Oficial Multi-Lliga")
    parser.add_argument("jornada", type=int, nargs="?", default=1, help="Número de jornada (ex: 1)")
    parser.add_argument("league", type=str, nargs="?", default="LALIGA", help="Competició (LALIGA, PREMIER, HYPERMOTION)")
    args = parser.parse_args()

    crawler = LiveCrawler(headless=True)
    crawler.run_live_pipeline(jornada=args.jornada, competition_id=args.league.upper())

"""
scraper/historical_loader.py
============================
Mòdul especialitzat per recuperar històric de partits de Flashscore
per a noves competicions (Bundesliga, Ligue 1, Serie A) de la temporada 2026-2027.

Recupera partits des de la Jornada 1 fins a l'actual:
- /resultados/ per a partits acabats amb marcador
- /partidos/ per a fixtures pendents/programats
- live feed per a partits actualment en directe
- MatchScraper per a xG, córners, targetes i àrbitre oficial
"""

import sys
import os
import re
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from database.db_manager import DatabaseManager
from scraper.live_crawler import LiveCrawler, FLASHSCORE_COMPETITIONS
from scraper.match_scraper import MatchScraper


def parse_feed_blocks(html_text: str, feed_key: str) -> List[Dict[str, Any]]:
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
            ts = fields.get('AD')
            dt_str = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M') if ts and ts.isdigit() else 'TBD'
            am_note = fields.get('AM', '')
            ac_code = str(fields.get('AC', '')).strip()
            is_susp = (
                ac_code in ['37', '4', '10', '11'] or
                any(w in am_note.lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
            )
            is_live = ac_code in ['2', '6', '7', '8', '12', '13']
            is_fin = (feed_key == 'results' or ac_code == '3') and not is_susp and not is_live

            items.append({
                'round_str': current_round or '',
                'match_code': fields.get('AA'),
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
                'ac_code': ac_code,
                'feed_key': feed_key
            })
    return items


def extract_live_matches_from_feed(pat: str) -> List[Dict[str, Any]]:
    """Consulta el feed en viu global per a partits en directe de la competició."""
    curl_bin = 'curl.exe' if os.name == 'nt' else 'curl'
    cmd_live = [curl_bin, '-s', '-H', 'x-fsign: SW9D1eZo', 'https://local-global.flashscore.ninja/2/x/feed/f_1_0_3_es_1']
    try:
        res = subprocess.run(cmd_live, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8)
        if not res.stdout:
            return []
        blocks = res.stdout.split('~')
        in_comp = False
        items = []
        for b in blocks:
            if 'ZA÷' in b:
                in_comp = bool(re.search(pat, b, re.IGNORECASE))
            if in_comp and 'AA÷' in b and 'AE÷' in b and 'AF÷' in b:
                fields = {}
                for token in re.split(r'[\xac\r\n\t]+', b):
                    if '÷' in token or '\xf7' in token:
                        parts = re.split(r'[\xf7÷]', token, maxsplit=1)
                        if len(parts) == 2:
                            fields[parts[0]] = parts[1]
                ts = fields.get('AD')
                dt_str = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M') if ts and ts.isdigit() else 'TBD'
                ac_code = str(fields.get('AC', '')).strip()
                is_susp = (ac_code in ['37', '4', '10', '11'])
                is_live = ac_code in ['2', '6', '7', '8', '12', '13']
                is_fin = (ac_code == '3') and not is_susp and not is_live
                items.append({
                    'match_code': fields.get('AA'),
                    'home': fields.get('AE'),
                    'away': fields.get('AF'),
                    'score_h': fields.get('AG') if (is_fin or is_live) else None,
                    'score_a': fields.get('AH') if (is_fin or is_live) else None,
                    'date': dt_str,
                    'is_live': is_live,
                    'is_finished': is_fin,
                    'is_suspended': is_susp,
                    'ac_code': ac_code,
                    'round_str': ''
                })
        return items
    except Exception as e:
        print(f"   [!] Error consultant live feed: {e}")
        return []


def parse_jornada_number(round_str: str) -> int:
    m_num = re.search(r'(?:Jornada|Round|Matchday|Spieltag|Journ[eé]e|Giornata)\s*(\d+)|(\d+)\.?\s*(?:Spieltag|Journ[eé]e|Giornata)', round_str or '', re.IGNORECASE)
    if m_num:
        return int(m_num.group(1) or m_num.group(2))
    return 1


class HistoricalLoader:
    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self.ms = MatchScraper(headless=True)
        self.curl_bin = 'curl.exe' if os.name == 'nt' else 'curl'

    def load_competition_historical(self, competition_id: str, fetch_detailed_stats: bool = True) -> Dict[str, Any]:
        """
        Recupera tots els partits de la temporada de la competició i els desa a SQLite.
        """
        comp_id = competition_id.upper()
        base_url = FLASHSCORE_COMPETITIONS.get(comp_id)
        if not base_url:
            raise ValueError(f"Competició no suportada: {competition_id}")

        print(f"\n{'='*70}")
        print(f"   📥 RECUPERANT DADES HISTÒRIQUES: {comp_id}")
        print(f"{'='*70}")

        # 1. Descarregar /resultados/ (partits finalitzats de la temporada)
        print(f"[*] Descarregant /resultados/ per a {comp_id}...")
        cmd_res = [self.curl_bin, '-s', f'{base_url}/resultados/']
        res_res = subprocess.run(cmd_res, capture_output=True, text=True, encoding='utf-8', errors='replace')
        results_items = parse_feed_blocks(res_res.stdout, 'results')
        print(f"   • Trobats {len(results_items)} partits jugats a /resultados/.")

        # 2. Descarregar /partidos/ (fixtures de la temporada)
        print(f"[*] Descarregant /partidos/ per a {comp_id}...")
        cmd_fix = [self.curl_bin, '-s', f'{base_url}/partidos/']
        res_fix = subprocess.run(cmd_fix, capture_output=True, text=True, encoding='utf-8', errors='replace')
        fixtures_items = parse_feed_blocks(res_fix.stdout, 'fixtures')
        print(f"   • Trobats {len(fixtures_items)} partits programats a /partidos/.")

        # 3. Comprovar feed en viu per a partits que s'estiguin jugant avui
        comp_patterns = {
            "BUNDESLIGA": r'GERMANY:\s*Bundesliga(?!\s*Women)',
            "LIGUE1": r'FRANCE:\s*Ligue 1(?!\s*Women)',
            "SERIEA": r'ITALY:\s*Serie A(?!\s*Women)',
        }
        live_items = extract_live_matches_from_feed(comp_patterns.get(comp_id, ''))
        live_dict = {m['match_code']: m for m in live_items}
        if live_items:
            print(f"   • Detectats {len(live_items)} partits al live feed actual.")

        # Combinar tots els partits evitant duplicats per match_code
        all_raw_matches = {}
        for m in fixtures_items:
            all_raw_matches[m['match_code']] = m
        for m in results_items:
            all_raw_matches[m['match_code']] = m  # resultats tenen prioritat sobre fixtures
        for code, lm in live_dict.items():
            if code in all_raw_matches:
                all_raw_matches[code]['is_live'] = lm['is_live']
                all_raw_matches[code]['is_finished'] = lm['is_finished']
                all_raw_matches[code]['is_suspended'] = lm['is_suspended']
                if lm.get('score_h') is not None:
                    all_raw_matches[code]['score_h'] = lm['score_h']
                    all_raw_matches[code]['score_a'] = lm['score_a']
            else:
                all_raw_matches[code] = lm

        # Carregar partits existents a SQLite per reutilitzar estadístiques ja extretes
        with self.db.get_connection() as conn:
            c = conn.cursor()
            existing_rows = c.execute(
                "SELECT * FROM matches WHERE competition_id = ?", (comp_id,)
            ).fetchall()
            existing_by_code = {}
            for r in existing_rows:
                rd = dict(r)
                m_code_match = re.search(r"2026-2027_([a-zA-Z0-9]+)", rd["id"])
                if m_code_match:
                    existing_by_code[m_code_match.group(1)] = rd
                # També per parella d'equips
                existing_by_code[f"{rd['home_team_id']}_{rd['away_team_id']}"] = rd

        # Processar i desar cada partit
        saved_count = 0
        finished_count = 0
        live_count = 0
        scheduled_count = 0
        suspended_count = 0
        jornades_seen = set()

        # Filtrar partits que tinguin equips identificables
        valid_raw = []
        for m in all_raw_matches.values():
            home_name = m.get('home')
            away_name = m.get('away')
            home_id = self.db.find_team_id(home_name)
            away_id = self.db.find_team_id(away_name)
            if not home_id or not away_id:
                continue
            jornada = parse_jornada_number(m.get('round_str'))
            is_fin = m.get('is_finished', False) and not m.get('is_live', False) and not m.get('is_suspended', False)
            is_liv = m.get('is_live', False)
            # Ometre fixtures de jornades futures llunyanes (més enllà de la jornada activa J7)
            if not is_fin and not is_liv and jornada > 7:
                continue
            m['_home_id'] = home_id
            m['_away_id'] = away_id
            m['_jornada'] = jornada
            valid_raw.append(m)

        valid_raw.sort(key=lambda x: (x['_jornada'], x.get('date', '')))

        # Pre-extreure estadístiques detallades en paral·lel per als partits finalitzats que ho requereixin
        codes_to_fetch = []
        for m in valid_raw:
            m_code = m['match_code']
            is_fin = m.get('is_finished', False) and not m.get('is_live', False) and not m.get('is_suspended', False)
            ex_db = existing_by_code.get(m_code) or existing_by_code.get(f"{m['_home_id']}_{m['_away_id']}")
            has_db_stats = (
                ex_db
                and ex_db.get("status") == "FINISHED"
                and ex_db.get("home_xg") is not None
                and ex_db.get("home_corners") is not None
            )
            if is_fin and fetch_detailed_stats and not has_db_stats:
                codes_to_fetch.append(m_code)

        fetched_details = {}
        if codes_to_fetch:
            print(f"[*] Descarregant en paral·lel estadístiques oficials (xG, córners, targetes) de {len(codes_to_fetch)} partits...")
            def _fetch_one(code):
                try:
                    return code, self.ms.scrape_match_feed(code) or {}
                except Exception as e_f:
                    print(f"   [!] Error feed {code}: {e_f}")
                    return code, {}

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as executor:
                for code, d in executor.map(_fetch_one, codes_to_fetch):
                    fetched_details[code] = d
            print(f"   • Descàrrega d'estadístiques completada ({len(fetched_details)} partits).")

        t_start = time.time()
        for idx, m in enumerate(valid_raw, 1):
            home_name = m['home']
            away_name = m['away']
            home_id = m['_home_id']
            away_id = m['_away_id']
            jornada = m['_jornada']
            if jornada > 0:
                jornades_seen.add(jornada)

            match_code = m['match_code']
            season = "2026-2027"
            match_id = LiveCrawler.build_match_id(comp_id, season, match_code, home_id, away_id, jornada)

            is_suspended = m.get('is_suspended', False)
            is_live = m.get('is_live', False)
            is_finished = m.get('is_finished', False) and not is_live and not is_suspended

            score_h = m.get('score_h')
            score_a = m.get('score_a')
            if is_finished and (score_h is None or not str(score_h).isdigit()):
                is_finished = False

            # Obtenir estadístiques detallades
            ex_db = existing_by_code.get(match_code) or existing_by_code.get(f"{home_id}_{away_id}")
            has_db_stats = (
                ex_db
                and ex_db.get("status") == "FINISHED"
                and ex_db.get("home_xg") is not None
                and ex_db.get("home_corners") is not None
            )

            detail_data = {}
            if has_db_stats:
                detail_data = ex_db
            elif is_finished:
                detail_data = fetched_details.get(match_code) or {}

            # Refereee
            ref_name = detail_data.get("referee") or (ex_db.get("referee_id") if ex_db else None)
            ref_id = self.db.get_or_create_referee(ref_name, competition_id=comp_id)

            if is_suspended:
                status = "SUSPENDED"
                h_goals = None
                a_goals = None
                suspended_count += 1
            elif is_finished:
                status = "FINISHED"
                h_goals = int(score_h) if score_h is not None and str(score_h).isdigit() else detail_data.get("home_goals")
                a_goals = int(score_a) if score_a is not None and str(score_a).isdigit() else detail_data.get("away_goals")
                finished_count += 1
            elif is_live:
                status = "LIVE"
                h_goals = int(score_h) if score_h is not None and str(score_h).isdigit() else (ex_db.get("home_goals", 0) if ex_db else 0)
                a_goals = int(score_a) if score_a is not None and str(score_a).isdigit() else (ex_db.get("away_goals", 0) if ex_db else 0)
                live_count += 1
            else:
                status = "SCHEDULED"
                h_goals = None
                a_goals = None
                scheduled_count += 1

            # Camps de detall
            h_xg = detail_data.get("home_xg") if is_finished else None
            a_xg = detail_data.get("away_xg") if is_finished else None
            h_corners = detail_data.get("home_corners") if is_finished else None
            a_corners = detail_data.get("away_corners") if is_finished else None
            h_yc = detail_data.get("home_yellow_cards") if is_finished else None
            a_yc = detail_data.get("away_yellow_cards") if is_finished else None
            h_rc = detail_data.get("home_red_cards", 0) if is_finished else 0
            a_rc = detail_data.get("away_red_cards", 0) if is_finished else 0
            h_fouls = detail_data.get("home_fouls") if is_finished else None
            a_fouls = detail_data.get("away_fouls") if is_finished else None

            match_record = {
                "id": match_id,
                "competition_id": comp_id,
                "season": season,
                "jornada": jornada,
                "date_time": m.get("date") or detail_data.get("date") or "TBD",
                "home_team_id": home_id,
                "away_team_id": away_id,
                "referee_id": ref_id,
                "home_goals": h_goals,
                "away_goals": a_goals,
                "home_xg": h_xg,
                "away_xg": a_xg,
                "home_corners": h_corners,
                "away_corners": a_corners,
                "home_yellow_cards": h_yc,
                "away_yellow_cards": a_yc,
                "home_red_cards": h_rc,
                "away_red_cards": a_rc,
                "home_fouls": h_fouls,
                "away_fouls": a_fouls,
                "status": status,
                "url": f"https://www.flashscore.es/partido/{match_code}/#/estadisticas-del-partido/0"
            }

            self.db.save_match(match_record)
            saved_count += 1

            if idx % 15 == 0 or idx == len(valid_raw):
                elapsed = time.time() - t_start
                print(f"   [{idx}/{len(valid_raw)}] Processats {saved_count} partits ({finished_count} finalitzats, {live_count} en directe, {scheduled_count} programats)... ({elapsed:.1f}s)")

        summary = {
            "competition_id": comp_id,
            "total_saved": saved_count,
            "finished": finished_count,
            "live": live_count,
            "scheduled": scheduled_count,
            "suspended": suspended_count,
            "jornades_count": len(jornades_seen),
            "jornades": sorted(list(jornades_seen))
        }

        print(f"\n[+] {comp_id} COMPLETAT:")
        print(f"    • Jornades detectades: {summary['jornades_count']} ({min(jornades_seen) if jornades_seen else 0} a {max(jornades_seen) if jornades_seen else 0})")
        print(f"    • Partits desats: {saved_count} totals ({finished_count} finalitzats, {live_count} en joc, {scheduled_count} programats, {suspended_count} suspesos)")

        return summary


def run_historical_fill(competitions: List[str] = None):
    comps = competitions or ["BUNDESLIGA", "LIGUE1", "SERIEA"]
    loader = HistoricalLoader()
    summaries = {}
    for c in comps:
        summaries[c] = loader.load_competition_historical(c)
    return summaries


if __name__ == "__main__":
    run_historical_fill()

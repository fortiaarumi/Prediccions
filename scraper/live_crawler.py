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
}

class LiveCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless

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
                    ts = fields.get('AD')
                    dt_str = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M') if ts and ts.isdigit() else 'TBD'
                    items.append({
                        'round_str': current_round or '',
                        'match_code': fields.get('AA'),
                        'home': fields.get('AE'),
                        'away': fields.get('AF'),
                        'score_h': fields.get('AG'),
                        'score_a': fields.get('AH'),
                        'date': dt_str,
                        'url': f"https://www.flashscore.es/partido/{fields.get('AA')}/#/estadisticas-del-partido/0"
                    })
            return items

        # 1. Obtenir resultats jugats
        cmd_res = ['curl', '-s', f'{base_url}/resultados/']
        res_res = subprocess.run(cmd_res, capture_output=True, text=True, encoding='utf-8', errors='replace')
        all_matches = parse_feed_blocks(res_res.stdout, 'results')

        # 2. Obtenir fixtures pendents
        cmd_fix = ['curl', '-s', f'{base_url}/partidos/']
        res_fix = subprocess.run(cmd_fix, capture_output=True, text=True, encoding='utf-8', errors='replace')
        all_matches.extend(parse_feed_blocks(res_fix.stdout, 'fixtures'))

        # Filtrar exactament per Jornada/Round N
        for m in all_matches:
            r_str = m.get('round_str', '')
            match_round_num = None
            m_num = re.search(r'(?:Jornada|Round|Matchday)\s*(\d+)', r_str, re.IGNORECASE)
            if m_num:
                match_round_num = int(m_num.group(1))
            
            if match_round_num == jornada:
                # Evitar duplicats
                if not any(f["home"] == m["home"] and f["away"] == m["away"] for f in fixtures):
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
        rank_engine = RankEngine(league_teams_count=22 if comp_id == "HYPERMOTION" else 20)
        scraper = MatchScraper(headless=self.headless)

        print(f"\n[*] Processant els {len(fixtures)} partits de la Jornada {jornada} ({comp_id})...")

        conn = db.get_connection()
        cursor = conn.cursor()

        ingested_count = 0
        skipped_count = 0

        for fix in fixtures:
            home = fix["home"]
            away = fix["away"]
            url = fix["url"]

            home_id = db.find_team_id(home) or db.get_or_create_team(home)
            away_id = db.find_team_id(away) or db.get_or_create_team(away)
            match_id = f"{comp_id}_J{jornada}_{home_id}_{away_id}"

            # 1. Comprovar si el partit ja estava guardat i finalitzat a la base de dades
            cursor.execute("SELECT status, home_goals, away_goals FROM matches WHERE id = ?", (match_id,))
            existing = cursor.fetchone()

            if existing and existing["status"] == "FINISHED" and existing["home_goals"] is not None:
                print(f">> {home_id} vs {away_id}: [JA INGESTAT PRÈVIAMENT ({existing['home_goals']}-{existing['away_goals']})] - Saltant per no duplicar Elo.")
                skipped_count += 1
                continue

            print(f"\n>> Ingestant partit nou o pendent: {home} vs {away}")
            print(f"   URL d'Estadístiques: {url}")

            if url and "http" in url:
                data = scraper.scrape_url(url)
                if not data:
                    data = {}

                # Si el feed té el marcador
                h_goals = data.get("home_goals") if data.get("home_goals") is not None else fix.get("score_h")
                a_goals = data.get("away_goals") if data.get("away_goals") is not None else fix.get("score_a")

                is_finished = h_goals is not None and str(h_goals).isdigit()
                ref_id = db.get_or_create_referee(data.get("referee"), competition_id=comp_id)

                match_record = {
                    "id": match_id,
                    "competition_id": comp_id,
                    "season": "2026-2027",
                    "jornada": jornada,
                    "date_time": fix["date"] or data.get("date"),
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "referee_id": ref_id,
                    "home_goals": int(h_goals) if is_finished else None,
                    "away_goals": int(a_goals) if is_finished else None,
                    "home_xg": data.get("home_xg"),
                    "away_xg": data.get("away_xg"),
                    "home_corners": data.get("home_corners"),
                    "away_corners": data.get("away_corners"),
                    "home_yellow_cards": data.get("home_yellow_cards"),
                    "away_yellow_cards": data.get("away_yellow_cards"),
                    "home_red_cards": data.get("home_red_cards", 0),
                    "away_red_cards": data.get("away_red_cards", 0),
                    "status": "FINISHED" if is_finished else "SCHEDULED",
                    "url": url
                }

                db.save_match(match_record)
                ingested_count += 1

                if is_finished:
                    h_data = db.get_team_rating(home_id)
                    a_data = db.get_team_rating(away_id)
                    d_elo_h, d_elo_a = rank_engine.calculate_elo_change(
                        h_data["elo_rating"], a_data["elo_rating"],
                        int(h_goals), int(a_goals)
                    )
                    cursor.execute("UPDATE team_ratings SET elo_rating = ?, rest_days = 0, updated_at = datetime('now') WHERE team_id = ?", (h_data["elo_rating"] + d_elo_h, home_id))
                    cursor.execute("UPDATE team_ratings SET elo_rating = ?, rest_days = 0, updated_at = datetime('now') WHERE team_id = ?", (a_data["elo_rating"] + d_elo_a, away_id))
                    conn.commit()

                    print(f"   [NOU RESULTAT INGESTAT] {h_goals}-{a_goals} (xG: {data.get('home_xg')} - {data.get('away_xg')}) | Elo: {home_id} ({d_elo_h:+.1f}) / {away_id} ({d_elo_a:+.1f})")
                else:
                    print(f"   [PENDENT] Partit encara no jugat. Registrat a SQLite com a programat.")

        conn.close()
        print("\n" + "=" * 75)
        print(f"   [ÈXIT] Resum Jornada {jornada} ({comp_id}): {ingested_count} nous partits processats, {skipped_count} partits preservats sense tocar.")
        print("=" * 75 + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scraper & Crawler Oficial Multi-Lliga")
    parser.add_argument("jornada", type=int, nargs="?", default=1, help="Número de jornada (ex: 1)")
    parser.add_argument("league", type=str, nargs="?", default="LALIGA", help="Competició (LALIGA, PREMIER, HYPERMOTION)")
    args = parser.parse_args()

    crawler = LiveCrawler(headless=True)
    crawler.run_live_pipeline(jornada=args.jornada, competition_id=args.league.upper())

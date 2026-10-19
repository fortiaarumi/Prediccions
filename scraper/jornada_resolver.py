"""
scraper/jornada_resolver.py
===========================
Calendari Oficial per a LaLiga EA Sports, Premier League i LaLiga Hypermotion.
Resolució dinàmica i robusta dels partits de qualsevol jornada.
"""

from pathlib import Path
from typing import List, Dict, Any
import re

DATA_DIR = Path(__file__).parent.parent / "data"

class JornadaResolver:
    def __init__(self, season: str = "2026-2027", competition_id: str = "LALIGA"):
        known_comps = {"LALIGA", "PREMIER", "HYPERMOTION", "CHAMPIONSHIP", "BUNDESLIGA", "LIGUE1", "SERIEA"}
        if str(season).upper() in known_comps:
            self.competition_id = str(season).upper()
            self.season = competition_id if competition_id not in known_comps else "2026-2027"
        else:
            self.season = season
            self.competition_id = competition_id.upper()

    def get_jornada_fixtures(self, jornada: int) -> List[Dict[str, Any]]:
        """
        Retorna els partits oficials de la jornada per a la competició triada.
        Obté els enllaços i enfrontaments reals dinàmicament d'internet.
        """
        from scraper.live_crawler import LiveCrawler
        crawler = LiveCrawler(headless=True)
        fixtures = crawler.crawl_jornada_fixtures_from_web(jornada, competition_id=self.competition_id)

        if not fixtures:
            raise ValueError(f"[ERROR CRÍTIC]: No s'han pogut resoldre partits per a la Jornada {jornada} de {self.competition_id}.")

        # Formatar els diccionaris per compatibilitat amb el predictor
        resolved = []
        for fix in fixtures:
            is_susp = (
                fix.get("is_suspended", False) or
                any(w in str(fix.get("suspension_reason") or "").lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
            )
            is_live = fix.get("is_live", False)
            is_fin = fix.get("is_finished", False) and not is_live and not is_susp
            sc_h = fix.get("score_h")
            sc_a = fix.get("score_a")
            if is_susp:
                m_status = "SUSPENDED"
                h_goals = None
                a_goals = None
            elif is_live:
                m_status = "LIVE"
                h_goals = int(sc_h) if sc_h is not None and str(sc_h).isdigit() else 0
                a_goals = int(sc_a) if sc_a is not None and str(sc_a).isdigit() else 0
            elif is_fin and sc_h is not None and str(sc_h).isdigit():
                m_status = "FINISHED"
                h_goals = int(sc_h)
                a_goals = int(sc_a)
            else:
                m_status = "SCHEDULED"
                h_goals = None
                a_goals = None

            r_str = fix.get("round_str", "")
            r_m = re.search(r'(\d+)', str(r_str))
            r_num = int(r_m.group(1)) if r_m else jornada

            resolved.append({
                "competition_id": self.competition_id,
                "jornada": r_num,
                "round_str": r_str,
                "home_team": fix["home"],
                "away_team": fix["away"],
                "home": fix["home"],
                "away": fix["away"],
                "date": fix["date"],
                "url": fix["url"],
                "match_code": fix.get("match_code"),
                "score_h": h_goals,
                "score_a": a_goals,
                "is_suspended": is_susp,
                "suspension_reason": fix.get("suspension_reason", ""),
                "status": m_status,
                "is_live": is_live,
                "is_finished": is_fin,
                "referee": fix.get("referee", "REF_DEFAULT")
            })

        return resolved

    def detect_current_jornada(self, competition_id: str = None) -> int:
        """
        Detecta automàticament quina és la jornada que toca jugar ara mateix (o en els propers 7 dies)
        per a la competició sol·licitada, analitzant en directe els fixtures de Flashscore.
        Evita predir jornades llunyanes i s'adapta tant a caps de setmana com a intersetmanals.
        """
        import os
        import subprocess
        import re

        if isinstance(self, type):
            comp_id = (competition_id or "LALIGA").upper()
        else:
            comp_id = (competition_id or getattr(self, "competition_id", "LALIGA")).upper()

        try:
            from daily_runner import get_upcoming_round_info
            u_info = get_upcoming_round_info(comp_id)
            if u_info and u_info.get("jornada"):
                detected = int(u_info["jornada"])
                print(f"[*] Detecció automàtica de calendari per a {comp_id}: Jornada activa = {detected}")
                return detected
        except Exception:
            pass

        urls = {
            "LALIGA": "https://www.flashscore.es/futbol/espana/laliga-ea-sports/partidos/",
            "PREMIER": "https://www.flashscore.es/futbol/inglaterra/premier-league/partidos/",
            "HYPERMOTION": "https://www.flashscore.es/futbol/espana/laliga-hypermotion/partidos/",
            "CHAMPIONSHIP": "https://www.flashscore.es/futbol/inglaterra/championship/partidos/",
            "BUNDESLIGA": "https://www.flashscore.es/futbol/alemania/bundesliga/partidos/",
            "LIGUE1": "https://www.flashscore.es/futbol/francia/ligue-1/partidos/",
            "SERIEA": "https://www.flashscore.es/futbol/italia/serie-a/partidos/",
        }
        target_url = urls.get(comp_id, urls["LALIGA"])

        curl_bin = 'curl.exe' if os.name == 'nt' else 'curl'
        cmd = [curl_bin, '-s', target_url]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

        blocks = res.stdout.split('~')
        current_round = None
        for b in blocks:
            if 'ER÷' in b:
                m_round = re.search(r'ER÷([^¬~]+)', b)
                if m_round:
                    current_round = m_round.group(1)
            if 'AA÷' in b and 'AD÷' in b:
                if current_round:
                    m_num = re.search(r'(?:Jornada|Round|Matchday|Spieltag|Journ[eé]e|Giornata)\s*(\d+)|(\d+)\.?\s*(?:Spieltag|Journ[eé]e|Giornata)', current_round, re.IGNORECASE)
                    if m_num:
                        detected = int(m_num.group(1) or m_num.group(2))
                        print(f"[*] Detecció automàtica de calendari per a {comp_id}: Jornada activa = {detected}")
                        return detected

        # Fallback si no hi ha partits pendents (ex: final de temporada)
        return 7

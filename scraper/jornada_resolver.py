"""
scraper/jornada_resolver.py
===========================
Calendari Oficial per a LaLiga EA Sports, Premier League i LaLiga Hypermotion.
Resolució dinàmica i robusta dels partits de qualsevol jornada.
"""

from pathlib import Path
from typing import List, Dict, Any

DATA_DIR = Path(__file__).parent.parent / "data"

class JornadaResolver:
    def __init__(self, season: str = "2026-2027", competition_id: str = "LALIGA"):
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
            sc_h = None if is_susp else fix.get("score_h")
            sc_a = None if is_susp else fix.get("score_a")
            m_status = "SUSPENDED" if is_susp else ("FINISHED" if sc_h is not None and str(sc_h).isdigit() else "SCHEDULED")

            resolved.append({
                "competition_id": self.competition_id,
                "home_team": fix["home"],
                "away_team": fix["away"],
                "home": fix["home"],
                "away": fix["away"],
                "date": fix["date"],
                "url": fix["url"],
                "match_code": fix.get("match_code"),
                "score_h": sc_h,
                "score_a": sc_a,
                "is_suspended": is_susp,
                "suspension_reason": fix.get("suspension_reason", ""),
                "status": m_status,
                "referee": fix.get("referee", "REF_DEFAULT")
            })

        return resolved

    @classmethod
    def detect_current_jornada(cls, competition_id: str = "LALIGA") -> int:
        """
        Detecta automàticament quina és la jornada que toca jugar ara mateix (o en els propers 7 dies)
        per a la competició sol·licitada, analitzant en directe els fixtures de Flashscore.
        Evita predir jornades llunyanes i s'adapta tant a caps de setmana com a intersetmanals.
        """
        import os
        import subprocess
        import re

        comp_id = competition_id.upper()
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

"""
scraper/match_scraper.py
========================
Scraper robust de partits de Flashscore/Sofascore.
Suporta extracció ultraràpida directa per feed HTTP i fallback per Selenium.
Extreu: Marcador FT, Gols Esperats (xG), Córners, Targetes Grogues, Targetes Vermelles i Àrbitre.
"""

import json
import os
import re
import time
import subprocess
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup

class MatchScraper:
    def __init__(self, headless: bool = True):
        self.headless = headless

    def scrape_url(self, url: str) -> Dict[str, Any]:
        """
        Extreu totes les estadístiques detallades d'una URL de partit.
        Intenta primer el mètode directe per feed HTTP (molt més ràpid i resilient)
        i fa fallback a Selenium si fos necessari.
        """
        # Extreure match_code si està a la URL (ex: https://www.flashscore.es/partido/GvI6ON5c/...)
        m_code_match = re.search(r"/partido/([a-zA-Z0-9]+)", url)
        match_code = m_code_match.group(1) if m_code_match else None

        if match_code:
            data = self.scrape_match_feed(match_code, url)
            if data and data.get("home_goals") is not None:
                return data

        # Fallback a Selenium si el mètode directe no ha retornat dades
        return self._scrape_selenium(url)

    def scrape_match_feed(self, match_code: str, url: str = None) -> Optional[Dict[str, Any]]:
        """Extreu estadístiques directament de l'API de feeds de Flashscore."""
        stats_url = url or f"https://www.flashscore.es/partido/{match_code}/#/estadisticas-del-partido/0"
        result = {
            "url": stats_url,
            "match_code": match_code,
            "home_team": "Desconegut",
            "away_team": "Desconegut",
            "date": "Desconeguda",
            "home_goals": None,
            "away_goals": None,
            "home_xg": None,
            "away_xg": None,
            "home_corners": None,
            "away_corners": None,
            "home_yellow_cards": None,
            "away_yellow_cards": None,
            "home_red_cards": 0,
            "away_red_cards": 0,
            "home_fouls": None,
            "away_fouls": None,
            "referee": "REF_DEFAULT",
            "raw_stats": {}
        }

        try:
            # 1. Extreure estadístiques detallades
            cmd = ['curl', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/df_st_1_{match_code}']
            res_st = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            
            def parse_num(val):
                try:
                    return float(val.replace(",", ".").replace("%", "").strip())
                except Exception:
                    return 0.0

            stats_dict = {}
            if res_st.stdout:
                blocks = res_st.stdout.split('~')
                for b in blocks:
                    if 'SG÷' in b:
                        m_cat = re.search(r'SG÷([^¬]+)', b)
                        m_h = re.search(r'SH÷([^¬]+)', b)
                        m_a = re.search(r'SI÷([^¬]+)', b)
                        if m_cat and m_h and m_a:
                            cat = m_cat.group(1).strip()
                            v_h = m_h.group(1).strip()
                            v_a = m_a.group(1).strip()
                            if cat not in stats_dict:
                                stats_dict[cat] = {"local": v_h, "visitante": v_a}

            result["raw_stats"] = stats_dict

            for cat, vals in stats_dict.items():
                cat_lower = cat.lower()
                if "xg" in cat_lower or "expected goals" in cat_lower or "goles esperados" in cat_lower:
                    if result["home_xg"] is None:
                        result["home_xg"] = parse_num(vals["local"])
                        result["away_xg"] = parse_num(vals["visitante"])
                elif "corner" in cat_lower or "córner" in cat_lower:
                    if result["home_corners"] is None:
                        result["home_corners"] = int(parse_num(vals["local"]))
                        result["away_corners"] = int(parse_num(vals["visitante"]))
                elif "yellow" in cat_lower or "amarilla" in cat_lower:
                    if result["home_yellow_cards"] is None:
                        result["home_yellow_cards"] = int(parse_num(vals["local"]))
                        result["away_yellow_cards"] = int(parse_num(vals["visitante"]))
                elif "red" in cat_lower or "roja" in cat_lower:
                    if result["home_red_cards"] == 0 and result["away_red_cards"] == 0:
                        result["home_red_cards"] = int(parse_num(vals["local"]))
                        result["away_red_cards"] = int(parse_num(vals["visitante"]))
                elif "foul" in cat_lower or "falta" in cat_lower:
                    if result["home_fouls"] is None:
                        result["home_fouls"] = int(parse_num(vals["local"]))
                        result["away_fouls"] = int(parse_num(vals["visitante"]))

            # 2. Extreure marcador i equips des del feed bàsic o del resum
            cmd_dc = ['curl', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/dc_1_{match_code}']
            res_dc = subprocess.run(cmd_dc, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res_dc.stdout:
                fields = {}
                for token in re.split(r'[\xac\r\n\t]+', res_dc.stdout):
                    if '÷' in token:
                        parts = token.split('÷', 1)
                        fields[parts[0]] = parts[1]
                if 'DE' in fields and 'DF' in fields:
                    try:
                        result["home_goals"] = int(fields['DE'])
                        result["away_goals"] = int(fields['DF'])
                    except Exception:
                        pass

            # 3. Extreure àrbitre des del feed oficial d'informació (df_sui_1)
            cmd_sui = ['curl', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/df_sui_1_{match_code}']
            res_sui = subprocess.run(cmd_sui, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res_sui.stdout:
                m_ref = re.search(r'REF[^\w]*MIV[^\w]*([A-Za-z\s\.\-]+)', res_sui.stdout)
                if m_ref:
                    result["referee"] = m_ref.group(1).strip()

            # Fallback secundari: comentaris en viu (df_li_1) si no s'ha trobat
            if result["referee"] == "REF_DEFAULT":
                cmd_li = ['curl', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/df_li_1_{match_code}']
                res_li = subprocess.run(cmd_li, capture_output=True, text=True, encoding='utf-8', errors='replace')
                if res_li.stdout:
                    for b in res_li.stdout.split('~'):
                        if 'Referee' in b or 'Árbitro' in b:
                            m_ref2 = re.search(r'([A-Za-z\s\.-]+(?:Referee|Árbitro))', b)
                            if m_ref2:
                                result["referee"] = m_ref2.group(1).replace("Referee", "").replace("Árbitro", "").strip()

            return result

        except Exception as e:
            print(f"[!] Error en extracció de feed: {e}")
            return None

    def _scrape_selenium(self, url: str) -> Dict[str, Any]:
        """Fallback clàssic per Selenium si cal."""
        base_match_url = url.split("#")[0].rstrip("/")
        stats_url = f"{base_match_url}/#/estadisticas-del-partido/0"

        result = {
            "url": stats_url,
            "home_team": "Desconegut",
            "away_team": "Desconegut",
            "date": "Desconeguda",
            "home_goals": None,
            "away_goals": None,
            "home_xg": None,
            "away_xg": None,
            "home_corners": None,
            "away_corners": None,
            "home_yellow_cards": None,
            "away_yellow_cards": None,
            "home_red_cards": 0,
            "away_red_cards": 0,
            "referee": "REF_DEFAULT",
            "raw_stats": {}
        }

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            options = Options()
            if self.headless:
                options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("user-agent=Mozilla/5.0")
            driver = webdriver.Chrome(options=options)
            driver.get(stats_url)
            time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            driver.quit()

            marcador = soup.find(class_=lambda x: x and ("detailScore__wrapper" in str(x) or "fixedScore" in str(x)))
            if marcador:
                txt_marcador = marcador.get_text(separator="-", strip=True)
                gols = [int(n) for n in re.findall(r"\d+", txt_marcador)]
                if len(gols) >= 2:
                    result["home_goals"] = gols[0]
                    result["away_goals"] = gols[1]
        except Exception as e:
            print(f"[!] Avís Selenium: {e}")

        return result

"""
scraper/winamax_scraper.py
==========================
Scraper oficial en directe de Winamax Espanya (100% fidel a la web real).

Extracció ultraràpida i matemàticament exacta dels mercats del partit complet:
- 1X2 i Doble Oportunitat
- Número total de gols (Slider principal: Més/Menys de 2.5 gols)
- Ambdós equips marquen (Sí / No)
- Número total de targetes (Més/Menys de 4.5 i 5.5 targetes, Expulsió Sí)
- Número total de córners (Més/Menys de 8.5, 9.5 i 10.5 córners)
"""

import sys
import os
import re
import time
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

CACHE_DIR = Path(__file__).parent.parent / "data"

WINAMAX_TOURNAMENTS = {
    "LALIGA": "32",
    "PREMIER": "1",
    "HYPERMOTION": "32",
    "CHAMPIONSHIP": "1",
    "BUNDESLIGA": "30",
    "LIGUE1": "7",
    "SERIEA": "31",
}

WINAMAX_TOURNAMENT_IDS = {
    "LALIGA": 36,
    "PREMIER": 1,
    "HYPERMOTION": 37,
    "CHAMPIONSHIP": 2,
    "BUNDESLIGA": 42,
    "LIGUE1": 4,
    "SERIEA": 33,
}

class WinamaxScraper:
    def __init__(self, headless: bool = True, db: Optional[Any] = None):
        self.headless = headless
        if db is not None:
            self.db = db
        else:
            try:
                from database.db_manager import DatabaseManager
                self.db = DatabaseManager()
            except Exception:
                self.db = None
        self._tournament_cache: Dict[str, Dict[str, Any]] = {}
        self._match_cache: Dict[str, Dict[str, Any]] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_match_odds(self, home_team: str, away_team: str, competition_id: str = "LALIGA") -> Dict[str, Any]:
        """
        Descobreix dinàmicament l'ID del partit a Winamax i n'extreu exactament les cuotes reals.
        """
        comp_id = competition_id.upper()
        tid = str(WINAMAX_TOURNAMENTS.get(comp_id, "32"))
        base_tournament_url = f"https://www.winamax.es/apuestas-deportivas/sports/1/{tid}"
        print(f"\n[*] Cercant dinàmicament a Winamax ({comp_id} | Categoria {tid}): {home_team} vs {away_team}...")

        odds_data = {
            "source": f"Winamax Espanya ({comp_id})",
            "url": base_tournament_url,
            "match_id": None,
            "matched_title": None,
            "matched": False,
            "1X2": {},
            "double_chance": {},
            "goals": {},
            "over_under_2_5": {},
            "team_goals": {},
            "btts": {},
            "cards": {},
            "corners": {},
            "combos": {}
        }

        def normalize(name: Any) -> str:
            if not name or not isinstance(name, str):
                return ""
            s = name.lower()
            for c, rep in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ç", "c"), ("ü", "u")]:
                s = s.replace(c, rep)
            return re.sub(r"[^a-z0-9]", "", s)

        # 0. Comprovar si hi ha quotes manuals definides per l'usuari
        manual_override_file = CACHE_DIR / "manual_odds_override.json"
        if manual_override_file.exists():
            try:
                with open(manual_override_file, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
                    cache_key = f"{comp_id}_{normalize(home_team)}_{normalize(away_team)}"
                    m_entry = overrides.get(cache_key) or overrides.get(f"{normalize(home_team)}_{normalize(away_team)}")
                    if m_entry and isinstance(m_entry, dict):
                        w_manual = m_entry.get("winamax") or m_entry.get("odds_winamax")
                        if w_manual and isinstance(w_manual, dict) and w_manual.get("1"):
                            odds_data["1X2"] = {
                                "1": float(w_manual.get("1")),
                                "X": float(w_manual.get("X")),
                                "2": float(w_manual.get("2"))
                            }
                            odds_data["matched"] = True
                            odds_data["is_manual_override"] = True
                            return odds_data
            except Exception:
                pass

        team_keywords = {
            # LaLiga EA Sports
            "madrid": "madrid", "sociedad": "sociedad", "barcelona": "barcelona",
            "athletic": "athletic", "atletico": "atletico", "villarreal": "villarreal",
            "sevilla": "sevilla", "betis": "betis", "valencia": "valencia",
            "celta": "celta", "osasuna": "osasuna", "rayo": "rayo",
            "getafe": "getafe", "alaves": "alaves", "espanyol": "espanyol",
            "racing": "racing", "deportivo": "deportiv", "malaga": "malaga",
            "levante": "levante", "elche": "elche",
            # Premier League
            "arsenal": "arsenal", "manchester city": "city", "man city": "city",
            "liverpool": "liverpool", "aston villa": "villa", "villa": "villa",
            "tottenham": "tottenham", "spurs": "tottenham", "chelsea": "chelsea",
            "newcastle": "newcastle", "manchester united": "united", "man united": "united",
            "west ham": "west", "brighton": "brighton", "bournemouth": "bournemouth",
            "fulham": "fulham", "wolves": "wolverhampton", "wolverhampton": "wolverhampton",
            "crystal palace": "palace", "palace": "palace", "brentford": "brentford",
            "everton": "everton", "nottingham": "forest", "forest": "forest",
            "ipswich": "ipswich", "leicester": "leicester", "southampton": "southampton",
            "leeds": "leeds", "sunderland": "sunderland",
            # LaLiga Hypermotion
            "zaragoza": "zaragoza", "oviedo": "oviedo", "sporting": "sporting",
            "gijon": "sporting", "eibar": "eibar", "castellon": "castellon", "burgos": "burgos",
            "albacete": "albacete", "huesca": "huesca", "granada": "granada",
            "almeria": "almeria", "cadiz": "cadiz", "cordoba": "cordoba",
            "mirandes": "mirandes", "eldense": "eldense", "ferrol": "ferrol",
            "tenerife": "tenerife", "cartagena": "cartagena", "girona": "girona",
            "sabadell": "sabadell", "ceuta": "ceuta", "palmas": "palmas",
            "mallorca": "mallorca", "fortuna": "celta", "valladolid": "valladolid",
            "leganes": "leganes", "andorra": "andorra",
            # EFL Championship
            "birmingham": "birmingham", "blackburn": "blackburn", "bolton": "bolton",
            "bristol": "bristol", "burnley": "burnley", "cardiff": "cardiff",
            "charlton": "charlton", "derby": "derby", "lincoln": "lincoln",
            "middlesbrough": "middlesbrough", "millwall": "millwall", "norwich": "norwich",
            "portsmouth": "portsmouth", "preston": "preston", "qpr": "qpr", "queens": "qpr",
            "sheffield": "sheffield", "sheff": "sheffield", "stoke": "stoke",
            "swansea": "swansea", "watford": "watford", "west brom": "bromwich",
            "wrexham": "wrexham", "coventry": "coventry", "hull": "hull",
            # Bundesliga
            "bayern": "bayern", "munich": "bayern", "munchen": "bayern",
            "leverkusen": "leverkusen", "bayer": "leverkusen",
            "dortmund": "dortmund", "leipzig": "leipzig",
            "frankfurt": "frankfurt", "francfort": "frankfurt",
            "stuttgart": "stuttgart", "friburgo": "friburgo", "freiburg": "friburgo",
            "gladbach": "gladbach", "monchengladbach": "gladbach",
            "bremen": "bremen", "augsburgo": "augsburg", "augsburg": "augsburg",
            "hoffenheim": "hoffenheim", "mainz": "mainz",
            "union berlin": "union", "colonia": "colonia", "koln": "colonia",
            "hamburgo": "hamburgo", "hamburg": "hamburgo",
            "schalke": "schalke", "paderborn": "paderborn", "elversberg": "elversberg",
            # Ligue 1
            "paris": "paris", "psg": "paris",
            "monaco": "monaco", "lille": "lille",
            "marseille": "marsella", "marsella": "marsella",
            "lyon": "lyon", "lens": "lens",
            "niza": "niza", "nice": "niza",
            "rennes": "rennes", "brest": "brest",
            "toulouse": "toulouse", "strasbourg": "estrasburgo", "estrasburgo": "estrasburgo",
            "le havre": "havre", "havre": "havre",
            "auxerre": "auxerre", "angers": "angers",
            "lorient": "lorient", "troyes": "troyes",
            "le mans": "mans",
            # Serie A
            "inter": "inter", "internazionale": "inter",
            "milan": "milan", "juventus": "juventus", "juve": "juventus",
            "atalanta": "atalanta", "napoli": "napoles", "napoles": "napoles",
            "roma": "roma", "lazio": "lazio",
            "fiorentina": "fiorentina", "bologna": "bolonia", "bolonia": "bolonia",
            "torino": "torino", "monza": "monza",
            "genoa": "genoa", "genova": "genoa", "udinese": "udinese",
            "cagliari": "cagliari", "lecce": "lecce",
            "parma": "parma", "como": "como",
            "venezia": "venecia", "venecia": "venecia",
            "sassuolo": "sassuolo", "frosinone": "frosinone"
        }

        sorted_keywords = sorted(team_keywords.items(), key=lambda x: len(x[0]), reverse=True)
        GENERIC_ROOTS = {"city", "united", "athletic", "town", "villa", "palace", "west", "sporting", "celta", "racing", "forest"}

        def get_keyword(name_str: str) -> str:
            n = normalize(name_str)
            for k, root in sorted_keywords:
                if k in n:
                    return root
                if root not in GENERIC_ROOTS and root in n:
                    return root
            return n[:5]

        h_id = self.db.find_team_id(home_team) if self.db else None
        a_id = self.db.find_team_id(away_team) if self.db else None
        h_key = get_keyword(home_team)
        a_key = get_keyword(away_team)

        # 1. Carregar l'índex del torneig de Winamax mitjançant HTTP directe (amb memòria cau per no saturar)
        cache_file = CACHE_DIR / f"winamax_cache_tid_{tid}.json"
        try:
            if tid in self._tournament_cache:
                state = self._tournament_cache[tid]
            else:
                cmd = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', base_tournament_url]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

                start = res.stdout.find('var PRELOADED_STATE = ')
                if start == -1:
                    start = res.stdout.find('PRELOADED_STATE = ')

                if start != -1:
                    state_str = res.stdout[start + len('var PRELOADED_STATE = '):] if 'var PRELOADED_STATE = ' in res.stdout[start:start+30] else res.stdout[start + len('PRELOADED_STATE = '):]
                    state, _ = json.JSONDecoder().raw_decode(state_str)
                    if state and state.get("matches"):
                        try:
                            with open(cache_file, "w", encoding="utf-8") as cf:
                                json.dump(state, cf)
                        except Exception:
                            pass
                else:
                    state = {}

                # Fallback al fitxer de cau si no s'ha obtingut resposta (ex: bloqueig cloud / GitHub Actions)
                if (not state or not state.get("matches")) and cache_file.exists():
                    try:
                        with open(cache_file, "r", encoding="utf-8") as cf:
                            state = json.load(cf)
                    except Exception:
                        pass

                self._tournament_cache[tid] = state

            matches = state.get("matches") or {}

            target_match = None
            target_mid = None
            target_tourn_id = WINAMAX_TOURNAMENT_IDS.get(comp_id)

            for mid, m in matches.items():
                if not isinstance(m, dict):
                    continue
                if target_tourn_id and m.get("tournamentId") != target_tourn_id:
                    continue
                c1 = m.get("competitor1Name")
                c2 = m.get("competitor2Name")
                if not c1 or not c2:
                    continue

                is_matched = False
                c1_id = self.db.find_team_id(c1) if self.db else None
                c2_id = self.db.find_team_id(c2) if self.db else None

                if h_id and a_id and c1_id and c2_id:
                    if h_id == c1_id and a_id == c2_id:
                        is_matched = True

                if not is_matched:
                    c1_key = get_keyword(c1)
                    c2_key = get_keyword(c2)
                    if (h_key == c1_key or (len(h_key) >= 4 and h_key in normalize(c1)) or (len(c1_key) >= 4 and c1_key in normalize(home_team))) and \
                       (a_key == c2_key or (len(a_key) >= 4 and a_key in normalize(c2)) or (len(c2_key) >= 4 and c2_key in normalize(away_team))):
                        is_matched = True

                if is_matched:
                    target_match = m
                    target_mid = str(mid)
                    break

            if target_match and target_mid:
                match_url = f"https://www.winamax.es/apuestas-deportivas/match/{target_mid}"
                odds_data["matched"] = True
                odds_data["match_id"] = target_mid
                odds_data["matched_title"] = target_match.get("title")
                odds_data["url"] = match_url

                print(f"[+] Partit descobert autònomament: '{target_match.get('title')}' (ID Winamax: {target_mid})")
                print(f"[*] Accedint a tots els mercats: {match_url}")

                if target_mid in self._match_cache:
                    match_state = self._match_cache[target_mid]
                else:
                    cmd_m = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', match_url]
                    res_m = subprocess.run(cmd_m, capture_output=True, text=True, encoding='utf-8', errors='replace')
                    start_m = res_m.stdout.find('var PRELOADED_STATE = ')
                    if start_m != -1:
                        state_m_str = res_m.stdout[start_m + len('var PRELOADED_STATE = '):]
                        match_state, _ = json.JSONDecoder().raw_decode(state_m_str)
                    else:
                        match_state = state
                    self._match_cache[target_mid] = match_state

                bets = match_state.get("bets") or {}
                odds = match_state.get("odds") or {}
                outcomes = match_state.get("outcomes") or {}

                # 1X2 del PARTIT COMPLET
                main_bet_id = target_match.get("mainBetId")
                if main_bet_id:
                    b_info = bets.get(str(main_bet_id)) or {}
                    out_ids = b_info.get("outcomes") or []
                    for oid in out_ids:
                        o_obj = outcomes.get(str(oid)) or {}
                        code = str(o_obj.get("code") or "").lower()
                        val = odds.get(str(oid))
                        if val is not None:
                            if code == "1":
                                odds_data["1X2"]["1"] = float(val)
                            elif code == "x":
                                odds_data["1X2"]["X"] = float(val)
                            elif code == "2":
                                odds_data["1X2"]["2"] = float(val)

                # Mercats addicionals
                for bid, b_info in bets.items():
                    if not isinstance(b_info, dict):
                        continue
                    m_id_val = str(b_info.get("matchId") or "")
                    if m_id_val == target_mid or m_id_val == str(target_mid):
                        b_title = str(b_info.get("betTitle") or b_info.get("betTypeName") or "Mercat").strip()
                        b_cat = str(b_info.get("betTypeCategory") or "").strip()
                        b_title_lower = b_title.lower()
                        b_cat_lower = b_cat.lower()
                        out_ids = b_info.get("outcomes") or []

                        if any(x in b_title_lower for x in ["1ª mitad", "1a mitad", "2ª mitad", "2a mitad", "descanso", "mitad con más goles"]):
                            continue

                        # Doble Oportunitat
                        if b_title_lower == "doble oportunidad":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "o empate" in lbl_lower:
                                        if normalize(target_match.get("competitor1Name", "")) in normalize(lbl):
                                            odds_data["double_chance"]["1X"] = float(val)
                                        else:
                                            odds_data["double_chance"]["X2"] = float(val)
                                    elif "o" in lbl_lower and "empate" not in lbl_lower:
                                        odds_data["double_chance"]["12"] = float(val)

                        # Total de Gols (Over / Under 1.5, 2.5, 3.5)
                        elif b_cat_lower == "total de goles" and b_title_lower == "número total de goles":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 1,5" in lbl_lower or "más de 1.5" in lbl_lower:
                                        odds_data["goals"]["Over 1.5"] = float(val)
                                    elif "menos de 1,5" in lbl_lower or "menos de 1.5" in lbl_lower:
                                        odds_data["goals"]["Under 1.5"] = float(val)
                                    elif "más de 2,5" in lbl_lower or "más de 2.5" in lbl_lower:
                                        odds_data["goals"]["Over 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["Over 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["over"] = float(val)
                                    elif "menos de 2,5" in lbl_lower or "menos de 2.5" in lbl_lower:
                                        odds_data["goals"]["Under 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["Under 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["under"] = float(val)
                                    elif "más de 3,5" in lbl_lower or "más de 3.5" in lbl_lower:
                                        odds_data["goals"]["Over 3.5"] = float(val)
                                    elif "menos de 3,5" in lbl_lower or "menos de 3.5" in lbl_lower:
                                        odds_data["goals"]["Under 3.5"] = float(val)

                        # Gols per equip (si Winamax ho ofereix de forma real)
                        elif b_cat_lower == "goles por equipo" and "número total de goles marcados por" in b_title_lower:
                            c1_norm = normalize(target_match.get("competitor1Name", ""))
                            c2_norm = normalize(target_match.get("competitor2Name", ""))
                            title_norm = normalize(b_title)
                            team_key = "home" if c1_norm and c1_norm in title_norm else ("away" if c2_norm and c2_norm in title_norm else None)
                            if team_key:
                                if team_key not in odds_data["team_goals"]:
                                    odds_data["team_goals"][team_key] = {}
                                for oid in out_ids:
                                    o_obj = outcomes.get(str(oid)) or {}
                                    lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                    val = odds.get(str(oid))
                                    if val is not None:
                                        lbl_lower = lbl.lower()
                                        if "más de 0,5" in lbl_lower or "más de 0.5" in lbl_lower:
                                            odds_data["team_goals"][team_key]["Over 0.5"] = float(val)
                                        elif "más de 1,5" in lbl_lower or "más de 1.5" in lbl_lower:
                                            odds_data["team_goals"][team_key]["Over 1.5"] = float(val)

                        # Ambdós marquen
                        elif b_title_lower in ["ambos equipos marcan", "¿ambos equipos marcarán?", "ambos equipos marcarán"]:
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    if lbl.lower() in ["sí", "si", "yes"]:
                                        odds_data["btts"]["Sí"] = float(val)
                                        odds_data["btts"]["yes"] = float(val)
                                    elif lbl.lower() in ["no"]:
                                        odds_data["btts"]["No"] = float(val)
                                        odds_data["btts"]["no"] = float(val)

                        # Targetes
                        elif "tarjeta" in b_cat_lower and b_title_lower == "número total de tarjetas":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 4,5" in lbl_lower or "más de 4.5" in lbl_lower:
                                        odds_data["cards"]["Over 4.5 Targetes"] = float(val)
                                    elif "menos de 4,5" in lbl_lower or "menos de 4.5" in lbl_lower:
                                        odds_data["cards"]["Under 4.5 Targetes"] = float(val)
                                    elif "más de 5,5" in lbl_lower or "más de 5.5" in lbl_lower:
                                        odds_data["cards"]["Over 5.5 Targetes"] = float(val)

                        # Targeta Vermella
                        elif "tarjeta" in b_cat_lower and b_title_lower == "expulsión":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None and lbl.lower() in ["sí", "si"]:
                                    odds_data["cards"]["Targeta Vermella (Sí)"] = float(val)

                        # Córners (Extracció dinàmica de totes les línies Over/Under del partit complet)
                        elif ("córner" in b_cat_lower or "corner" in b_cat_lower):
                            # Evitar meitats, descans o córners individuals d'un sol equip
                            if any(sw in b_title_lower for sw in ["mitad", "descanso", "tiempo", "prórroga"]):
                                continue

                            clean_title = re.sub(r'\s+', ' ', b_title_lower).strip()
                            valid_match_titles = [
                                "número total córneres",
                                "número total de córneres",
                                "total córneres",
                                "total de córneres"
                            ]
                            if clean_title not in valid_match_titles:
                                continue

                            title_norm = normalize(b_title)
                            if (h_key and h_key in title_norm) or (a_key and a_key in title_norm):
                                continue  # Ignorar córners per equip

                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_clean = lbl.lower().replace(",", ".")
                                    m_c = re.search(r'(m[aá]s|menos)\s+de\s+(\d+(?:\.\d+)?)', lbl_clean)
                                    if m_c:
                                        dir_str = m_c.group(1).lower()
                                        prefix = "Under" if "menos" in dir_str else "Over"
                                        c_line = m_c.group(2)
                                        try:
                                            c_line_flt = float(c_line)
                                            if 5.5 <= c_line_flt <= 15.5:
                                                odds_data["corners"][f"{prefix} {c_line} Córners"] = float(val)
                                        except ValueError:
                                            pass

            else:
                print(f"[!] No s'ha trobat cap partit actiu a Winamax per a '{home_team}' vs '{away_team}'.")

        except Exception as e:
            print(f"[!] Error durant l'extracció de Winamax: {e}")

        return odds_data

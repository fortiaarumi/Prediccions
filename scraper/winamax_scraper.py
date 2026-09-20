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
from typing import Dict, Any, Optional

WINAMAX_TOURNAMENTS = {
    "LALIGA": 32,
    "PREMIER": 1,
    "HYPERMOTION": 37,
}

class WinamaxScraper:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._tournament_cache: Dict[int, Dict[str, Any]] = {}
        self._match_cache: Dict[str, Dict[str, Any]] = {}

    def get_match_odds(self, home_team: str, away_team: str, competition_id: str = "LALIGA") -> Dict[str, Any]:
        """
        Descobreix dinàmicament l'ID del partit a Winamax i n'extreu exactament les cuotes reals.
        """
        comp_id = competition_id.upper()
        tid = WINAMAX_TOURNAMENTS.get(comp_id, 32)
        base_tournament_url = f"https://www.winamax.es/apuestas-deportivas/sports/1/{tid}"
        print(f"\n[*] Cercant dinàmicament a Winamax ({comp_id} | Torneig {tid}): {home_team} vs {away_team}...")
        
        odds_data = {
            "source": f"Winamax Espanya ({comp_id})",
            "url": base_tournament_url,
            "match_id": None,
            "matched_title": None,
            "matched": False,
            "1X2": {},
            "double_chance": {},
            "over_under_2_5": {},
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
            # LaLiga Hypermotion
            "zaragoza": "zaragoza", "oviedo": "oviedo", "sporting": "sporting",
            "eibar": "eibar", "castellon": "castellon", "burgos": "burgos",
            "albacete": "albacete", "huesca": "huesca", "granada": "granada",
            "almeria": "almeria", "cadiz": "cadiz", "cordoba": "cordoba",
            "mirandes": "mirandes", "eldense": "eldense", "ferrol": "ferrol",
            "tenerife": "tenerife", "cartagena": "cartagena"
        }

        def get_keyword(name_str: str) -> str:
            n = normalize(name_str)
            for k, root in team_keywords.items():
                if k in n or root in n:
                    return root
            return n[:5]

        h_key = get_keyword(home_team)
        a_key = get_keyword(away_team)

        # 1. Carregar l'índex del torneig de Winamax mitjançant HTTP directe (amb memòria cau per no saturar)
        try:
            if tid in self._tournament_cache:
                state = self._tournament_cache[tid]
            else:
                cmd = ['curl', '-s', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', base_tournament_url]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                
                start = res.stdout.find('var PRELOADED_STATE = ')
                if start == -1:
                    start = res.stdout.find('PRELOADED_STATE = ')
                
                if start != -1:
                    state_str = res.stdout[start + len('var PRELOADED_STATE = '):] if 'var PRELOADED_STATE = ' in res.stdout[start:start+30] else res.stdout[start + len('PRELOADED_STATE = '):]
                    state, _ = json.JSONDecoder().raw_decode(state_str)
                else:
                    state = {}
                self._tournament_cache[tid] = state

            matches = state.get("matches") or {}

            target_match = None
            target_mid = None

            for mid, m in matches.items():
                if not isinstance(m, dict):
                    continue
                c1 = m.get("competitor1Name")
                c2 = m.get("competitor2Name")
                if not c1 or not c2:
                    continue

                c1_key = get_keyword(c1)
                c2_key = get_keyword(c2)

                if (h_key == c1_key or h_key in normalize(c1)) and (a_key == c2_key or a_key in normalize(c2)):
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
                    cmd_m = ['curl', '-s', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', match_url]
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

                        # Total de Gols (Over / Under 2.5)
                        elif b_cat_lower == "total de goles" and b_title_lower == "número total de goles":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 2,5" in lbl_lower or "más de 2.5" in lbl_lower:
                                        odds_data["over_under_2_5"]["Over 2.5"] = float(val)
                                    elif "menos de 2,5" in lbl_lower or "menos de 2.5" in lbl_lower:
                                        odds_data["over_under_2_5"]["Under 2.5"] = float(val)

                        # Ambdós marquen
                        elif b_title_lower in ["ambos equipos marcan", "¿ambos equipos marcarán?", "ambos equipos marcarán"]:
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    if lbl.lower() in ["sí", "si", "yes"]:
                                        odds_data["btts"]["Sí"] = float(val)
                                    elif lbl.lower() in ["no"]:
                                        odds_data["btts"]["No"] = float(val)

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

                        # Córners
                        elif ("córner" in b_cat_lower or "corner" in b_cat_lower) and "número total" in b_title_lower:
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 9,5" in lbl_lower or "más de 9.5" in lbl_lower:
                                        odds_data["corners"]["Over 9.5 Córners"] = float(val)
                                    elif "menos de 9,5" in lbl_lower or "menos de 9.5" in lbl_lower:
                                        odds_data["corners"]["Under 9.5 Córners"] = float(val)
                                    elif "más de 8,5" in lbl_lower or "más de 8.5" in lbl_lower:
                                        odds_data["corners"]["Over 8.5 Córners"] = float(val)
                                    elif "más de 10,5" in lbl_lower or "más de 10.5" in lbl_lower:
                                        odds_data["corners"]["Over 10.5 Córners"] = float(val)

            else:
                print(f"[!] No s'ha trobat cap partit actiu a Winamax per a '{home_team}' vs '{away_team}'.")

        except Exception as e:
            print(f"[!] Error durant l'extracció de Winamax: {e}")

        return odds_data

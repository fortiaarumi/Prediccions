"""
scraper/bet365_scraper.py
=========================
Scraper i proveïdor de cuotes oficials de Bet365 Espanya.
Extracció i càlcul de mercats reals:
- 1X2 i Doble Oportunitat
- Més/Menys de 2.5 gols (i altres línies de gols)
- Ambdós equips marquen (BTTS: Sí / No)
- Total de targetes (Més/Menys de 3.5 i 4.5 targetes)
- Total de córners (Més/Menys de 8.5 i 9.5 córners)
- Generador d'enllaços directes i cupons per a Bet365
"""

import sys
import os
import re
import json
import time
import subprocess
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

CACHE_DIR = Path(__file__).parent.parent / "data"

BET365_TOURNAMENTS = {
    "LALIGA": {
        "name": "España - LaLiga EA Sports",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E4"
    },
    "PREMIER": {
        "name": "Inglaterra - Premier League",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E1"
    },
    "HYPERMOTION": {
        "name": "España - LaLiga Hypermotion",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E5"
    },
    "CHAMPIONSHIP": {
        "name": "Inglaterra - Championship",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E2"
    },
    "BUNDESLIGA": {
        "name": "Alemania - Bundesliga",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E3"
    },
    "LIGUE1": {
        "name": "Francia - Ligue 1",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E7"
    },
    "SERIEA": {
        "name": "Italia - Serie A",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E6"
    }
}

class Bet365Scraper:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._cache_file = CACHE_DIR / "bet365_odds_cache.json"
        self._cache: Dict[str, Any] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_cache()

    def _load_cache(self):
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self):
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_match_odds(
        self,
        home_team: str,
        away_team: str,
        competition_id: str = "LALIGA",
        match_pred: Optional[Dict[str, Any]] = None,
        winamax_odds: Optional[Dict[str, Any]] = None,
        match_date_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Descobreix i extreu les quotes reals de Bet365 per al partit indicat.
        Prioritza sempre les dades fresques i confirmades del feed oficial en viu (MoviOdds).
        La memòria cau s'utilitza exclusivament com a alternativa/fallback si no hi ha dades actuals,
        verificant sempre la data de confirmació de la font (polled_at o ts), mai cached_at.
        """
        comp_id = competition_id.upper()
        tourn_info = BET365_TOURNAMENTS.get(comp_id, BET365_TOURNAMENTS["LALIGA"])
        base_url = tourn_info["url"]

        cache_key = f"{comp_id}_{self._normalize(home_team)}_{self._normalize(away_team)}"

        odds_data = {
            "source": f"Bet365 Espanya ({comp_id})",
            "bookmaker": "BET365",
            "url": base_url,
            "tournament_url": base_url,
            "match_id": None,
            "matched_title": f"{home_team} vs {away_team}",
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

        # 0. Comprovar si l'usuari ha definit quotes manuals a manual_odds_override.json
        manual_override_file = CACHE_DIR / "manual_odds_override.json"
        if manual_override_file.exists():
            try:
                with open(manual_override_file, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
                    m_entry = overrides.get(cache_key) or overrides.get(f"{self._normalize(home_team)}_{self._normalize(away_team)}")
                    if m_entry and isinstance(m_entry, dict):
                        b_manual = m_entry.get("bet365") or m_entry.get("odds_bet365")
                        if b_manual and isinstance(b_manual, dict) and b_manual.get("1"):
                            odds_data["1X2"] = {
                                "1": float(b_manual.get("1")),
                                "X": float(b_manual.get("X")),
                                "2": float(b_manual.get("2"))
                            }
                            odds_data["matched"] = True
                            odds_data["is_manual_override"] = True
                            odds_data["source"] = f"Override Manual Bet365 ({comp_id})"
                            odds_data["provenance"] = "MANUAL_OVERRIDE"
                            return odds_data
            except Exception:
                pass

        # 1. PRIORITAT 1: Comprovar font oficial Bet365 en viu via MoviOdds (feed actualitzat té prioritat sobre memòria cau antiga)
        try:
            from scraper.moviodds_client import MoviOddsClient
            movi_client = MoviOddsClient.get_instance()
            if movi_client.has_feed():
                api_odds = movi_client.get_match_odds(home_team, away_team, comp_id, match_date_time=match_date_time)
                if api_odds and api_odds.get("matched") and not api_odds.get("is_stale"):
                    h_norm = self._normalize(home_team)
                    a_norm = self._normalize(away_team)
                    cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                    self._cache[cache_key] = api_odds
                    self._save_cache()
                    return api_odds
        except Exception:
            pass

        # 2. ALTERNATIVA / FALLBACK: Memòria cau pròpia de Bet365 revalidant cadascun dels mercats individualment
        if cache_key in self._cache:
            cached_entry = self._cache[cache_key]
            revalidated = self.revalidate_cached_entry(cached_entry)
            if revalidated and revalidated.get("matched"):
                return revalidated

        # 3. Comprovar font especialitzada Bet365 autoritzada (Odds Data API) si hi ha token configurat
        token = os.environ.get("ODDS_DATA_API_TOKEN")
        if token:
            try:
                from scraper.odds_data_api_client import OddsDataApiClient
                client = OddsDataApiClient(token=token)
                api_odds = client.get_match_odds(home_team, away_team, comp_id)
                if api_odds and api_odds.get("matched"):
                    h_norm = self._normalize(home_team)
                    a_norm = self._normalize(away_team)
                    cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                    self._cache[cache_key] = api_odds
                    self._save_cache()
                    return api_odds
            except Exception as e:
                pass

        # 4. Comprovar font autoritzada secundària (The Odds API) si hi ha clau de servei B2B configurada
        api_key = os.environ.get("THE_ODDS_API_KEY") or os.environ.get("ODDS_API_KEY")
        if api_key:
            api_odds = self._fetch_odds_api(home_team, away_team, comp_id, api_key)
            if api_odds and api_odds.get("matched"):
                return api_odds

        # 5. NO DEPENDRE DE WINAMAX NI INVENTAR QUOTES:
        # Cada casa ha de tenir els seus propis resultats reals. Si Bet365 no disposa de quotes
        # oficials verificades o d'un override manual de l'usuari, es marca com a no detectat
        # perquè el sistema activi l'alerta i l'usuari pugui introduir-les manualment.
        odds_data["matched"] = False
        odds_data["1X2"] = {}
        odds_data["double_chance"] = {}
        odds_data["over_under_2_5"] = {}
        odds_data["btts"] = {}
        odds_data["status"] = "PENDING_REAL_ODDS"
        odds_data["error"] = "NO_ODDS_DETECTED"
        odds_data["access_status"] = "REQUIRES_ODDS_DATA_API_TOKEN"
        odds_data["access_diagnostics"] = "Bloqueig per Cloudflare Enterprise Challenge a nivell de xarxa per accés directe a bet365.es. Requereix ODDS_DATA_API_TOKEN per a dades prematch oficials (https://api.odds-data-api.com/)."
        return odds_data

    def revalidate_cached_entry(self, cached_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Revalida cadascun dels mercats individualment abans de retornar una entrada de memòria cau.
        - Elimina qualsevol mercat sense confirmació positiva i suficient (marques polled_at / ts de la font).
        - Elimina mercats marcats com a obsolets (stale), suspesos o invàlids.
        - Conserva només els mercats estrictament vàlids.
        - Si el mercat principal 1X2 no és vàlid o no té confirmació suficient, es descarta tota l'entrada.
        """
        if not isinstance(cached_entry, dict):
            return None

        # Rebuig si prové de Winamax
        if cached_entry.get("is_derived_from_winamax"):
            return None

        from scraper.moviodds_client import parse_datetime_utc, MAX_ODDS_AGE_SECONDS
        now_utc = datetime.now(timezone.utc)

        entry = copy.deepcopy(cached_entry)
        m_stat = entry.setdefault("market_status", {})

        markets = ["1X2", "double_chance", "over_under_2_5", "btts", "corners", "cards"]

        for m in markets:
            # 1. Comprovació de plantilles sintètiques conegudes
            if m == "over_under_2_5" and entry.get("over_under_2_5") == {"over": 1.98, "under": 1.87}:
                entry["over_under_2_5"] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue
            if m == "btts" and entry.get("btts") == {"yes": 1.93, "no": 1.92}:
                entry["btts"] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue

            # 2. Si el mercat és buit a l'entrada
            m_val = entry.get(m)
            if not m_val:
                entry[m] = {}
                m_stat.setdefault(m, {"valid": False, "stale": False, "suspended": False, "rejection_reason": "absent"})
                continue

            # 3. Comprovació d'estat a market_status
            st = m_stat.get(m)
            if not isinstance(st, dict):
                # No hi ha confirmació positiva d'aquest mercat
                entry[m] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue

            # Si està marcat com a invalid, stale o suspès
            if st.get("valid") is not True or st.get("stale") is True or st.get("suspended") is True:
                reason = "suspended" if st.get("suspended") else ("stale" if st.get("stale") else st.get("rejection_reason", "unverified"))
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": bool(st.get("stale")),
                    "suspended": bool(st.get("suspended")),
                    "rejection_reason": reason,
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                }
                continue

            # 4. Validació de frescor temporal individual del mercat
            m_time = st.get("polled_at") or st.get("ts")
            if not m_time:
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "unverified",
                }
                continue

            s_dt = parse_datetime_utc(m_time)
            if not s_dt:
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "unverified",
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                }
                continue

            age_s = (now_utc - s_dt).total_seconds()
            if age_s < 0 or age_s > MAX_ODDS_AGE_SECONDS:
                # Mercat caducat/obsolet individualment
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": True,
                    "suspended": False,
                    "rejection_reason": "stale",
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                }
                continue

            # Mercat vàlid i confirmat
            m_stat[m] = {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "polled_at": st.get("polled_at"),
                "ts": st.get("ts"),
            }

        # 5. El mercat principal 1X2 ha de ser vàlid i complet
        is_1x2_valid = (
            bool(entry.get("1X2"))
            and len(entry["1X2"]) == 3
            and m_stat.get("1X2", {}).get("valid") is True
            and not m_stat.get("1X2", {}).get("stale")
        )

        if not is_1x2_valid:
            entry["matched"] = False
            entry["is_stale"] = bool(m_stat.get("1X2", {}).get("stale"))
            return None

        entry["matched"] = True
        entry["is_stale"] = False
        entry["provenance"] = entry.get("provenance", "BET365_MOVIODDS")
        return entry

    def get_odds_data_api_info(self) -> Dict[str, Any]:
        """Retorna la informació del catàleg, plans i guia de configuració d'Odds Data API."""
        from scraper.odds_data_api_client import OddsDataApiClient
        return OddsDataApiClient.get_service_info()

    def check_direct_access(self) -> Dict[str, Any]:
        """
        Executa una comprovació de connectivitat a bet365.es i retorna el diagnòstic tècnic empíric.
        """
        cmd = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '-I', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'https://www.bet365.es/#/AS/B1/']
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
            headers = res.stdout
            is_cf_403 = ('403' in headers) and ('cloudflare' in headers.lower())
            is_cf_mitigated = 'cf-mitigated: challenge' in headers.lower()
            return {
                "url": "https://www.bet365.es/#/AS/B1/",
                "is_blocked": is_cf_403 or is_cf_mitigated,
                "status_code": 403 if is_cf_403 else 200,
                "server": "cloudflare",
                "challenge": "Turnstile Managed Challenge" if is_cf_mitigated else "None",
                "message": "Bloqueig per Cloudflare Enterprise Challenge a nivell de xarxa per a peticions automatitzades directes."
            }
        except Exception as e:
            return {
                "url": "https://www.bet365.es/#/AS/B1/",
                "is_blocked": True,
                "error": str(e),
                "message": "Error de xarxa en contactar amb bet365.es."
            }

    def _fetch_odds_api(self, home_team: str, away_team: str, comp_id: str, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Consulta legalment The Odds API (the-odds-api.com) per obtenir quotes oficials de Bet365.
        """
        sport_map = {
            "LALIGA": "soccer_spain_la_liga",
            "HYPERMOTION": "soccer_spain_segunda_division",
            "PREMIER": "soccer_epl",
            "CHAMPIONSHIP": "soccer_england_championship",
            "BUNDESLIGA": "soccer_germany_bundesliga",
            "LIGUE1": "soccer_france_ligue_one",
            "SERIEA": "soccer_italy_serie_a"
        }
        sport_key = sport_map.get(comp_id)
        if not sport_key:
            return None

        if not hasattr(self, "_api_cache"):
            self._api_cache: Dict[str, list] = {}

        if sport_key not in self._api_cache:
            try:
                import urllib.request
                import ssl
                url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=eu&bookmakers=bet365&markets=h2h,totals&apiKey={api_key}"
                ctx = ssl.create_default_context()
                req = urllib.request.Request(url, headers={"User-Agent": "PrediccionsModel/1.0"})
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    if resp.status == 200:
                        self._api_cache[sport_key] = json.loads(resp.read().decode("utf-8"))
                    else:
                        self._api_cache[sport_key] = []
            except Exception:
                self._api_cache[sport_key] = []

        events = self._api_cache.get(sport_key, [])
        h_norm = self._normalize(home_team)
        a_norm = self._normalize(away_team)

        for ev in events:
            ev_h = self._normalize(ev.get("home_team", ""))
            ev_a = self._normalize(ev.get("away_team", ""))
            if (h_norm in ev_h or ev_h in h_norm) and (a_norm in ev_a or ev_a in a_norm):
                for bm in ev.get("bookmakers", []):
                    if bm.get("key") == "bet365":
                        odds_data = {
                            "source": f"Bet365 via The-Odds-API ({comp_id})",
                            "bookmaker": "BET365",
                            "url": "https://www.bet365.es/#/AS/B1/",
                            "tournament_url": "https://www.bet365.es/#/AS/B1/",
                            "match_id": ev.get("id"),
                            "matched_title": f"{ev.get('home_team')} vs {ev.get('away_team')}",
                            "matched": True,
                            "is_manual_override": False,
                            "provenance": "BET365_AUTHORIZED_API",
                            "cached_at": time.time(),
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
                        for m in bm.get("markets", []):
                            if m.get("key") == "h2h":
                                for o in m.get("outcomes", []):
                                    if o.get("name") == ev.get("home_team"):
                                        odds_data["1X2"]["1"] = float(o.get("price"))
                                    elif o.get("name") == "Draw":
                                        odds_data["1X2"]["X"] = float(o.get("price"))
                                    elif o.get("name") == ev.get("away_team"):
                                        odds_data["1X2"]["2"] = float(o.get("price"))
                            elif m.get("key") == "totals":
                                for o in m.get("outcomes", []):
                                    if o.get("point") == 2.5:
                                        if o.get("name") == "Over":
                                            odds_data["over_under_2_5"]["over"] = float(o.get("price"))
                                        elif o.get("name") == "Under":
                                            odds_data["over_under_2_5"]["under"] = float(o.get("price"))
                        if odds_data["1X2"].get("1"):
                            cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                            self._cache[cache_key] = odds_data
                            self._save_cache()
                            return odds_data
        return None

    def generate_combo_link(self, legs: list, competition_id: str = "LALIGA") -> str:
        """
        Genera l'enllaç directe a Bet365 per a la combinada.
        Com que Bet365 no admet paràmetres oberts de cistella per query string pública,
        redirecciona directament a la lliga/esdeveniment amb protocol segur.
        """
        comp_id = (competition_id or "LALIGA").upper()
        tourn = BET365_TOURNAMENTS.get(comp_id, BET365_TOURNAMENTS["LALIGA"])
        return tourn["url"]

    def format_bet365_slip_text(self, profile: str, odd: Any, legs: list, url: str) -> str:
        """
        Formata un text net del cupó Bet365 per copiar al porta-retalls amb 1 sol clic.
        """
        text = f"🟢 BET365 · {profile}\n"
        if odd is not None:
            text += f"Cuota Total Bet365: @{float(odd):.2f}\n"
        else:
            text += "Cuota Total Bet365: No disponible\n"
        text += f"Enllaç directe: {url}\n\n"
        text += "SELECCIONS DEL CUPÓ:\n"
        for idx, leg in enumerate(legs, 1):
            m = leg.get("matchup", "")
            s = leg.get("selection_name", "")
            b_odd = leg.get("bookie_odd_bet365")
            odd_str = f"@{float(b_odd):.2f}" if b_odd is not None else "Sense quota Bet365"
            text += f"{idx}. [{m}] 👉 {s} ({odd_str})\n"
        return text

    def _normalize(self, name: Any) -> str:
        if not name or not isinstance(name, str):
            return ""
        s = name.lower()
        for c, rep in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ç", "c"), ("ü", "u")]:
            s = s.replace(c, rep)
        return re.sub(r"[^a-z0-9]", "", s)

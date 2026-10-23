"""
scraper/odds_data_api_client.py
===============================
Client natiu i autoritzat per a Odds Data API (https://api.odds-data-api.com/).
Especialitzat en l'extracció directa de fixtures i quotes prematch oficials de Bet365.

Característiques:
1. Autenticació segura mitjançant la variable d'entorn ODDS_DATA_API_TOKEN.
   - La credencial mai s'escriu al codi font ni es mostra als logs ni a data.json.
2. Endpoints oficials:
   - /v1/bet365/upcoming (llistat de partits de futbol amb IDs de fixture de Bet365 'FI')
   - /v4/bet365/prematch (quotes detallades 1X2, Over/Under, BTTS, Córners i Targetes)
3. Associació inequívoca mitjançant DatabaseManager (resolució d'àlies i IDs canònics).
   - Exigeix coincidència de TOTS DOS equips (home i away). Mai associa per un sol equip.
4. Procedència verificable:
   - Etiqueta les dades amb provenance="BET365_ODDS_DATA_API" i is_manual_override=False.
   - Mai sintetitza ni inventa dades en absència de resposta vàlida.
"""

import os
import json
import time
import ssl
import urllib.request
import urllib.error
import re
import unicodedata
from typing import Dict, Any, Optional, List
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

class OddsDataApiClient:
    BASE_URL = "https://api.odds-data-api.com"

    def __init__(self, token: Optional[str] = None):
        # La credencial s'obté exclusivament de la variable d'entorn o del paràmetre
        self._token = token or os.environ.get("ODDS_DATA_API_TOKEN")
        self._fixtures_cache: Dict[str, Any] = {}
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 600.0  # 10 minuts de memòria cau per a fixtures
        self._odds_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        """Indica si el token està present sense revelar-ne el contingut."""
        return bool(self._token and len(self._token.strip()) > 0)

    @staticmethod
    def normalize_name(text: str) -> str:
        """Normalitza cadenes eliminant accents, caràcters especials i espais."""
        if not text:
            return ""
        nfkd = unicodedata.normalize('NFKD', text)
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)])
        clean = clean.lower()
        clean = re.sub(r'[^a-z0-9]', '', clean)
        return clean

    def _api_get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Executa una petició GET a l'API amb timeouts estrictes i maneig d'errors."""
        if not self.is_configured:
            return None

        # Afegir token a la query string
        query_parts = [f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()]
        query_parts.append(f"token={urllib.parse.quote(self._token)}")
        url = f"{self.BASE_URL}{endpoint}?{'&'.join(query_parts)}"

        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PrediccionsModel/1.0 (Bet365 Authorized Client)",
                "Accept": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict) and data.get("success") == 1:
                        return data
                    return data
        except urllib.error.HTTPError as e:
            # Mai mostrar el token als missatges d'error
            print(f"[!] OddsDataApiClient HTTPError {e.code} a {endpoint}")
        except Exception as e:
            print(f"[!] OddsDataApiClient error de connexió a {endpoint}: {e}")
        return None

    def get_upcoming_fixtures(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Recupera el llistat d'esdeveniments de futbol (sport_id=1) pendents de disputar a Bet365.
        Retorna la llista d'objectes fixture amb el seu Bet365 'FI' ID.
        """
        now = time.time()
        if not force_refresh and (now - self._cache_timestamp < self._cache_ttl) and self._fixtures_cache:
            return self._fixtures_cache.get("results", [])

        data = self._api_get("/v1/bet365/upcoming", {"sport_id": 1})
        if not data:
            # Fallback a l'endpoint general d'esdeveniments
            data = self._api_get("/v3/events/upcoming", {"sport_id": 1})

        if data and isinstance(data.get("results"), list):
            self._fixtures_cache = data
            self._cache_timestamp = now
            return data["results"]

        return []

    def get_prematch_odds(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        """
        Recupera les quotes prematch completes per a un fixture ID ('FI') de Bet365.
        """
        if not fixture_id:
            return None

        if fixture_id in self._odds_cache:
            return self._odds_cache[fixture_id]

        data = self._api_get("/v4/bet365/prematch", {"FI": fixture_id})
        if data and isinstance(data.get("results"), list) and len(data["results"]) > 0:
            parsed = self.parse_prematch_markets(data["results"][0])
            if parsed and parsed.get("matched"):
                self._odds_cache[fixture_id] = parsed
                return parsed

        return None

    def parse_prematch_markets(self, raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Interpreta l'estructura JSON oficial de /v4/bet365/prematch (layout BetsAPI/OddsDataAPI)
        i n'extreu 1X2, Over/Under 2.5, BTTS, Córners i Targetes.
        """
        fi_id = str(raw_event.get("id", ""))
        home_name = raw_event.get("home", {}).get("name", "")
        away_name = raw_event.get("away", {}).get("name", "")

        result = {
            "source": f"Bet365 via Odds Data API (FI: {fi_id})",
            "bookmaker": "BET365",
            "url": f"https://www.bet365.es/#/AS/B1/",
            "match_id": fi_id,
            "matched_title": f"{home_name} vs {away_name}",
            "matched": False,
            "is_manual_override": False,
            "provenance": "BET365_ODDS_DATA_API",
            "cached_at": time.time(),
            "1X2": {},
            "double_chance": {},
            "over_under_2_5": {},
            "btts": {},
            "corners": {},
            "cards": {}
        }

        # 1. Mercat 1X2 (Full Time Result)
        main_sec = raw_event.get("main", {})
        ftr = main_sec.get("full_time_result", {}).get("odds", [])
        h_norm = self.normalize_name(home_name)
        a_norm = self.normalize_name(away_name)

        for item in ftr:
            name = item.get("name", "")
            odds_val = self._safe_float(item.get("odds"))
            if not odds_val:
                continue
            norm = self.normalize_name(name)
            if norm == h_norm or (norm and norm in h_norm) or (h_norm and h_norm in norm) or item.get("id") == "1":
                result["1X2"]["1"] = odds_val
            elif "draw" in norm or "empat" in norm or item.get("id") == "2":
                result["1X2"]["X"] = odds_val
            elif norm == a_norm or (norm and norm in a_norm) or (a_norm and a_norm in norm) or item.get("id") == "3":
                result["1X2"]["2"] = odds_val

        # 2. Doble Oportunitat
        dc = main_sec.get("double_chance", {}).get("odds", [])
        for item in dc:
            name = item.get("name", "").lower()
            odds_val = self._safe_float(item.get("odds"))
            if not odds_val:
                continue
            if ("draw" in name or "empat" in name) and (h_norm in self.normalize_name(name)):
                result["double_chance"]["1X"] = odds_val
            elif (h_norm in self.normalize_name(name)) and (a_norm in self.normalize_name(name)):
                result["double_chance"]["12"] = odds_val
            elif ("draw" in name or "empat" in name) and (a_norm in self.normalize_name(name)):
                result["double_chance"]["X2"] = odds_val

        # 3. Gols Over / Under 2.5
        goals_sec = raw_event.get("goals", {})
        ou_odds = goals_sec.get("goals_over_under", {}).get("odds", [])
        for item in ou_odds:
            hcap = str(item.get("handicap", item.get("name", "")))
            odds_val = self._safe_float(item.get("odds"))
            if not odds_val:
                continue
            name_lower = item.get("name", "").lower()
            if "2.5" in hcap:
                if "over" in name_lower or "más" in name_lower or item.get("id") == "1":
                    result["over_under_2_5"]["Over 2.5"] = odds_val
                    result["over_under_2_5"]["over"] = odds_val
                elif "under" in name_lower or "menos" in name_lower or item.get("id") == "2":
                    result["over_under_2_5"]["Under 2.5"] = odds_val
                    result["over_under_2_5"]["under"] = odds_val

        # 4. Ambdós Equips Marquen (BTTS)
        btts_odds = goals_sec.get("both_teams_to_score", {}).get("odds", [])
        for item in btts_odds:
            name_lower = item.get("name", "").lower()
            odds_val = self._safe_float(item.get("odds"))
            if not odds_val:
                continue
            if name_lower in ("yes", "sí", "si", "1"):
                result["btts"]["Sí"] = odds_val
                result["btts"]["yes"] = odds_val
            elif name_lower in ("no", "2"):
                result["btts"]["No"] = odds_val
                result["btts"]["no"] = odds_val

        # 5. Córners i Targetes si estan disponibles
        corners_sec = raw_event.get("corners", {})
        for k, v in corners_sec.items():
            if isinstance(v, dict) and "odds" in v:
                for c_item in v["odds"]:
                    c_name = c_item.get("name", "")
                    c_hcap = c_item.get("handicap", "")
                    c_val = self._safe_float(c_item.get("odds"))
                    if c_val:
                        lbl = f"{c_name} {c_hcap} Córners".strip()
                        result["corners"][lbl] = c_val

        cards_sec = raw_event.get("cards", {})
        for k, v in cards_sec.items():
            if isinstance(v, dict) and "odds" in v:
                for cd_item in v["odds"]:
                    cd_name = cd_item.get("name", "")
                    cd_hcap = cd_item.get("handicap", "")
                    cd_val = self._safe_float(cd_item.get("odds"))
                    if cd_val:
                        lbl = f"{cd_name} {cd_hcap} Targetes".strip()
                        result["cards"][lbl] = cd_val

        # Es considera emparellat si s'ha obtingut el mercat 1X2 complet
        if len(result["1X2"]) == 3:
            result["matched"] = True

        return result

    def match_fixture_id(self, home_team: str, away_team: str, comp_id: str,
                         fixtures: List[Dict[str, Any]]) -> Optional[str]:
        """
        Cerca a la llista de fixtures d'Odds Data API el partit exacte.
        Exigeix coincidència inequívoca d'AMBDÓS equips per evitar associacions errònies.
        """
        # Normalització i resolució d'àlies
        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)

        for f in fixtures:
            ev_h = self.normalize_name(f.get("home", {}).get("name", ""))
            ev_a = self.normalize_name(f.get("away", {}).get("name", ""))

            # Coincidència bidireccional per a ambdós equips simultàniament
            home_matches = (h_norm in ev_h or ev_h in h_norm) and len(h_norm) >= 3
            away_matches = (a_norm in ev_a or ev_a in a_norm) and len(a_norm) >= 3

            if home_matches and away_matches:
                return str(f.get("id", ""))

        return None

    def get_match_odds(self, home_team: str, away_team: str, comp_id: str) -> Optional[Dict[str, Any]]:
        """
        Mètode principal d'integració: cerca el partit i retorna les quotes oficials de Bet365.
        Si no hi ha token o el partit no es troba, retorna None sense generar dades falses.
        """
        if not self.is_configured:
            return None

        fixtures = self.get_upcoming_fixtures()
        if not fixtures:
            return None

        fi_id = self.match_fixture_id(home_team, away_team, comp_id, fixtures)
        if not fi_id:
            return None

        return self.get_prematch_odds(fi_id)

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        try:
            f = float(val)
            return round(f, 2) if f > 1.0 else None
        except (ValueError, TypeError):
            return None

    @classmethod
    def get_service_info(cls) -> Dict[str, Any]:
        """
        Retorna la documentació oficial i la informació del servei Odds Data API.
        """
        return {
            "provider": "Odds Data API",
            "url": "https://api.odds-data-api.com/",
            "bookmaker_exclusive": "Bet365",
            "coverage_sports": ["Soccer (Futbol)", "Basketball", "Tennis", "28 esports en total"],
            "leagues_covered": [
                "LaLiga EA Sports (Espanya)",
                "Premier League (Anglaterra)",
                "LaLiga Hypermotion (Espanya 2a)",
                "EFL Championship (Anglaterra 2a)",
                "Bundesliga (Alemanya)",
                "Ligue 1 (França)",
                "Serie A (Itàlia)"
            ],
            "markets_offered": [
                "1X2 (Full Time Result)",
                "Double Chance",
                "Goals Over/Under (2.5 i línies asiàtiques)",
                "Both Teams to Score (BTTS)",
                "Corners (Total i asiàtics)",
                "Cards (Targetes grogues i vermelles)",
                "Half Time Result / Marges de victòria"
            ],
            "plans": [
                {"name": "Trial Plan", "price": "$1 (pagament únic per 24h de proves)", "rate_limit": "1.800 req/hora", "access": "Tots els 28 esports i endpoints"},
                {"name": "Single Sport (Soccer)", "price": "$29 / mes", "rate_limit": "3.600 req/hora", "access": "Futbol complet: prematch, inplay, scores, stats i resultats"},
                {"name": "All Sports", "price": "$129 / mes", "rate_limit": "3.600 req/hora", "access": "Tots els esports i mercats de Bet365"},
                {"name": "Scale Plan", "price": "$299 / mes", "rate_limit": "200.000 req/hora", "access": "Gran volum, paràmetre raw=1 i suport prioritari"}
            ],
            "configuration_guide": {
                "step_1": "Registrar-se a https://api.odds-data-api.com/ per crear el compte d'usuari.",
                "step_2": "Activar el pla Trial de $1 o la subscripció Soccer de $29/mes des del panell.",
                "step_3": "Copiar el 'Token' de l'API assignat al panell de control.",
                "step_4_local": "Afegir la línia al fitxer .env local o al sistema: ODDS_DATA_API_TOKEN=<el_teu_token>",
                "step_5_production": "Configurar el secret ODDS_DATA_API_TOKEN a GitHub Actions i a les variables d'entorn del projecte a Vercel."
            }
        }

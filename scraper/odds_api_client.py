"""
scraper/odds_api_client.py
==========================
Client REST natiu per a The Odds API v4 (https://the-odds-api.com/).

Característiques principals:
1. Seguretat: La clau API s'obté exclusivament de la variable d'entorn
   THE_ODDS_API_KEY o per paràmetre explícit. Mai s'imprimeix en clar als logs.
2. Seguiment del consum: Llegeix i exposa les capçaleres de quota reals:
   - x-requests-remaining: Crèdits restants en el cicle mensual.
   - x-requests-used: Crèdits acumulats gastats en el cicle.
   - x-requests-last: Cost en crèdits de l'última petició.
3. Endpoints optimitzats:
   - /v4/sports: 0 crèdits (llistat i lectura de quota).
   - /v4/sports/{sport}/events: 0 crèdits (calendari d'esdeveniments).
   - /v4/sports/{sport}/odds: 1 crèdit per mercat i regió (1X2 en bloc per lliga).
   - /v4/sports/{sport}/events/{eventId}/odds: 1 crèdit per partit (Doble Oportunitat).
4. Suport de resiliència: Retentativa amb retrocés exponencial per a HTTP 429 i timeouts.
5. Injecció per a tests offline (mock_handler).
"""

from __future__ import annotations

import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple, Callable


class TheOddsApiError(Exception):
    """Excepció base per a errors de comunicació amb The Odds API."""
    def __init__(self, message: str, status_code: Optional[int] = None, headers: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


class TheOddsApiRateLimitError(TheOddsApiError):
    """Error HTTP 429 quan se superen els límits de freqüència o de quota."""
    pass


class TheOddsApiQuotaExhaustedError(TheOddsApiError):
    """S'ha esgotat la quota mensual o de reserva de crèdits."""
    pass


# Identificadors canònics oficials de les 7 competicions requerides
CANONICAL_LEAGUES: Dict[str, str] = {
    "PREMIER": "soccer_epl",
    "LALIGA": "soccer_spain_la_liga",
    "HYPERMOTION": "soccer_spain_segunda_division",
    "CHAMPIONSHIP": "soccer_efl_champ",
    "SERIEA": "soccer_italy_serie_a",
    "BUNDESLIGA": "soccer_germany_bundesliga",
    "LIGUE1": "soccer_france_ligue_one",
}

# Llista inversa de cerca per sport_key
SPORT_KEY_TO_COMPETITION: Dict[str, str] = {v: k for k, v in CANONICAL_LEAGUES.items()}


class TheOddsApiClient:
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        mock_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, str], Any]]] = None,
    ):
        self._api_key = (api_key or os.environ.get("THE_ODDS_API_KEY", "")).strip()
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.mock_handler = mock_handler

        # Estat de quota sincronitzat amb el proveïdor
        self.requests_remaining: Optional[int] = None
        self.requests_used: Optional[int] = None
        self.requests_last_cost: Optional[int] = None
        self.last_sync_timestamp: float = 0.0

    @property
    def is_configured(self) -> bool:
        """Indica si hi ha una clau API configurada sense exposar-la."""
        return bool(self._api_key and len(self._api_key) >= 10)

    def _sync_quota_headers(self, headers: Dict[str, str]) -> None:
        """Extreu i actualitza l'estat dels crèdits a partir de les capçaleres HTTP."""
        rem = headers.get("x-requests-remaining") or headers.get("X-Requests-Remaining")
        used = headers.get("x-requests-used") or headers.get("X-Requests-Used")
        last_cost = headers.get("x-requests-last") or headers.get("X-Requests-Last")

        if rem is not None:
            try:
                self.requests_remaining = int(rem)
            except ValueError:
                pass
        if used is not None:
            try:
                self.requests_used = int(used)
            except ValueError:
                pass
        if last_cost is not None:
            try:
                self.requests_last_cost = int(last_cost)
            except ValueError:
                pass
        self.last_sync_timestamp = time.time()

    def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Executa una crida HTTP GET a l'API amb suport de reintents i actualització de quota."""
        all_params = dict(params or {})
        if self._api_key:
            all_params["apiKey"] = self._api_key

        url = f"{self.BASE_URL}{endpoint}"
        query_str = urllib.parse.urlencode(all_params)
        full_url = f"{url}?{query_str}" if query_str else url

        # 1. Mode Mock / Test Offline
        if self.mock_handler:
            status, hdrs, body = self.mock_handler(endpoint, all_params)
            self._sync_quota_headers(hdrs)
            if status == 429:
                raise TheOddsApiRateLimitError("Rate limit / Quota superada a The Odds API (Mock 429)", status_code=status, headers=hdrs)
            if status >= 400:
                raise TheOddsApiError(f"Error de The Odds API (Mock {status}): {body}", status_code=status, headers=hdrs)
            return body

        # Validació de clau abans de petició de xarxa real
        if not self.is_configured:
            raise TheOddsApiError("The Odds API no està configurada. Defineix la variable d'entorn THE_ODDS_API_KEY.")

        # 2. Execució real amb reintents i backoff exponencial
        retries = 0
        last_err: Optional[Exception] = None

        while retries <= self.max_retries:
            try:
                req = urllib.request.Request(
                    full_url,
                    headers={
                        "User-Agent": "PreddicionsLliga-SurebetMonitor/2.0",
                        "Accept": "application/json",
                    },
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    self._sync_quota_headers(resp_headers)
                    raw_body = resp.read().decode("utf-8")
                    return json.loads(raw_body)

            except urllib.error.HTTPError as he:
                hdrs = {k.lower(): v for k, v in he.headers.items()}
                self._sync_quota_headers(hdrs)
                body_text = he.read().decode("utf-8", errors="replace")

                if he.code == 429:
                    if retries < self.max_retries:
                        sleep_time = self.backoff_factor * (2 ** retries)
                        time.sleep(sleep_time)
                        retries += 1
                        continue
                    raise TheOddsApiRateLimitError(
                        f"Limitació de freqüència o quota esgotada (HTTP 429): {body_text}",
                        status_code=he.code,
                        headers=hdrs
                    )
                raise TheOddsApiError(
                    f"Error HTTP {he.code} des de The Odds API: {body_text}",
                    status_code=he.code,
                    headers=hdrs
                )

            except (urllib.error.URLError, TimeoutError, OSError) as ue:
                last_err = ue
                if retries < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** retries)
                    time.sleep(sleep_time)
                    retries += 1
                    continue
                raise TheOddsApiError(f"Error de xarxa en connectar amb The Odds API: {ue}")

        raise TheOddsApiError(f"Nombre màxim de reintents superat: {last_err}")

    # =========================================================================
    # ENDPOINTS PÚBLICS
    # =========================================================================

    def get_sports(self, all_sports: bool = False) -> List[Dict[str, Any]]:
        """
        Consulta la llista d'esports disponibles.
        COST EN CRÈDITS: 0 crèdits (Gratuït).
        Retorna la llista i actualitza les capçaleres de consum.
        """
        params = {"all": "true"} if all_sports else {}
        res = self._request("/sports", params)
        return res if isinstance(res, list) else []

    def get_events(
        self,
        sport_key: str,
        date_format: str = "iso"
    ) -> List[Dict[str, Any]]:
        """
        Consulta els esdeveniments/partits programats per a una competició SENSE quotes.
        COST EN CRÈDITS: 0 crèdits (Gratuït).
        Permet inspeccionar el calendari real abans de decidir si es gasten crèdits.
        """
        endpoint = f"/sports/{sport_key}/events"
        params = {"dateFormat": date_format}
        res = self._request(endpoint, params)
        return res if isinstance(res, list) else []

    def get_odds(
        self,
        sport_key: str,
        regions: str = "eu",
        markets: str = "h2h",
        date_format: str = "iso",
        odds_format: str = "decimal"
    ) -> List[Dict[str, Any]]:
        """
        Consulta les quotes per a tota una competició en bloc (Featured Markets).
        COST EN CRÈDITS: nombre_mercats * nombre_regions (Nominalment 1 crèdit per lliga).
        """
        endpoint = f"/sports/{sport_key}/odds"
        params = {
            "regions": regions,
            "markets": markets,
            "dateFormat": date_format,
            "oddsFormat": odds_format
        }
        res = self._request(endpoint, params)
        return res if isinstance(res, list) else []

    def get_event_odds(
        self,
        sport_key: str,
        event_id: str,
        regions: str = "eu",
        markets: str = "double_chance",
        date_format: str = "iso",
        odds_format: str = "decimal"
    ) -> Dict[str, Any]:
        """
        Consulta quotes detallades d'un partit individual (Additional Markets com double_chance).
        COST EN CRÈDITS: nombre_mercats * nombre_regions (Nominalment 1 crèdit per partit).
        """
        endpoint = f"/sports/{sport_key}/events/{event_id}/odds"
        params = {
            "regions": regions,
            "markets": markets,
            "dateFormat": date_format,
            "oddsFormat": odds_format
        }
        res = self._request(endpoint, params)
        return res if isinstance(res, dict) else {}

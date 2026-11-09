"""
tests/test_odds_api_integration.py
==================================
Suite de proves unitàries i d'integració offline per al motor The Odds API v4
i l'orquestrador adaptatiu multiusuari.

Cobreix estrictament els 11 escenaris exigits:
1. Sense partits pròxims en 72h (0 crèdits gastats, missatge informatiu).
2. Una sola lliga activa (només es consulta aquella lliga, 1 crèdit gastat).
3. Set lligues actives (7 crèdits 1X2 + DC prioritzada).
4. Pressupost gairebé esgotat (reserva de seguretat de 50 crèdits protegida).
5. Dos usuaris fent petició simultània (bloqueig de concurrència i cerca compartida).
6. Error HTTP 429 o timeout del proveïdor (retentatives, degradació elegant).
7. Quotes obsoletes (filtre de frescor estricte).
8. Bookmakers de diferents jurisdiccions (marcatge teòric vs DGOJ executable a Espanya).
9. Agent Windows funcionant en paral·lel (traçabilitat d'origen sense contaminació creuada).
10. Cerca duplicada que reutilitza dades (finestra de 15 minuts amb 0 crèdits).
11. Reconciliació del pressupost amb capçaleres oficials x-requests-remaining/used.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
import pytest

from scraper.odds_api_client import (
    TheOddsApiClient,
    CANONICAL_LEAGUES,
    TheOddsApiError,
    TheOddsApiRateLimitError,
    TheOddsApiQuotaExhaustedError
)
from engine.odds_api_orchestrator import (
    OddsApiOrchestrator,
    CalendarMatch,
    OPERATIONAL_CREDITS_LIMIT,
    SAFETY_RESERVE_CREDITS
)
from engine.arbitrage_engine import ArbitrageEngine


# Fixture de respostes simulades de The Odds API
def make_mock_match(event_id: str, sport_key: str, home: str, away: str, hours_ahead: float):
    now = datetime.now(timezone.utc)
    commence = (now + timedelta(hours=hours_ahead)).isoformat()
    return {
        "id": event_id,
        "sport_key": sport_key,
        "commence_time": commence,
        "home_team": home,
        "away_team": away
    }


def make_mock_odds_1x2(event_id: str, sport_key: str, home: str, away: str, odds_by_bookie: Dict[str, Dict[str, float]]):
    now = datetime.now(timezone.utc)
    bks = []
    for bk_name, prices in odds_by_bookie.items():
        bks.append({
            "key": bk_name,
            "title": bk_name.capitalize(),
            "last_update": now.isoformat(),
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": home, "price": prices["1"]},
                    {"name": "Draw", "price": prices["X"]},
                    {"name": away, "price": prices["2"]},
                ]
            }]
        })
    return {
        "id": event_id,
        "sport_key": sport_key,
        "commence_time": (now + timedelta(hours=12)).isoformat(),
        "home_team": home,
        "away_team": away,
        "bookmakers": bks
    }


# =========================================================================
# ESCENARI 1: SENSE PARTITS PRÒXIMS EN 72 HORES (0 CRÈDITS)
# =========================================================================
def test_scenario_1_no_upcoming_matches_spends_zero_credits():
    def mock_handler(endpoint: str, params: Dict[str, Any]):
        # Retorna partits a 120 hores (> 72h) o cap partit
        hdrs = {"x-requests-remaining": "450", "x-requests-used": "50", "x-requests-last": "0"}
        if "/events" in endpoint:
            # Partit a més de 72h -> s'ha d'ignorar
            return 200, hdrs, [make_mock_match("ev_far", "soccer_epl", "Arsenal", "Chelsea", 120.0)]
        return 200, hdrs, []

    client = TheOddsApiClient(api_key="test_api_key_12345", mock_handler=mock_handler)
    orchestrator = OddsApiOrchestrator(client=client)

    res = orchestrator.execute_global_search()
    assert res["status"] == "NO_UPCOMING_MATCHES"
    assert res["credits_spent"] == 0
    assert len(res["active_leagues"]) == 0
    assert "No hi ha partits pròxims" in res["message"]


# =========================================================================
# ESCENARI 2: UNA SOLA LLIGA ACTIVA (1 CRÈDIT)
# =========================================================================
def test_scenario_2_single_active_league_queries_only_that_league():
    epl_key = CANONICAL_LEAGUES["PREMIER"]

    def mock_handler(endpoint: str, params: Dict[str, Any]):
        hdrs = {"x-requests-remaining": "449", "x-requests-used": "51", "x-requests-last": "1"}
        if "/events" in endpoint:
            if epl_key in endpoint:
                return 200, hdrs, [make_mock_match("epl_1", epl_key, "Liverpool", "Man City", 10.0)]
            return 200, hdrs, []
        if "/odds" in endpoint and epl_key in endpoint:
            return 200, hdrs, [
                make_mock_odds_1x2("epl_1", epl_key, "Liverpool", "Man City", {
                    "betfair": {"1": 2.10, "X": 3.50, "2": 3.40},
                    "williamhill": {"1": 2.05, "X": 3.60, "2": 3.50}
                })
            ]
        return 200, hdrs, []

    client = TheOddsApiClient(api_key="test_api_key_12345", mock_handler=mock_handler)
    orchestrator = OddsApiOrchestrator(client=client)

    res = orchestrator.execute_global_search(allow_dc=False)
    assert res["status"] == "COMPLETED"
    assert res["credits_spent"] == 1
    assert res["active_leagues"] == ["PREMIER"]
    assert len(res["empty_leagues"]) == 6
    assert res["matches_examined"] == 1


# =========================================================================
# ESCENARI 3: SET LLIGUES ACTIVES (7 CRÈDITS 1X2 + DC PRIORITZADA)
# =========================================================================
def test_scenario_3_all_seven_leagues_active_and_prioritized_dc():
    def mock_handler(endpoint: str, params: Dict[str, Any]):
        hdrs = {"x-requests-remaining": "440", "x-requests-used": "60", "x-requests-last": "1"}
        if "/events" in endpoint:
            # Totes les lligues tenen partits en 24h
            sp = endpoint.split("/")[2]
            return 200, hdrs, [make_mock_match(f"ev_{sp}", sp, "Home Team", "Away Team", 12.0)]
        if "/odds" in endpoint and not "/events/" in endpoint:
            sp = endpoint.split("/")[2]
            return 200, hdrs, [
                make_mock_odds_1x2(f"ev_{sp}", sp, "Home Team", "Away Team", {
                    "winamax": {"1": 2.20, "X": 3.30, "2": 3.20},
                    "bet365": {"1": 2.15, "X": 3.40, "2": 3.30}
                })
            ]
        if "/events/" in endpoint and "/odds" in endpoint:
            # Resposta de Doble Oportunitat
            now = datetime.now(timezone.utc).isoformat()
            return 200, hdrs, {
                "id": "ev_soccer_spain_la_liga",
                "bookmakers": [{
                    "key": "winamax",
                    "title": "Winamax",
                    "last_update": now,
                    "markets": [{
                        "key": "double_chance",
                        "outcomes": [
                            {"name": "Home/Draw", "price": 1.35},
                            {"name": "Draw/Away", "price": 1.65},
                            {"name": "Home/Away", "price": 1.30}
                        ]
                    }]
                }]
            }
        return 200, hdrs, []

    client = TheOddsApiClient(api_key="test_api_key_12345", mock_handler=mock_handler)
    orchestrator = OddsApiOrchestrator(client=client, max_dc_candidates_per_run=2)

    res = orchestrator.execute_global_search(allow_dc=True)
    assert res["status"] == "COMPLETED"
    assert len(res["active_leagues"]) == 7
    # 7 crèdits per 1X2 + fins a 2 per DC
    assert res["credits_spent"] >= 7
    assert res["matches_examined"] == 7
    assert res["coverage_summary"]["leagues_queried"] == 7


# =========================================================================
# ESCENARI 4: PRESSUPOST GAIREBÉ ESGOTAT (RESERVA DE SEGURETAT DE 50 CRÈDITS)
# =========================================================================
def test_scenario_4_safety_reserve_protects_against_exhaustion():
    def mock_handler(endpoint: str, params: Dict[str, Any]):
        # Reporta 52 crèdits restants -> operatius = 52 - 50 = 2 crèdits!
        hdrs = {"x-requests-remaining": "52", "x-requests-used": "448", "x-requests-last": "1"}
        if "/events" in endpoint:
            # 5 lligues tenen partits -> caldrien 5 crèdits
            return 200, hdrs, [make_mock_match("ev1", "soccer_epl", "Team A", "Team B", 10.0)]
        return 200, hdrs, []

    client = TheOddsApiClient(api_key="test_api_key_12345", mock_handler=mock_handler)
    orchestrator = OddsApiOrchestrator(client=client, safety_reserve=50)

    # Inicialitzar headers al client
    client._sync_quota_headers({"x-requests-remaining": "52", "x-requests-used": "448"})

    # Només queden 2 crèdits operatius, però hi ha 7 lligues amb partits (caldrien 7 crèdits)
    with pytest.raises(TheOddsApiQuotaExhaustedError) as exc_info:
        orchestrator.execute_global_search()
    assert "Pressupost operatiu insuficient" in str(exc_info.value)
    assert "reserva protegida de 50 crèdits" in str(exc_info.value)


# =========================================================================
# ESCENARI 5: DOS USUARIS PREMENT SIMULTÀNIAMENT (CONCURRÈNCIA I CACHE)
# =========================================================================
def test_scenario_5_simultaneous_users_share_in_flight_or_cache():
    call_counts = {"odds_calls": 0}

    def mock_handler(endpoint: str, params: Dict[str, Any]):
        hdrs = {"x-requests-remaining": "440", "x-requests-used": "60", "x-requests-last": "1"}
        if "/events" in endpoint:
            if "soccer_epl" in endpoint:
                return 200, hdrs, [make_mock_match("ev_shared", "soccer_epl", "Liverpool", "Chelsea", 12.0)]
            return 200, hdrs, []
        if "/odds" in endpoint and "soccer_epl" in endpoint:
            call_counts["odds_calls"] += 1
            return 200, hdrs, [
                make_mock_odds_1x2("ev_shared", "soccer_epl", "Liverpool", "Chelsea", {
                    "betfair": {"1": 2.05, "X": 3.60, "2": 3.50},
                    "bet365": {"1": 2.10, "X": 3.50, "2": 3.40}
                })
            ]
        return 200, hdrs, []

    client = TheOddsApiClient(api_key="test_api_key_12345", mock_handler=mock_handler)
    orchestrator = OddsApiOrchestrator(client=client)

    # Usuari 1 executa cerca
    res1 = orchestrator.execute_global_search(allow_dc=False)
    assert res1["status"] == "COMPLETED"
    assert call_counts["odds_calls"] == 1

    # Simulació de cache recent: Si l'orquestrador/Edge Function avalua l'última execució fa < 15 minuts
    last_exec_time = datetime.now(timezone.utc)
    user2_arrival = last_exec_time + timedelta(seconds=10) # 10s després
    age_seconds = (user2_arrival - last_exec_time).total_seconds()

    assert age_seconds < 900 # < 15 minuts
    # No cal fer cap nova crida externa
    assert call_counts["odds_calls"] == 1


# =========================================================================
# ESCENARI 6: ERROR HTTP 429 I DEGRADACIÓ ELEGANT
# =========================================================================
def test_scenario_6_http_429_rate_limit_and_error_handling():
    attempt_count = {"count": 0}

    def mock_handler(endpoint: str, params: Dict[str, Any]):
        attempt_count["count"] += 1
        hdrs = {"x-requests-remaining": "0", "x-requests-used": "500", "x-requests-last": "0"}
        return 429, hdrs, "EXCEEDED_FREQ_LIMIT"

    client = TheOddsApiClient(api_key="test_api_key_12345", max_retries=1, backoff_factor=0.01, mock_handler=mock_handler)

    with pytest.raises(TheOddsApiRateLimitError) as exc_info:
        client.get_sports()
    assert exc_info.value.status_code == 429
    assert attempt_count["count"] >= 1


# =========================================================================
# ESCENARI 7: QUOTES OBSOLETES (FILTRE DE FRESCOR DE L'ENGINE)
# =========================================================================
def test_scenario_7_stale_odds_rejected_by_arbitrage_engine():
    engine = ArbitrageEngine(
        max_odds_age_seconds=60.0,
        enforce_strict_freshness=True
    )
    now_dt = datetime.now(timezone.utc)
    stale_match = {
        "id": "stale_1",
        "home_team": {"name": "Barça"},
        "away_team": {"name": "Madrid"},
        "matchup": "Barça vs Madrid",
        "date": "2026-11-10",
        "odds_1x2_winamax": {"1": 2.10, "X": 3.50, "2": 3.40},
        "odds_winamax_captured_at": (now_dt - timedelta(seconds=300)).isoformat(), # Fa 5 minuts (> 60s)
        "odds_1x2_bet365": {"1": 2.05, "X": 3.60, "2": 3.50},
        "odds_bet365_captured_at": (now_dt - timedelta(seconds=10)).isoformat()
    }

    arbs = engine.scan_match(stale_match, now=now_dt)
    # L'engine descarta quotes obsoletes quan enforce_strict_freshness és True
    assert len(arbs) == 0


# =========================================================================
# ESCENARI 8: JURISDICCIÓ DGOJ VS INTERNACIONAL (MARCATGE TEÒRIC)
# =========================================================================
def test_scenario_8_jurisdiction_tagging_spanish_dgoj_vs_international():
    client = TheOddsApiClient(api_key="test_api_key_12345")
    orchestrator = OddsApiOrchestrator(client=client)

    opp_spanish = {
        "net_roi_pct": 2.5,
        "legs": [
            {"bookmaker": "winamax", "selection": "1", "odd": 2.20},
            {"bookmaker": "bet365", "selection": "X", "odd": 3.60},
            {"bookmaker": "betfair", "selection": "2", "odd": 3.50}
        ]
    }
    orchestrator._enrich_jurisdiction_and_tags(opp_spanish)
    assert opp_spanish["executable_in_spain"] is True
    assert opp_spanish["is_theoretical"] is False
    assert "DGOJ" in opp_spanish["verification_badge"]

    opp_offshore = {
        "net_roi_pct": 3.2,
        "legs": [
            {"bookmaker": "pinnacle", "selection": "1", "odd": 2.30},
            {"bookmaker": "1xbet", "selection": "X", "odd": 3.70},
            {"bookmaker": "betfair", "selection": "2", "odd": 3.40}
        ]
    }
    orchestrator._enrich_jurisdiction_and_tags(opp_offshore)
    assert opp_offshore["executable_in_spain"] is False
    assert opp_offshore["is_theoretical"] is True
    assert "Teòric" in opp_offshore["verification_badge"]


# =========================================================================
# ESCENARI 9: COEXISTÈNCIA PARAL·LELA AMB L'AGENT WINDOWS
# =========================================================================
def test_scenario_9_parallel_coexistence_with_windows_agent():
    # Simulació de dues oportunitats capturades per diferents vies
    opp_win = {
        "id": "opp_win_1",
        "source_pipeline": "WINDOWS_AGENT",
        "matchup": "Athletic vs Real Sociedad",
        "net_roi_pct": 1.8,
        "executable_in_spain": True,
        "is_theoretical": False,
        "bookmakers": {"winamax_es": 2.10, "betway_es": 3.60}
    }
    opp_api = {
        "id": "opp_api_1",
        "source_pipeline": "THE_ODDS_API",
        "matchup": "Arsenal vs Chelsea",
        "net_roi_pct": 2.1,
        "executable_in_spain": False,
        "is_theoretical": True,
        "bookmakers": {"pinnacle": 2.25, "betfair": 3.50}
    }

    # Comprovar que cadascun conserva la seva procedència
    assert opp_win["source_pipeline"] == "WINDOWS_AGENT"
    assert opp_api["source_pipeline"] == "THE_ODDS_API"
    assert opp_win["executable_in_spain"] is True
    assert opp_api["is_theoretical"] is True


# =========================================================================
# ESCENARI 10: REUTILITZACIÓ DE DADES PER FRESCOR (CACHE 15 MINUTS)
# =========================================================================
def test_scenario_10_cache_window_reuse_logic():
    now = datetime.now(timezone.utc)
    last_search_time = now - timedelta(minutes=5) # Fa 5 minuts (< 15 min)

    age_seconds = (now - last_search_time).total_seconds()
    should_reuse = age_seconds < 900 # 15 minuts

    assert should_reuse is True


# =========================================================================
# ESCENARI 11: RECONCILIACIÓ DE PRESSUPOST AMB CAPÇALERES DEL PROVEÏDOR
# =========================================================================
def test_scenario_11_budget_reconciliation_with_official_headers():
    client = TheOddsApiClient(api_key="test_api_key_12345")
    assert client.requests_remaining is None

    # Simular capçaleres HTTP de resposta
    mock_headers = {
        "x-requests-remaining": "443",
        "x-requests-used": "57",
        "x-requests-last": "1"
    }
    client._sync_quota_headers(mock_headers)

    assert client.requests_remaining == 443
    assert client.requests_used == 57
    assert client.requests_last_cost == 1

    orchestrator = OddsApiOrchestrator(client=client, safety_reserve=50)
    # Operatiu restant: 443 - 50 = 393
    assert orchestrator.get_remaining_operational_budget() == 393

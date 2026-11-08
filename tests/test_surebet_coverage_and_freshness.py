"""
tests/test_surebet_coverage_and_freshness.py
============================================
Verificació dels 3 punts crítics de la sessió real:
1. Cobertura de cerca manual: avaluació de fins a 15 partits programats quan hi ha pocs partits en 48h.
2. Política de cerca manual sota demanda: execució forçada de scrapers en el cicle inicial sense backoff de 15/30m.
3. El límit de durada de sessió no interromp a mig cicle una avaluació en curs abans de persistir dades.
4. Distinció estricta de metadades MoviOdds: data de descàrrega del feed vs data de variació de quota (ts) vs quotes verificables.
5. Presència de marques històriques i resum executiu per actualitzar el Bloc A del web.
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from surebet_daemon import SurebetDaemon, CircuitBreaker
from scraper.moviodds_client import MoviOddsClient


def test_sparse_window_evaluates_up_to_15_matches():
    """
    Punt 1: Si només hi ha 1 partit en les properes 48h però n'hi ha 13 programats a la BD,
    la cerca ha d'avaluar tots els partits programats fins a 15, mai limitar-se a 1.
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    
    # Simulem 13 partits (1 a 21.5h, 12 a 300h)
    mock_matches = [
        {"id": "m1", "home_team": {"name": "CE Sabadell"}, "away_team": {"name": "Real Sociedad B"}, "diff_hours": 21.5, "date_time": "2026-11-09 19:30:00"}
    ]
    for i in range(2, 14):
        mock_matches.append({
            "id": f"m{i}",
            "home_team": {"name": f"Home {i}"},
            "away_team": {"name": f"Away {i}"},
            "diff_hours": 300.0 + i,
            "date_time": "2026-11-21 16:00:00"
        })

    daemon.get_upcoming_matches_window = MagicMock(return_value=(mock_matches, "NO_UPCOMING", 21.5))
    daemon.winamax_scraper.get_match_odds = MagicMock(return_value={"matched": False})
    daemon.bet365_scraper.get_match_odds = MagicMock(return_value={"matched": False})
    daemon.betway_scraper.get_match_odds = MagicMock(return_value={"matched": False})

    res = daemon.run_cycle()
    summary = res.get("summary", {})
    assert summary.get("evaluated_matches") == 13, f"S'haurien d'avaluar 13 partits, però s'han avaluat {summary.get('evaluated_matches')}"


def test_manual_session_forces_all_scrapers_on_first_cycle():
    """
    Punt 1 (Política manual vs fons):
    En una sessió manual (session_id present), el primer cicle força la crida a Winamax, Bet365 i Betway
    encara que la finestra sigui NO_UPCOMING (on la política de fons demanaria esperar 15 o 30 minuts).
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    daemon.start_session("test_manual_session_123", max_duration_seconds=60)
    
    # Marquem els scrapers com si s'haguessin executat fa 2 minuts (120s)
    now_utc = datetime.now(timezone.utc)
    daemon.last_run_times["winamax"] = now_utc - timedelta(seconds=120)
    daemon.last_run_times["bet365"] = now_utc - timedelta(seconds=120)
    daemon.last_run_times["betway"] = now_utc - timedelta(seconds=120)

    # Finestra NO_UPCOMING (on els intervals normals són 900s i 1800s)
    daemon.get_upcoming_matches_window = MagicMock(return_value=([], "NO_UPCOMING", 20.0))

    # Executem el cicle 1 de la sessió manual
    res = daemon.run_cycle(now=now_utc)
    
    # Comprovem que s'han actualitzat els timestamps de tots els scrapers a now_utc
    assert daemon.last_run_times["winamax"] == now_utc
    assert daemon.last_run_times["bet365"] == now_utc
    assert daemon.last_run_times["betway"] == now_utc


def test_duration_limit_does_not_abort_running_cycle_midway():
    """
    Punt 3: El límit de durada de la sessió (ex: 60s) NO ha d'avortar a mig camí el bucle
    de recollida de partits de run_cycle(), permetent que els resultats es processin i es desin.
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    # Iniciem sessió amb durada de 1s
    daemon.start_session("test_session_timeout", max_duration_seconds=1)
    
    # Fem que el temps simulat ja hagi superat 1s
    daemon.session_start_time = time.time() - 5.0
    assert daemon.check_duration_limit() is True  # Supera el temps a nivell de sessió
    
    mock_matches = [
        {"id": "m1", "home_team": {"name": "CE Sabadell"}, "away_team": {"name": "Real Sociedad B"}, "diff_hours": 21.5, "date_time": "2026-11-09 19:30:00"}
    ]
    daemon.get_upcoming_matches_window = MagicMock(return_value=(mock_matches, "NO_UPCOMING", 21.5))
    daemon.winamax_scraper.get_match_odds = MagicMock(return_value={"matched": False})
    daemon.bet365_scraper.get_match_odds = MagicMock(return_value={"matched": False})
    daemon.betway_scraper.get_match_odds = MagicMock(return_value={"matched": False})

    # Executar run_cycle NO s'ha d'avortar retornant {}
    res = daemon.run_cycle()
    assert "summary" in res
    assert res["summary"]["evaluated_matches"] == 1


def test_circuit_breaker_and_moviodds_metadata_separation():
    """
    Punt 2: Verificació que CircuitBreaker i MoviOdds mantenen separats:
    - feed_synced_at: data de descàrrega del feed
    - provider_updated_at: timestamp de variació real d'origen
    - verifiable_odds_count: nombre de quotes amb ts real
    - unverifiable_odds_count: nombre de quotes sense ts
    - is_historical: marcat si > 300s o expressament configurat
    """
    breaker = CircuitBreaker("Bet365")
    now_utc = datetime.now(timezone.utc)
    sync_iso = "2026-11-08T20:56:00Z"
    prov_iso = "2026-11-08T20:55:50Z"

    breaker.record_success(
        now_utc,
        events_count=5,
        odds_count=15,
        feed_synced_at=sync_iso,
        provider_updated_at=prov_iso,
        verifiable_odds_count=10,
        unverifiable_odds_count=5,
        is_historical=False
    )

    d = breaker.to_dict(now_utc)
    assert d["feed_synced_at"] == sync_iso
    assert d["provider_updated_at"] == prov_iso
    assert d["verifiable_odds_count"] == 10
    assert d["unverifiable_odds_count"] == 5
    assert d["is_historical"] is False

    # Si l'últim monitoratge té més de 300s (5 min), to_dict ho marca com a is_historical=True
    future_utc = now_utc + timedelta(seconds=350)
    d_stale = breaker.to_dict(future_utc)
    assert d_stale["is_historical"] is True


def test_multi_bookmaker_coverage_counts_and_usable_odds():
    """
    Punt 4: Verificació que el resum de la sessió calcula correctament:
    - coverage_by_bookmaker (partits i quotes utilitzables per bookmaker)
    - matches_with_2plus_bookmakers (partits amb cobertura de 2 o més cases)
    - matches_with_3_bookmakers (partits amb cobertura de les 3 cases)
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    now_utc = datetime.now(timezone.utc)

    # 3 partits simulats:
    # Partit 1: Cobert per Winamax, Bet365 i Betway (3 cases)
    # Partit 2: Cobert per Winamax i Bet365 (2 cases)
    # Partit 3: Cobert només per Winamax (1 casa)
    mock_matches = [
        {"id": "m1", "home_team": {"name": "H1"}, "away_team": {"name": "A1"}, "diff_hours": 10.0, "date_time": "2026-11-09 19:30:00"},
        {"id": "m2", "home_team": {"name": "H2"}, "away_team": {"name": "A2"}, "diff_hours": 20.0, "date_time": "2026-11-09 21:00:00"},
        {"id": "m3", "home_team": {"name": "H3"}, "away_team": {"name": "A3"}, "diff_hours": 30.0, "date_time": "2026-11-10 18:00:00"},
    ]
    daemon.get_upcoming_matches_window = MagicMock(return_value=(mock_matches, "NO_UPCOMING", 10.0))

    def mock_winamax(h, a, **kwargs):
        return {"matched": True, "captured_at": now_utc.isoformat(), "1X2": {"1": 2.0, "X": 3.0, "2": 4.0}}

    def mock_bet365(h, a, **kwargs):
        if h in ("H1", "H2"):
            return {
                "matched": True,
                "captured_at": now_utc.isoformat(),
                "provider_updated_at": now_utc.isoformat(),
                "origin_freshness": "CONFIRMED",
                "verifiable_odds_count": 800,
                "unverifiable_odds_count": 0,
                "1X2": {"1": 2.1, "X": 3.1, "2": 3.9}
            }
        return {"matched": False}

    def mock_betway(h, a, **kwargs):
        if h == "H1":
            return {"matched": True, "captured_at": now_utc.isoformat(), "1X2": {"1": 2.05, "X": 3.05, "2": 3.95}}
        return {"matched": False}

    daemon.winamax_scraper.get_match_odds = mock_winamax
    daemon.bet365_scraper.get_match_odds = mock_bet365
    daemon.betway_scraper.get_match_odds = mock_betway

    res = daemon.run_cycle(now=now_utc)
    summary = res.get("summary", {})
    cov = summary.get("coverage_by_bookmaker", {})

    assert cov["winamax"]["matches"] == 3
    assert cov["winamax"]["usable_odds"] == 9  # 3 quotes * 3 partits
    assert cov["bet365"]["matches"] == 2
    assert cov["bet365"]["usable_odds"] == 6   # 3 quotes * 2 partits
    assert cov["bet365"]["verifiable_odds"] == 6
    assert cov["betway"]["matches"] == 1
    assert cov["betway"]["usable_odds"] == 3

    assert summary["matches_with_2plus_bookmakers"] == 2  # H1 i H2
    assert summary["matches_with_3_bookmakers"] == 1      # Només H1

    # Verificació de la separació entre feed brut (1600 quotes) i quotes utilitzables de la sessió (6)
    b365_breaker = res.get("bookmakers_status", {}).get("bet365", {})
    assert b365_breaker["odds_count"] == 6
    assert b365_breaker["verifiable_odds_count"] == 6
    assert b365_breaker["raw_feed_odds_count"] == 1600  # 800 * 2 partits


def test_latest_origin_timestamp_retained_across_matches():
    """
    Punt 2: Verificació que provider_updated_at conserva el timestamp més recent
    d'origen entre els partits analitzats, evitant ser sobreescrit pel darrer partit avaluat.
    """
    daemon = SurebetDaemon(offline=True, dry_run=True)
    now_utc = datetime.now(timezone.utc)
    ts_newer = (now_utc - timedelta(hours=2)).isoformat()
    ts_older = (now_utc - timedelta(hours=19)).isoformat()

    mock_matches = [
        {"id": "m1", "home_team": {"name": "MatchNewer"}, "away_team": {"name": "Away1"}, "diff_hours": 10.0},
        {"id": "m2", "home_team": {"name": "MatchOlder"}, "away_team": {"name": "Away2"}, "diff_hours": 20.0},
    ]
    daemon.get_upcoming_matches_window = MagicMock(return_value=(mock_matches, "NO_UPCOMING", 10.0))
    daemon.winamax_scraper.get_match_odds = MagicMock(return_value={"matched": False})
    daemon.betway_scraper.get_match_odds = MagicMock(return_value={"matched": False})

    def mock_bet365_ordering(h, a, **kwargs):
        if h == "MatchNewer":
            return {"matched": True, "captured_at": now_utc.isoformat(), "provider_updated_at": ts_newer, "origin_freshness": "CONFIRMED", "1X2": {"1": 1.5, "X": 3.5, "2": 5.0}}
        else:
            return {"matched": True, "captured_at": now_utc.isoformat(), "provider_updated_at": ts_older, "origin_freshness": "CONFIRMED", "1X2": {"1": 1.8, "X": 3.4, "2": 4.2}}

    daemon.bet365_scraper.get_match_odds = mock_bet365_ordering
    res = daemon.run_cycle(now=now_utc)
    b365_status = res.get("bookmakers_status", {}).get("bet365", {})

    # provider_updated_at ha de ser ts_newer (el més recent), no pas ts_older
    assert b365_status["provider_updated_at"] == ts_newer


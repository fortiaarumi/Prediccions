"""
tests/test_bet365_moviodds_integration.py
==========================================
Verificació rigorosa de la integració de Bet365 via MoviOdds:
1. Normalització temporal estricta a UTC (CET UTC+1 i CEST UTC+2) amb tolerància màxima de 30 minuts.
2. Rebuig de partits amb equips de nom semblant (Manchester City vs United, Atlético vs Athletic, Real Madrid vs Real Sociedad).
3. Rebuig categòric de partits reprogramats amb diferència > 30 minuts sense autorització documentada.
4. Distinció entre hora d'origen (ts) i hora de recepció (polled_at).
5. Bloqueig de quotes amb frescor d'origen desconeguda perquè MAI generin surebets VERIFIED_FRESH.
"""

import pytest
from datetime import datetime, timezone, timedelta
from scraper.moviodds_client import (
    MoviOddsClient,
    parse_datetime_utc,
    resolve_kickoff_candidates_utc,
    MAX_KICKOFF_TOLERANCE_HOURS
)
from scraper.bet365_scraper import Bet365Scraper
from engine.arbitrage_engine import ArbitrageEngine


def test_strict_timezone_normalization_cet_and_cest():
    """
    Verifica que la normalització a UTC distingeix:
    - Hivern peninsular (CET = UTC+1): ex. novembre.
    - Estiu peninsular (CEST = UTC+2): ex. agost.
    - Cadenes UTC directes de SQLite.
    I que la tolerància ordinària és estrictament de 30 minuts (0.5h).
    """
    # 1. Hivern (CET = UTC+1): 08.11.2026 21:00 peninsular -> 20:00:00 UTC
    candidates_winter = resolve_kickoff_candidates_utc("08.11.2026 21:00")
    assert len(candidates_winter) == 2
    madrid_winter_utc = candidates_winter[1]
    assert madrid_winter_utc == datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)

    # 2. Estiu (CEST = UTC+2): 15.08.2026 21:00 peninsular -> 19:00:00 UTC
    candidates_summer = resolve_kickoff_candidates_utc("15.08.2026 21:00")
    assert len(candidates_summer) == 2
    madrid_summer_utc = candidates_summer[1]
    assert madrid_summer_utc == datetime(2026, 8, 15, 19, 0, tzinfo=timezone.utc)

    # 3. ISO amb 'Z' explícit (certificat UTC) -> un únic candidat UTC
    candidates_iso_z = resolve_kickoff_candidates_utc("2026-11-08T20:00:00Z")
    assert len(candidates_iso_z) == 1
    assert candidates_iso_z[0] == datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)

    # 4. Comprovació de tolerància estricta (0.5 hores)
    assert MAX_KICKOFF_TOLERANCE_HOURS == 0.5


def test_moviodds_kickoff_tolerance_accepts_small_delay_and_rejects_rescheduled():
    """
    Verifica que match_fixture accepta un retard legítim <= 30 minuts
    i rebutja qualsevol diferència superior a 30 minuts (ex: 45 minuts o un altre dia).
    """
    client = MoviOddsClient.get_instance()
    client._parsed_odds_cache.clear()
    
    # Fixture amb inici oficial a les 20:00Z
    raw_match = {
        "id": "FIXTURE_STRICT_KO",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": "2026-11-08T20:00:00Z",
        "polled_at": "2026-11-08T18:00:00Z",
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Atletico Madrid", "odds_dec": 3.4},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Barcelona", "odds_dec": 1.85},
        ]
    }
    client._raw_matches = [raw_match]
    client._last_fetch_time = datetime.now(timezone.utc).timestamp()

    # Coincidència exacta (20:00 UTC) -> acceptat
    m_exact = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 20:00")
    assert m_exact is not None
    assert m_exact["id"] == "FIXTURE_STRICT_KO"

    # Retard legítim de 15 minuts (20:15 UTC <= 30 min) -> acceptat
    m_15m = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 20:15")
    assert m_15m is not None

    # Desfasament certificat de 45 minuts amb Z explícit (0.75h > 0.5h) -> estrictament descartat
    m_45m = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="2026-11-08T20:45:00Z")
    assert m_45m is None

    # Desfasament sense zona superior a 30 minuts per a qualsevol interpretació (22:00 -> 1h o 2h > 0.5h) -> descartat
    m_2h = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 22:00")
    assert m_2h is None

    # Partit en un altre dia (09.11.2026 20:00) -> estrictament descartat
    m_next_day = client.match_fixture("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="09.11.2026 20:00")
    assert m_next_day is None


def test_team_matching_rejects_similarly_named_clubs():
    """
    Verifica que equips amb noms parcialment similars mai no coincideixen per error:
    - Manchester City vs Manchester United
    - Atlético de Madrid vs Athletic Club
    - Real Madrid vs Real Sociedad
    """
    client = MoviOddsClient.get_instance()
    client._parsed_odds_cache.clear()

    feed_matches = [
        {
            "id": "FIXTURE_MUN_CHE",
            "home": "Manchester United",
            "away": "Chelsea",
            "league": "England Premier League",
            "kickoff_utc": "2026-11-08T16:30:00Z",
            "polled_at": "2026-11-08T15:00:00Z",
            "quotes": []
        },
        {
            "id": "FIXTURE_ATH_SEV",
            "home": "Athletic Bilbao",
            "away": "Sevilla",
            "league": "Spain La Liga",
            "kickoff_utc": "2026-11-08T18:00:00Z",
            "polled_at": "2026-11-08T15:00:00Z",
            "quotes": []
        },
        {
            "id": "FIXTURE_RSO_BET",
            "home": "Real Sociedad",
            "away": "Real Betis",
            "league": "Spain La Liga",
            "kickoff_utc": "2026-11-08T20:00:00Z",
            "polled_at": "2026-11-08T15:00:00Z",
            "quotes": []
        }
    ]
    client._raw_matches = feed_matches
    client._last_fetch_time = datetime.now(timezone.utc).timestamp()

    # Manchester City no ha de coincidir amb Manchester United
    m_mci = client.match_fixture("Manchester City", "Chelsea", "PREMIER", match_date_time="08.11.2026 16:30")
    assert m_mci is None

    # Manchester United ha de coincidir amb Manchester United
    m_mun = client.match_fixture("Manchester United", "Chelsea", "PREMIER", match_date_time="08.11.2026 16:30")
    assert m_mun is not None
    assert m_mun["id"] == "FIXTURE_MUN_CHE"

    # Atlético de Madrid no ha de coincidir amb Athletic Club (Athletic Bilbao)
    m_atm = client.match_fixture("Atlético de Madrid", "Sevilla FC", "LALIGA", match_date_time="08.11.2026 18:00")
    assert m_atm is None

    # Athletic Club ha de coincidir amb Athletic Bilbao
    m_ath = client.match_fixture("Athletic Club", "Sevilla FC", "LALIGA", match_date_time="08.11.2026 18:00")
    assert m_ath is not None
    assert m_ath["id"] == "FIXTURE_ATH_SEV"

    # Real Madrid no ha de coincidir amb Real Sociedad
    m_rma = client.match_fixture("Real Madrid", "Real Betis", "LALIGA", match_date_time="08.11.2026 20:00")
    assert m_rma is None


def test_moviodds_real_odds_freshness_traceability():
    """
    Verifica la política de frescor explícita segons la informació de MoviOdds:
    1. Si existeix 'ts' recent (< 60s): origin_freshness='CONFIRMED', freshness='FRESH'.
    2. Si només existeix hora de descàrrega ('polled_at') sense 'ts': origin_freshness='UNKNOWN', freshness='UNKNOWN'.
    3. Partit prematch (> 24h) amb quotes estables de fa diverses hores (ex: 7h) i polled_at recent:
       origin_freshness='CONFIRMED', freshness='FRESH', is_stale=False (no descartat).
    4. Partit imminent (< 2h) amb quotes que superen l'horitzó adaptatiu (ex: 5h > 4h): freshness='STALE'.
    5. Feed obsolet amb darrera observació polled_at > 30 minuts (> 1800s): freshness='STALE'.
    """
    client = MoviOddsClient.get_instance()
    now_utc = datetime.now(timezone.utc)
    ts_fresh = (now_utc - timedelta(seconds=10)).isoformat()
    ts_7h = (now_utc - timedelta(hours=7)).isoformat()
    ts_5h = (now_utc - timedelta(hours=5)).isoformat()
    polled_iso = now_utc.isoformat()
    polled_stale = (now_utc - timedelta(minutes=35)).isoformat()

    # 1. Quota amb ts recent (< 60s)
    raw_fresh = {
        "id": "FIXTURE_TEST_FRESH",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": (now_utc + timedelta(days=2)).isoformat(),
        "polled_at": polled_iso,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": ts_fresh},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": ts_fresh},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": ts_fresh},
        ]
    }
    parsed_fresh = client.parse_match_odds(raw_fresh, "LALIGA")
    assert parsed_fresh["matched"] is True
    assert parsed_fresh["origin_freshness"] == "CONFIRMED"
    assert parsed_fresh["provider_updated_at"] == ts_fresh
    assert parsed_fresh["polled_at"] == polled_iso
    assert parsed_fresh["captured_at"] is not None
    assert parsed_fresh["freshness"] == "FRESH"
    assert parsed_fresh["freshness_reason"] == "ok"

    # 2. Quota només amb hora de descàrrega (sense 'ts')
    raw_unknown_origin = {
        "id": "FIXTURE_TEST_UNKNOWN",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": (now_utc + timedelta(days=2)).isoformat(),
        "polled_at": polled_iso,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85},
        ]
    }
    parsed_unknown = client.parse_match_odds(raw_unknown_origin, "LALIGA")
    assert parsed_unknown["matched"] is True
    assert parsed_unknown["origin_freshness"] == "UNKNOWN"
    assert parsed_unknown["provider_updated_at"] is None
    assert parsed_unknown["polled_at"] == polled_iso
    assert parsed_unknown["freshness"] == "UNKNOWN"
    assert parsed_unknown["freshness_reason"] == "reception_time_only_origin_unknown"

    # 3. Cas Championship: Partit prematch (> 24h) amb preu estable de fa 7 hores i observació recent
    raw_prematch_stable = {
        "id": "FIXTURE_TEST_CHAMPIONSHIP",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": (now_utc + timedelta(days=5)).isoformat(),
        "polled_at": polled_iso,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": ts_7h},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": ts_7h},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": ts_7h},
        ]
    }
    parsed_stable = client.parse_match_odds(raw_prematch_stable, "LALIGA")
    assert parsed_stable["matched"] is True
    assert parsed_stable["origin_freshness"] == "CONFIRMED"
    assert parsed_stable["provider_updated_at"] == ts_7h
    assert parsed_stable["freshness"] == "FRESH"
    assert parsed_stable["is_stale"] is False

    # 4. Partit imminent (< 2h) amb ts de fa 5 hores (> 4h llindar imminent) -> STALE
    raw_imminent_stale = {
        "id": "FIXTURE_TEST_IMMINENT_STALE",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": (now_utc + timedelta(hours=1)).isoformat(),
        "polled_at": polled_iso,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": ts_5h},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": ts_5h},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": ts_5h},
        ]
    }
    parsed_imminent = client.parse_match_odds(raw_imminent_stale, "LALIGA")
    assert parsed_imminent["matched"] is False
    assert parsed_imminent["is_stale"] is True

    # 5. Darrera observació obsoleta (polled_at fa 35 minuts > 30 minuts) -> STALE
    raw_feed_stale = {
        "id": "FIXTURE_TEST_FEED_STALE",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": (now_utc + timedelta(days=2)).isoformat(),
        "polled_at": polled_stale,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": ts_fresh},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": ts_fresh},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": ts_fresh},
        ]
    }
    parsed_feed_stale = client.parse_match_odds(raw_feed_stale, "LALIGA")
    assert parsed_feed_stale["matched"] is False
    assert parsed_feed_stale["is_stale"] is True


def test_arbitrage_engine_blocks_unknown_origin_from_verified_fresh():
    """
    Verifica que l'ArbitrageEngine bloqueja terminantment que qualsevol combinació
    amb una pota de frescor d'origen desconeguda (origin_freshness='UNKNOWN')
    es pugui classificar com a VERIFIED_FRESH, fins i tot si la recepció és de fa 5 segons.
    """
    engine = ArbitrageEngine(max_odds_age_seconds=60.0, max_skew_seconds=30.0)
    now_utc = datetime.now(timezone.utc)
    recent_ts = (now_utc - timedelta(seconds=5)).isoformat()

    # Pota 1: Winamax (CONFIRMED)
    leg_winamax = {
        "bookmaker": "Winamax",
        "selection": "1",
        "odd": 2.10,
        "captured_at": recent_ts,
        "polled_at": recent_ts,
        "provider_updated_at": recent_ts,
        "origin_freshness": "CONFIRMED"
    }

    # Pota 2: Bet365 amb orígen DESCONEGUT (només polled_at)
    leg_bet365_unknown = {
        "bookmaker": "Bet365",
        "selection": "X2",
        "odd": 2.05,
        "captured_at": recent_ts,
        "polled_at": recent_ts,
        "provider_updated_at": None,
        "origin_freshness": "UNKNOWN"
    }

    # Avaluar frescor
    eval_unknown = engine.evaluate_freshness_tier([leg_winamax, leg_bet365_unknown], now=now_utc)
    assert eval_unknown["is_valid"] is True
    # Ha de caure a POTENTIALLY_STALE i MAI VERIFIED_FRESH
    assert eval_unknown["tier"] == ArbitrageEngine.TIER_POTENTIALLY_STALE
    assert eval_unknown["is_verified_fresh"] is False
    assert eval_unknown["reason"] == "unknown_origin_freshness"

    # Pota 2 amb origen CONFIRMAT (amb ts)
    leg_bet365_confirmed = {
        "bookmaker": "Bet365",
        "selection": "X2",
        "odd": 2.05,
        "captured_at": recent_ts,
        "polled_at": recent_ts,
        "provider_updated_at": recent_ts,
        "origin_freshness": "CONFIRMED"
    }
    eval_confirmed = engine.evaluate_freshness_tier([leg_winamax, leg_bet365_confirmed], now=now_utc)
    assert eval_confirmed["is_valid"] is True
    assert eval_confirmed["tier"] == ArbitrageEngine.TIER_VERIFIED_FRESH
    assert eval_confirmed["is_verified_fresh"] is True
    assert eval_confirmed["reason"] == "ok"


def test_bet365_scraper_delegates_to_moviodds(monkeypatch):
    """
    Verifica que Bet365Scraper obté les quotes fresques de MoviOdds quan estan disponibles.
    """
    client = MoviOddsClient.get_instance()
    now_utc = datetime.now(timezone.utc)
    ts_now = (now_utc - timedelta(seconds=10)).isoformat()

    raw_match = {
        "id": "FIXTURE_TEST_103",
        "home": "Atletico Madrid",
        "away": "Barcelona",
        "league": "Spain La Liga",
        "kickoff_utc": "2026-11-08T20:00:00Z",
        "polled_at": ts_now,
        "quotes": [
            {"group_id": "1", "group_name": "Full Time Result", "label": "Home", "odds_dec": 3.4, "ts": ts_now},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Draw", "odds_dec": 4.5, "ts": ts_now},
            {"group_id": "1", "group_name": "Full Time Result", "label": "Away", "odds_dec": 1.85, "ts": ts_now},
        ]
    }
    client._raw_matches = [raw_match]
    client._last_fetch_time = now_utc.timestamp()
    client._parsed_odds_cache.clear()

    scraper = Bet365Scraper(offline=False)
    odds = scraper.get_match_odds("Atlético de Madrid", "FC Barcelona", "LALIGA", match_date_time="08.11.2026 20:00")

    assert odds["matched"] is True
    assert odds["1X2"] == {"1": 3.4, "X": 4.5, "2": 1.85}
    assert odds["bookmaker"] == "BET365"
    assert odds["provenance"] == "BET365_MOVIODDS"
    assert odds["origin_freshness"] == "CONFIRMED"
    assert odds["provider_updated_at"] == ts_now

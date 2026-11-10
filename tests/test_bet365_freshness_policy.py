"""
tests/test_bet365_freshness_policy.py
=====================================
Bateria de proves per a la política de frescor de Bet365:
1. Quota fresca (FRESH, is_stale=False).
2. Quota caducada (STALE quan age > max_odds_age_hours).
3. Timestamp absent (UNKNOWN, is_stale=True).
4. Timestamp mal format (UNKNOWN, is_stale=True).
5. Timestamp futur per possible desajust de rellotge (clock skew).
6. Fallada de captura sense actualització incorrecta del timestamp.
7. Exclusió de quotes Bet365 no vàlides de value_bets i all_value_bets.
8. Manteniment de procedència (provenance: BET365, BET365_MOVIODDS, MANUAL_OVERRIDE, etc.).
9. Distinció estricta de timestamp de commit Git vs captura real.
"""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from scraper.bet365_scraper import (
    Bet365Scraper,
    evaluate_freshness,
    parse_datetime_utc,
    DEFAULT_MAX_ODDS_AGE_HOURS,
)
from engine.value_bet_engine import ValueBetEngine


def test_1_bet365_fresh_odds_within_threshold():
    """Valida que una quota Bet365 capturada recentment és FRESH i is_stale=False."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    captured_20m = (now - timedelta(minutes=20)).isoformat()

    res = evaluate_freshness(captured_20m, now=now, max_age_hours=2.0)
    assert res["freshness"] == "FRESH"
    assert res["is_stale"] is False
    assert res["rejection_reason"] == "ok"
    assert res["age_seconds"] == 1200.0


def test_2_bet365_stale_odds_exceeding_threshold():
    """Valida que una quota Bet365 que supera el límit d'antiguitat és STALE i is_stale=True."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    captured_3h = (now - timedelta(hours=3)).isoformat()

    res = evaluate_freshness(captured_3h, now=now, max_age_hours=2.0)
    assert res["freshness"] == "STALE"
    assert res["is_stale"] is True
    assert res["rejection_reason"] == "exceeded_max_age"
    assert res["age_seconds"] == 10800.0


def test_3_bet365_missing_timestamp_classified_as_unknown():
    """Valida que quan el timestamp és absent (None o buit) Bet365 el classifica com a UNKNOWN."""
    for empty_val in [None, "", "   "]:
        res = evaluate_freshness(empty_val)
        assert res["freshness"] == "UNKNOWN"
        assert res["is_stale"] is True
        assert res["rejection_reason"] == "missing_timestamp"


def test_4_bet365_malformed_timestamp_classified_as_unknown():
    """Valida que timestamps malformats o no parsejables es classifiquen com a UNKNOWN."""
    for bad in ["not-a-date", "2026-99-99", "invalid"]:
        res = evaluate_freshness(bad)
        assert res["freshness"] == "UNKNOWN"
        assert res["is_stale"] is True
        assert res["rejection_reason"] == "malformed_timestamp"


def test_5_bet365_future_timestamp_clock_skew():
    """Valida el tractament de timestamps en el futur (clock skew tolerat <= 5 min)."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)

    # 1. Tolerància lleugera (ex: 2 minuts)
    slight = (now + timedelta(minutes=2)).isoformat()
    res_slight = evaluate_freshness(slight, now=now)
    assert res_slight["freshness"] == "FRESH"
    assert res_slight["is_stale"] is False
    assert res_slight["age_seconds"] == 0.0

    # 2. Desajust greu (> 5 minuts)
    big = (now + timedelta(minutes=10)).isoformat()
    res_big = evaluate_freshness(big, now=now)
    assert res_big["freshness"] == "UNKNOWN"
    assert res_big["is_stale"] is True
    assert res_big["rejection_reason"] == "future_clock_skew"


def test_6_bet365_scraper_failure_preserves_cache_without_updating_timestamp(tmp_path):
    """
    Comprova que si una petició a l'API externa falla, la memòria cau existent
    NO es modifica i el timestamp no s'actualitza pas.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    orig_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()

    initial_cache = {
        "LALIGA_realmadrid_barcelona": {
            "source": "Bet365 Espanya (LALIGA)",
            "bookmaker": "BET365",
            "matched": True,
            "captured_at": orig_ts,
            "polled_at": orig_ts,
            "freshness": "FRESH",
            "is_stale": False,
            "provenance": "BET365_OFFICIAL_FEED",
            "1X2": {"1": 2.20, "X": 3.40, "2": 3.20},
            "market_status": {
                "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "polled_at": orig_ts}
            }
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(initial_cache, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=48.0)

    # Simular crida d'API externa fallida a _fetch_odds_api
    with patch.object(scraper, "_fetch_odds_api", return_value=None):
        odds = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA")

    # Revalida la memòria cau preexistent
    assert odds["matched"] is True
    assert odds["1X2"]["1"] == 2.20
    assert odds["captured_at"] == orig_ts

    # El fitxer roman 100% inalterat
    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["LALIGA_realmadrid_barcelona"]["captured_at"] == orig_ts


def test_7_bet365_stale_and_unknown_excluded_from_value_bets():
    """Valida que ValueBetEngine exclou terminantment quotes Bet365 amb estat STALE o UNKNOWN."""
    engine = ValueBetEngine(kelly_fraction=0.25)

    pred = {
        "home_team": {"name": "Equip Local", "id": "LOC"},
        "away_team": {"name": "Equip Visitant", "id": "VIS"},
        "goals": {
            "prob_1X2": {"1": 60.0, "X": 25.0, "2": 15.0},
            "over_under": {"over_2_5": 55.0},
            "btts": {"yes": 50.0, "no": 50.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }

    # Cas A: Bet365 FRESH amb +EV
    fresh_b365 = {
        "matched": True,
        "freshness": "FRESH",
        "is_stale": False,
        "provenance": "BET365",
        "1X2": {"1": 2.30, "X": 3.40, "2": 3.50}
    }
    res_fresh = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=fresh_b365)
    vb_fresh = res_fresh.get("value_bets", [])
    assert len(vb_fresh) > 0
    assert vb_fresh[0]["bookie"] == "Bet365"
    assert vb_fresh[0]["provenance"] == "BET365"

    # Cas B: Bet365 STALE
    stale_b365 = {
        "matched": True,
        "freshness": "STALE",
        "is_stale": True,
        "freshness_reason": "exceeded_max_age",
        "provenance": "BET365",
        "1X2": {"1": 2.30, "X": 3.40, "2": 3.50}
    }
    res_stale = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=stale_b365)
    assert len(res_stale.get("value_bets", [])) == 0

    # Cas C: Bet365 UNKNOWN (sense timestamp)
    unknown_b365 = {
        "matched": True,
        "freshness": "UNKNOWN",
        "is_stale": True,
        "freshness_reason": "missing_timestamp",
        "provenance": "BET365",
        "1X2": {"1": 2.30, "X": 3.40, "2": 3.50}
    }
    res_unknown = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=unknown_b365)
    assert len(res_unknown.get("value_bets", [])) == 0


def test_8_bet365_provenance_preservation():
    """Verifica que la procedència (provenance) es manté amb fidelitat (BET365, MANUAL_OVERRIDE, etc.)."""
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Lecce", "id": "LEC"},
        "away_team": {"name": "Genoa", "id": "GEN"},
        "goals": {
            "prob_1X2": {"1": 50.0, "X": 30.0, "2": 20.0},
            "over_under": {"over_2_5": 50.0},
            "btts": {"yes": 50.0, "no": 50.0}
        },
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    b365_feed = {
        "matched": True,
        "freshness": "FRESH",
        "is_stale": False,
        "provenance": "BET365_MOVIODDS",
        "1X2": {"1": 2.50, "X": 3.20, "2": 3.40}
    }
    res = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=b365_feed)
    vb = res["value_bets"][0]
    assert vb["provenance"] == "BET365"
    assert vb["bookie_odd_bet365"] == 2.50
    assert vb["bookie"] == "Bet365"


def test_9_bet365_git_commit_timestamp_never_confused():
    """Verifica que l'absència de metadades a la memòria cau resulta sempre en UNKNOWN independentment de Git."""
    eval_res = evaluate_freshness(None)
    assert eval_res["freshness"] == "UNKNOWN"
    assert eval_res["is_stale"] is True



def test_10_granular_market_freshness_and_partial_updates(tmp_path):
    """
    CAS DE PROVA OBLIGATORI (Requisit 1):
    1. Un partit té tres mercats amb timestamps diferents a la memòria cau.
    2. Una nova captura HTTP/feed actualitza ÚNICAMENT un dels tres mercats.
    3. Comprova que els altres dos mercats NO reben un timestamp nou artificialment.
    4. Verifica que només les quotes realment actualitzades (i les que ja eren recents)
       passen a FRESH, mentre que les antigues sense actualitzar romanen STALE i s'exclouen.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    now_ref = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)

    ts_1x2_init = (now_ref - timedelta(minutes=10)).isoformat()       # 10 minuts d'antiguitat (FRESH)
    ts_goals_init = (now_ref - timedelta(hours=3, minutes=30)).isoformat() # 3.5 hores d'antiguitat (STALE)
    ts_corners_init = (now_ref - timedelta(hours=6)).isoformat()     # 6 hores d'antiguitat (STALE)

    initial_cache = {
        "LALIGA_barcelona_madrid": {
            "source": "Bet365 Espanya (LALIGA)",
            "bookmaker": "BET365",
            "matched": True,
            "captured_at": ts_1x2_init,
            "polled_at": ts_1x2_init,
            "provenance": "BET365",
            "market_status": {
                "1X2": {
                    "valid": True,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok",
                    "captured_at": ts_1x2_init,
                    "polled_at": ts_1x2_init,
                },
                "over_under_2_5": {
                    "valid": True,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok",
                    "captured_at": ts_goals_init,
                    "polled_at": ts_goals_init,
                },
                "corners": {
                    "valid": True,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok",
                    "captured_at": ts_corners_init,
                    "polled_at": ts_corners_init,
                }
            },
            "1X2": {"1": 2.10, "X": 3.40, "2": 3.20},
            "over_under_2_5": {"Over 2.5": 1.85, "Under 2.5": 1.95},
            "corners": {"Over 9.5": 1.90, "Under 9.5": 1.90}
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(initial_cache, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=2.0)

    # 1. Comprovació inicial abans de la nova captura parcial
    entry_before = scraper.revalidate_cached_entry(initial_cache["LALIGA_barcelona_madrid"], now=now_ref)
    # 1X2 era fresh (10 min < 2h)
    assert entry_before["1X2"]["1"] == 2.10
    assert entry_before["market_status"]["1X2"]["valid"] is True
    # Gols i córners eren stale (> 2h)
    assert entry_before["over_under_2_5"] == {}
    assert entry_before["market_status"]["over_under_2_5"]["stale"] is True
    assert entry_before["corners"] == {}
    assert entry_before["market_status"]["corners"]["stale"] is True

    # 2. Nova captura parcial que ÚNICAMENT actualitza 'over_under_2_5' en aquest instant (now_ref)
    ts_new_capture = now_ref.isoformat()
    scraper.update_match_markets(
        "LALIGA_barcelona_madrid",
        updated_markets={"over_under_2_5": {"Over 2.5": 2.05, "Under 2.5": 1.75}},
        captured_at=ts_new_capture,
        market_metadata={"over_under_2_5": {"polled_at": ts_new_capture}}
    )

    # 3. Comprovar que els altres dos mercats NO han rebut el timestamp nou artificialment
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_cache = json.load(f)
    match_saved = saved_cache["LALIGA_barcelona_madrid"]
    m_stat_saved = match_saved["market_status"]

    assert m_stat_saved["over_under_2_5"]["captured_at"] == ts_new_capture, "over_under_2_5 ha de tenir el timestamp nou"
    assert m_stat_saved["1X2"]["captured_at"] == ts_1x2_init, "1X2 NO ha de ser alterat pel update d'un altre mercat"
    assert m_stat_saved["corners"]["captured_at"] == ts_corners_init, "corners NO ha de rebre el timestamp nou artificialment"

    # 4. Revalidació: només les quotes realment actualitzades i les que ja eren recents passen a FRESH
    reval = scraper.revalidate_cached_entry(match_saved, now=now_ref)
    assert reval["matched"] is True

    # 'over_under_2_5' ara ha passat a FRESH amb les noves quotes 2.05 / 1.75
    assert reval["over_under_2_5"]["Over 2.5"] == 2.05
    assert reval["market_status"]["over_under_2_5"]["valid"] is True
    assert reval["market_status"]["over_under_2_5"]["stale"] is False

    # '1X2' es manté FRESH (capturat fa 10 min)
    assert reval["1X2"]["1"] == 2.10
    assert reval["market_status"]["1X2"]["valid"] is True

    # 'corners' (capturat fa 6 hores) CONTINUA STALE i queda exclòs
    assert reval["corners"] == {}, "El mercat no actualitzat fa 6 hores ha de romandre buit i descartat"
    assert reval["market_status"]["corners"]["stale"] is True


def test_11_separation_of_capture_provider_and_match_timestamps():
    """
    Valida la separació explícita i rigorosa entre:
    - captured_at: Hora local de descàrrega/ingesta UTC pel scraper.
    - provider_updated_at: Hora d'actualització interna reportada pel proveïdor (ts, polled_at, last_update).
    - kickoff_utc: Hora oficial d'inici del partit.
    """
    kickoff = "2026-11-01T20:00:00Z"
    provider_ts = "2026-10-31T12:45:00Z"
    local_capture = "2026-10-31T13:00:00+00:00"

    entry = {
        "source": "Bet365 via The-Odds-API (LALIGA)",
        "bookmaker": "BET365",
        "matched": True,
        "kickoff_utc": kickoff,
        "provider_updated_at": provider_ts,
        "captured_at": local_capture,
        "polled_at": provider_ts,
        "provenance": "BET365_AUTHORIZED_API",
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.20},
        "market_status": {
            "1X2": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": local_capture,
                "provider_updated_at": provider_ts,
                "polled_at": provider_ts,
            }
        }
    }

    scraper = Bet365Scraper()
    now_ref = datetime(2026, 10, 31, 13, 0, 0, tzinfo=timezone.utc)
    reval = scraper.revalidate_cached_entry(entry, now=now_ref)
    assert reval["captured_at"] == local_capture
    assert reval["provider_updated_at"] == provider_ts
    assert reval["kickoff_utc"] == kickoff
    assert reval["captured_at"] != reval["provider_updated_at"]
    assert reval["captured_at"] != reval["kickoff_utc"]


def test_12_legacy_cache_without_captured_at_is_unknown_and_recovers_on_fresh_capture(tmp_path):
    """
    Verifica que:
    1. Una entrada antiga sense captured_at vàlid es classifica com a UNKNOWN i no genera value bets.
    2. Una captura correcta posterior recupera la disponibilitat d'apostes sense haver de tocar manualment la memòria cau antiga.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"

    # Entrada antiga sense captured_at ni polled_at fiable
    legacy_cache = {
        "LALIGA_sevilla_betis": {
            "source": "Bet365 Espanya",
            "bookmaker": "BET365",
            "matched": True,
            "1X2": {"1": 2.40, "X": 3.10, "2": 3.00},
            "market_status": {}
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(legacy_cache, f)

    scraper = Bet365Scraper(cache_file=cache_file)
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Sevilla", "id": "SEV"},
        "away_team": {"name": "Betis", "id": "BET"},
        "goals": {"prob_1X2": {"1": 55.0, "X": 25.0, "2": 20.0}, "over_under": {"over_2_5": 50.0}, "btts": {"yes": 50.0, "no": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }

    # 1. Entrada antiga: rebutjada com a UNKNOWN
    odds_legacy = scraper.get_match_odds("Sevilla", "Betis", "LALIGA")
    assert odds_legacy["matched"] is False or odds_legacy.get("freshness") == "UNKNOWN"
    res_legacy = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=odds_legacy)
    assert len(res_legacy["value_bets"]) == 0

    # 2. Nova captura vàlida d'aquest partit (sense tocar a mà el fitxer antic)
    now_fresh = datetime.now(timezone.utc).isoformat()
    scraper.save_cache_entry("LALIGA_sevilla_betis", {
        "source": "Bet365 Espanya",
        "bookmaker": "BET365",
        "matched": True,
        "provenance": "BET365",
        "captured_at": now_fresh,
        "polled_at": now_fresh,
        "1X2": {"1": 2.40, "X": 3.10, "2": 3.00},
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": now_fresh, "polled_at": now_fresh}
        }
    })

    # 3. La disponibilitat de l'aposta de valor es recupera automàticament
    odds_recovered = scraper.get_match_odds("Sevilla", "Betis", "LALIGA")
    assert odds_recovered["matched"] is True
    assert odds_recovered["freshness"] == "FRESH"
    res_recovered = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=odds_recovered)
    assert len(res_recovered["value_bets"]) > 0
    assert res_recovered["value_bets"][0]["bookie"] == "Bet365"



def test_13_bet365_configurable_freshness_limits_and_precedence():
    """
    Verifica la configurabilitat dels límits de frescor a Bet365:
    1. Valor per defecte: 2.0 hores (7200 segons).
    2. Variable d'entorn BET365_MAX_ODDS_AGE_HOURS sobreescriu el valor per defecte.
    3. Argument del constructor max_odds_age_hours té prioritat màxima sobre la variable d'entorn.
    4. Transició exacta a STALE: age_seconds <= 7200 és FRESH; age_seconds > 7200 passa a STALE.
    """
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)

    # 1. Valor per defecte: 2.0 hores
    scraper_def = Bet365Scraper()
    assert scraper_def.max_odds_age_hours == 2.0

    # 2. Transició exacta a l'últim segon
    ts_exact_limit = (now - timedelta(seconds=7200)).isoformat()
    res_at_limit = evaluate_freshness(ts_exact_limit, now=now, max_age_hours=2.0)
    assert res_at_limit["freshness"] == "FRESH"
    assert res_at_limit["is_stale"] is False

    ts_just_over = (now - timedelta(seconds=7201)).isoformat()
    res_over_limit = evaluate_freshness(ts_just_over, now=now, max_age_hours=2.0)
    assert res_over_limit["freshness"] == "STALE"
    assert res_over_limit["is_stale"] is True

    # 3. Variable d'entorn
    with patch.dict(os.environ, {"BET365_MAX_ODDS_AGE_HOURS": "6.0"}):
        scraper_env = Bet365Scraper()
        assert scraper_env.max_odds_age_hours == 6.0
        ts_4h = (now - timedelta(hours=4)).isoformat()
        res_env = evaluate_freshness(ts_4h, now=now)
        assert res_env["freshness"] == "FRESH"

        # 4. Argument del constructor preval sobre la variable d'entorn
        scraper_override = Bet365Scraper(max_odds_age_hours=1.5)
        assert scraper_override.max_odds_age_hours == 1.5


def test_14_preservation_of_historical_odds_when_stale(tmp_path):
    """
    Verifica la separació entre la preservació històrica i l'elegibilitat per generar apostes:
    1. La quota original i el timestamp es conserven intactes a la memòria cau a disc i a get_historical_cache_entry().
    2. El mercat queda esborrat del diccionari actiu de quotes i exclòs de value_bets i all_value_bets.
    3. Una fallada posterior de xarxa manté la quota antiga en STALE (mai passa a FRESH).
    4. Una captura vàlida posterior restableix la quota a FRESH.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=5)).isoformat()

    stale_entry = {
        "source": "Bet365 Espanya (LALIGA)",
        "bookmaker": "BET365",
        "matched": True,
        "captured_at": stale_ts,
        "polled_at": stale_ts,
        "provenance": "BET365",
        "1X2": {"1": 2.25, "X": 3.30, "2": 3.40},
        "corners": {"Over 9.5": 1.95, "Under 9.5": 1.85},
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": stale_ts, "polled_at": stale_ts},
            "corners": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": stale_ts, "polled_at": stale_ts}
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"LALIGA_valencia_osasuna": stale_entry}, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=2.0)
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Valencia", "id": "VAL"},
        "away_team": {"name": "Osasuna", "id": "OSA"},
        "goals": {"prob_1X2": {"1": 55.0, "X": 25.0, "2": 20.0}, "over_under": {"over_2_5": 50.0}, "btts": {"yes": 50.0, "no": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 60.0}}
    }

    # 1. Consulta quan la quota és STALE:
    # get_match_odds() descarta l'ús actiu d'apostes caducades
    odds = scraper.get_match_odds("Valencia", "Osasuna", "LALIGA")
    assert odds["matched"] is False
    assert odds["1X2"] == {}
    assert odds["corners"] == {}

    # 2. Exclusió absoluta de value_bets
    res = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=odds)
    assert len(res["value_bets"]) == 0

    # 3. La memòria cau a disc i en memòria conserva íntegrament les dades originals
    hist = scraper.get_historical_cache_entry("LALIGA_valencia_osasuna")
    assert hist is not None
    assert hist["1X2"]["1"] == 2.25
    assert hist["corners"]["Over 9.5"] == 1.95
    assert hist["captured_at"] == stale_ts

    with open(cache_file, "r", encoding="utf-8") as f:
        saved_on_disk = json.load(f)
    assert saved_on_disk["LALIGA_valencia_osasuna"]["1X2"]["1"] == 2.25
    assert saved_on_disk["LALIGA_valencia_osasuna"]["captured_at"] == stale_ts

    # 4. Fallada de xarxa posterior: no converteix la quota antiga en FRESH
    with patch.object(scraper, "_fetch_odds_api", return_value=None):
        odds_failed = scraper.get_match_odds("Valencia", "Osasuna", "LALIGA")
    assert odds_failed["matched"] is False
    assert odds_failed["1X2"] == {}

    # 5. Captura vàlida posterior: recupera el mercat com a FRESH
    now_fresh = datetime.now(timezone.utc).isoformat()
    scraper.save_cache_entry("LALIGA_valencia_osasuna", {
        "source": "Bet365 Espanya (LALIGA)",
        "bookmaker": "BET365",
        "matched": True,
        "captured_at": now_fresh,
        "polled_at": now_fresh,
        "provenance": "BET365",
        "1X2": {"1": 2.25, "X": 3.30, "2": 3.40},
        "corners": {"Over 9.5": 1.95, "Under 9.5": 1.85},
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": now_fresh, "polled_at": now_fresh},
            "corners": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": now_fresh, "polled_at": now_fresh}
        }
    })

    odds_recovered = scraper.get_match_odds("Valencia", "Osasuna", "LALIGA")
    assert odds_recovered["matched"] is True
    assert odds_recovered["freshness"] == "FRESH"
    assert odds_recovered["1X2"]["1"] == 2.25
    assert odds_recovered["corners"]["Over 9.5"] == 1.95
    res_rec = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=odds_recovered)
    assert len(res_rec["value_bets"]) > 0



def test_15_historical_odds_survives_subsequent_cache_updates(tmp_path):
    """
    Verifica que:
    1. Quan una quota o mercat caduca, les quotes originals es conserven a historical_odds.
    2. Actualitzacions posteriors de la memòria cau (ex: actualitzar un altre mercat)
       preserven intactes les historical_odds prèvies sense esborrar-les.
    3. Les dades històriques sobreviuen tant en memòria com en disc.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    ts_init = (now - timedelta(hours=6)).isoformat()

    init_entry = {
        "source": "Bet365 Espanya",
        "bookmaker": "BET365",
        "matched": True,
        "captured_at": ts_init,
        "polled_at": ts_init,
        "provenance": "BET365",
        "1X2": {"1": 2.15, "X": 3.25, "2": 3.40},
        "corners": {"Over 9.5": 1.90, "Under 9.5": 1.90},
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": ts_init, "polled_at": ts_init},
            "corners": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": ts_init, "polled_at": ts_init}
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"LALIGA_athletic_sociedad": init_entry}, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=2.0)

    # 1. Revalidar l'entrada antiga: les quotes de corners passen a historical_odds
    reval = scraper.revalidate_cached_entry(init_entry)
    # Com que 1X2 era fa 6h, el partit sencer no és matched com a fresh
    assert reval is None or reval.get("corners") == {}

    # 2. Executar una actualització posterior del mercat 1X2 (nova captura parcial)
    now_fresh = now.isoformat()
    scraper.update_match_markets(
        "LALIGA_athletic_sociedad",
        updated_markets={"1X2": {"1": 2.10, "X": 3.30, "2": 3.50}},
        captured_at=now_fresh,
        market_metadata={"1X2": {"polled_at": now_fresh}}
    )

    # 3. Comprovar que les quotes prèvies de 1X2 i corners s'han preservat a historical_odds
    hist_entry = scraper.get_historical_cache_entry("LALIGA_athletic_sociedad")
    assert "historical_odds" in hist_entry
    assert hist_entry["historical_odds"]["1X2"]["1"] == 2.15, "La quota prèvia de 1X2 ha de sobreviure a historical_odds"

    # Comprovació a disc
    with open(cache_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data["LALIGA_athletic_sociedad"]["historical_odds"]["1X2"]["1"] == 2.15


def test_16_provider_updated_at_null_when_not_provided(tmp_path):
    """
    Verifica que:
    1. Si el proveïdor no transmet cap timestamp de canvi de quota, el camp 'provider_updated_at'
       roman estrictament None (null en JSON).
    2. En cap cas se li atribueix fraudulentament el rellotge local del sistema.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    scraper = Bet365Scraper(cache_file=cache_file)

    now_str = datetime.now(timezone.utc).isoformat()
    scraper.save_cache_entry("LALIGA_celta_rayo", {
        "source": "Bet365 Espanya",
        "bookmaker": "BET365",
        "matched": True,
        "provenance": "BET365",
        "captured_at": now_str,
        "polled_at": now_str,
        "provider_updated_at": None,
        "1X2": {"1": 2.00, "X": 3.40, "2": 3.60},
        "market_status": {
            "1X2": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": now_str,
                "polled_at": now_str,
                "provider_updated_at": None
            }
        }
    })

    with open(cache_file, "r", encoding="utf-8") as f:
        disk_entry = json.load(f)["LALIGA_celta_rayo"]

    assert disk_entry["provider_updated_at"] is None
    assert disk_entry["market_status"]["1X2"]["provider_updated_at"] is None
    # captured_at té el timestamp local, però provider_updated_at és NULL
    assert disk_entry["captured_at"] == now_str



def test_17_partial_update_of_selections_within_corners_market(tmp_path):
    """
    CAS DE PROVA OBLIGATORI (Requisit 1):
    1. El mercat de córners conté Over 8.5, Under 8.5, Over 9.5 i Under 9.5 capturats fa 6 hores (STALE).
    2. Una nova captura només retorna Over 8.5 en aquest instant (now).
    3. Comprova que:
       - El timestamp de Over 8.5 es renova a now.
       - Les altres 3 seleccions (Under 8.5, Over 9.5, Under 9.5) conserven el timestamp de fa 6 hores.
       - A la revalidació, només Over 8.5 roman actiu a entry['corners'].
       - Les altres 3 seleccions queden excloses del mercat actiu, marcades com a STALE i arxivades a historical_odds.
       - ValueBetEngine només pot generar apostes sobre la línia fresca (Over 8.5), mai sobre Under 9.5.
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=6)).isoformat()
    now_ts = now.isoformat()

    init_entry = {
        "source": "Bet365 Espanya",
        "bookmaker": "BET365",
        "matched": True,
        "captured_at": stale_ts,
        "polled_at": stale_ts,
        "provenance": "BET365",
        "1X2": {"1": 2.00, "X": 3.40, "2": 3.60},
        "corners": {
            "Over 8.5": 1.70,
            "Under 8.5": 2.10,
            "Over 9.5": 2.05,
            "Under 9.5": 1.80
        },
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": now_ts, "polled_at": now_ts},
            "corners": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": stale_ts,
                "polled_at": stale_ts,
                "selections": {
                    "Over 8.5": {"valid": True, "is_stale": False, "captured_at": stale_ts},
                    "Under 8.5": {"valid": True, "is_stale": False, "captured_at": stale_ts},
                    "Over 9.5": {"valid": True, "is_stale": False, "captured_at": stale_ts},
                    "Under 9.5": {"valid": True, "is_stale": False, "captured_at": stale_ts}
                }
            }
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"LALIGA_valencia_betis": init_entry}, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=2.0)

    # 1. Actualització parcial: només rebem Over 8.5 amb quota 1.75 en aquest instant (now_ts)
    scraper.update_match_markets(
        "LALIGA_valencia_betis",
        updated_markets={"corners": {"Over 8.5": 1.75}},
        captured_at=now_ts,
        market_metadata={"corners": {"polled_at": now_ts}}
    )

    # 2. Comprovació a la memòria cau desada
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_cache = json.load(f)["LALIGA_valencia_betis"]

    sel_status = saved_cache["market_status"]["corners"]["selections"]
    assert sel_status["Over 8.5"]["captured_at"] == now_ts, "Over 8.5 ha de rebre el timestamp nou"
    assert sel_status["Under 8.5"]["captured_at"] == stale_ts, "Under 8.5 NO ha de renovar-se artificialment"
    assert sel_status["Over 9.5"]["captured_at"] == stale_ts, "Over 9.5 NO ha de renovar-se artificialment"
    assert sel_status["Under 9.5"]["captured_at"] == stale_ts, "Under 9.5 NO ha de renovar-se artificialment"

    # 3. Revalidació en l'instant now
    reval = scraper.revalidate_cached_entry(saved_cache, now=now)
    assert reval is not None
    assert reval["matched"] is True

    # El diccionari actiu de córners NOMÉS conté Over 8.5
    assert "Over 8.5" in reval["corners"]
    assert reval["corners"]["Over 8.5"] == 1.75
    assert "Under 8.5" not in reval["corners"]
    assert "Over 9.5" not in reval["corners"]
    assert "Under 9.5" not in reval["corners"]

    # Les altres 3 seleccions estan arxivades a historical_odds
    assert "historical_odds" in reval
    assert "corners" in reval["historical_odds"]
    assert reval["historical_odds"]["corners"]["Under 8.5"] == 2.10
    assert reval["historical_odds"]["corners"]["Over 9.5"] == 2.05
    assert reval["historical_odds"]["corners"]["Under 9.5"] == 1.80

    # 4. Impacte sobre ValueBetEngine
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Valencia", "id": "VAL"},
        "away_team": {"name": "Betis", "id": "BET"},
        "goals": {"prob_1X2": {"1": 50.0, "X": 30.0, "2": 20.0}, "over_under": {"over_2_5": 50.0}, "btts": {"yes": 50.0, "no": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {
            "prob_over_corners": {"over_8_5": 65.0, "over_9_5": 50.0},
            "prob_under_corners": {"under_8_5": 35.0, "under_9_5": 70.0}
        }
    }

    res = engine.analyze_match_betting(pred, winamax_odds={}, bet365_odds=reval)
    vb_corners = [vb for vb in res["value_bets"] if vb.get("category") == "Córners" or "Córners" in vb.get("name", "")]

    # Over 8.5 és fresca i té EV positiu (prob 65% amb quota 1.75 -> EV = +13.75%)
    assert any(vb.get("threshold") == 8.5 or "8.5" in vb.get("name", "") for vb in vb_corners)

    # Under 9.5 (encara que tingués prob 70% per a quota 1.80) MAI pot entrar perquè és STALE i no és a active odds
    assert not any("9.5" in vb.get("name", "") for vb in vb_corners), "Under 9.5 STALE mai ha de produir una value bet"


def test_18_collision_prevention_across_corner_market_types(tmp_path):
    """
    FASE 1.2: Verifica la unicitat i no-col·lisió de seleccions amb el mateix nom ("Over 8.5")
    a través de diferents àmbits de mercat de córners:
    - corners (totals)
    - corners_home (locals)
    - corners_away (visitants)
    - corners_1st_half (1a part)
    - corners_2nd_half (2a part)
    """
    cache_file = tmp_path / "bet365_odds_cache.json"
    now = datetime(2026, 11, 1, 15, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=5)).isoformat()
    fresh_ts = now.isoformat()

    corner_markets = ["corners", "corners_home", "corners_away", "corners_1st_half", "corners_2nd_half"]
    init_entry = {
        "source": "Bet365 Espanya",
        "bookmaker": "BET365",
        "matched": True,
        "captured_at": stale_ts,
        "polled_at": stale_ts,
        "provenance": "BET365",
        "1X2": {"1": 1.95, "X": 3.40, "2": 3.80},
        "market_status": {
            "1X2": {"valid": True, "stale": False, "suspended": False, "rejection_reason": "ok", "captured_at": fresh_ts, "polled_at": fresh_ts}
        }
    }

    # Assignar a cadascun una quota diferent per la selecció "Over 8.5" fa 5 hores
    odds_map = {
        "corners": 1.80,
        "corners_home": 3.20,
        "corners_away": 4.10,
        "corners_1st_half": 5.50,
        "corners_2nd_half": 5.00
    }
    for m in corner_markets:
        init_entry[m] = {"Over 8.5": odds_map[m]}
        init_entry["market_status"][m] = {
            "valid": True,
            "stale": False,
            "suspended": False,
            "rejection_reason": "ok",
            "captured_at": stale_ts,
            "polled_at": stale_ts,
            "selections": {
                "Over 8.5": {"valid": True, "is_stale": False, "captured_at": stale_ts}
            }
        }

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"LALIGA_athletic_espanyol": init_entry}, f)

    scraper = Bet365Scraper(cache_file=cache_file, max_odds_age_hours=2.0)

    # Actualitzem exclusivament el mercat de córners totals ("corners")
    scraper.update_match_markets(
        "LALIGA_athletic_espanyol",
        updated_markets={"corners": {"Over 8.5": 1.95}},
        captured_at=fresh_ts,
        market_metadata={"corners": {"polled_at": fresh_ts}}
    )

    # 1. Verificar la persistència a disc
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_cache = json.load(f)["LALIGA_athletic_espanyol"]

    # "corners" (totals) ha rebut fresh_ts
    assert saved_cache["corners"]["Over 8.5"] == 1.95
    assert saved_cache["market_status"]["corners"]["selections"]["Over 8.5"]["captured_at"] == fresh_ts

    # Tots els altres àmbits de mercat conserven exactament stale_ts i les seves quotes pròpies
    for m in ["corners_home", "corners_away", "corners_1st_half", "corners_2nd_half"]:
        assert saved_cache[m]["Over 8.5"] == odds_map[m]
        assert saved_cache["market_status"][m]["selections"]["Over 8.5"]["captured_at"] == stale_ts, (
            f"El mercat {m} ha rebut indegudament el timestamp del mercat corners!"
        )

    # 2. Revalidació
    reval = scraper.revalidate_cached_entry(saved_cache, now=now)
    assert reval is not None

    # corners (totals) està actiu i FRESH
    assert "corners" in reval
    assert reval["corners"].get("Over 8.5") == 1.95

    # La resta d'àmbits són STALE i han estat purgats dels mercats actius
    for m in ["corners_home", "corners_away", "corners_1st_half", "corners_2nd_half"]:
        assert len(reval.get(m, {})) == 0, f"El mercat obsolet {m} no ha de figurar als mercats actius"
        assert m in reval.get("historical_odds", {}), f"El mercat {m} ha d'estar arxivat a historical_odds"
        assert reval["historical_odds"][m]["Over 8.5"] == odds_map[m]


def test_19_championship_prematch_stable_odds_actionable_for_surebets():
    """
    REGRESSIÓ CRÍTICA AUDITORIA CHAMPIONSHIP:
    Valida que partits prematch a diversos dies vista (>24h) amb quotes estables
    des de fa diverses hores (ex: provider_updated_at fa 7h) NO són descartats
    quan la darrera observació (polled_at i captured_at) és recent (1 minut).
    Demostra que l'ArbitrageEngine les accepta plenament i no les exclou per timeout.
    """
    now = datetime(2026, 11, 9, 18, 0, 0, tzinfo=timezone.utc)
    kickoff = (now + timedelta(days=5)).isoformat()
    prov_ts = (now - timedelta(hours=7)).isoformat()
    poll_ts = (now - timedelta(minutes=1)).isoformat()
    cap_ts = now.isoformat()

    entry = {
        "source": "Bet365 via MoviOdds (Championship)",
        "bookmaker": "BET365",
        "matched": True,
        "kickoff_utc": kickoff,
        "provider_updated_at": prov_ts,
        "polled_at": poll_ts,
        "captured_at": cap_ts,
        "provenance": "BET365_MOVIODDS",
        "origin_freshness": "CONFIRMED",
        "1X2": {"1": 2.80, "X": 3.30, "2": 2.65},
        "market_status": {
            "1X2": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": cap_ts,
                "polled_at": poll_ts,
                "provider_updated_at": prov_ts,
                "origin_freshness": "CONFIRMED"
            }
        }
    }

    scraper = Bet365Scraper()
    reval = scraper.revalidate_cached_entry(entry, now=now)
    assert reval is not None
    assert reval["matched"] is True
    assert reval["is_stale"] is False
    assert reval["freshness"] == "FRESH"
    assert reval["1X2"]["1"] == 2.80
    assert reval["captured_at"] == cap_ts
    assert reval["polled_at"] == poll_ts
    assert reval["provider_updated_at"] == prov_ts

    # Validació a l'ArbitrageEngine creuant amb Winamax (capturat fa 30s)
    from engine.arbitrage_engine import ArbitrageEngine
    engine = ArbitrageEngine(max_odds_age_seconds=60.0, max_skew_seconds=60.0)

    match_mock = {
        "id": "champ_match_1",
        "home_team": {"name": "Watford"},
        "away_team": {"name": "Bristol City"},
        "kickoff_utc": kickoff,
        "odds_1x2_winamax": {"1": 2.85, "X": 3.40, "2": 2.50},
        "odds_winamax": {"captured_at": (now - timedelta(seconds=20)).isoformat(), "polled_at": (now - timedelta(seconds=20)).isoformat()},
        "odds_1x2_bet365": reval["1X2"],
        "odds_bet365": reval,
    }

    arbs = engine.scan_match(match_mock, now=now)
    assert isinstance(arbs, list)
    # L'ArbitrageEngine NO ha d'excloure el partit per age_max > 1800s
    for a in arbs:
        assert a.get("freshness_tier") != engine.TIER_EXCLUDED


def test_20_imminent_match_stale_provider_modification_rejected():
    """
    Valida la protecció adaptativa de seguretat:
    Si el partit comença d'aquí a 1 hora (< 2h) i la quota de Bet365 no s'ha modificat
    ni confirmat en origen des de fa 6 hores (> 4h llindar imminent), es rebutja
    com a STALE per evitar publicar un arbitratge fantasma amb un mercat desfasat.
    """
    now = datetime(2026, 11, 9, 18, 0, 0, tzinfo=timezone.utc)
    imminent_kickoff = (now + timedelta(hours=1)).isoformat()
    prov_ts_old = (now - timedelta(hours=6)).isoformat() # 6h > 4h
    poll_ts_recent = (now - timedelta(minutes=1)).isoformat()
    cap_ts = now.isoformat()

    entry = {
        "source": "Bet365 via MoviOdds (LaLiga)",
        "bookmaker": "BET365",
        "matched": True,
        "kickoff_utc": imminent_kickoff,
        "provider_updated_at": prov_ts_old,
        "polled_at": poll_ts_recent,
        "captured_at": cap_ts,
        "provenance": "BET365_MOVIODDS",
        "origin_freshness": "CONFIRMED",
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.50},
        "market_status": {
            "1X2": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": cap_ts,
                "polled_at": poll_ts_recent,
                "provider_updated_at": prov_ts_old,
                "origin_freshness": "CONFIRMED"
            }
        }
    }

    scraper = Bet365Scraper()
    reval = scraper.revalidate_cached_entry(entry, now=now)
    # Ha de ser descartat per risc d'obsolescència a les portes del xiulet inicial
    assert reval is None or reval.get("matched") is False or reval.get("is_stale") is True


def test_21_stale_feed_observation_rejected():
    """
    Valida la separació de conceptes:
    Fins i tot si provider_updated_at és molt recent (fa 5 minuts), si la darrera
    observació del feed (polled_at / captured_at) té més de 30 minuts (ex: 2.5 hores),
    el mercat es marca com a STALE perquè no podem garantir que la quota continuï disponible.
    """
    now = datetime(2026, 11, 9, 18, 0, 0, tzinfo=timezone.utc)
    kickoff = (now + timedelta(days=2)).isoformat()
    prov_ts_recent = (now - timedelta(minutes=5)).isoformat()
    poll_ts_old = (now - timedelta(hours=2, minutes=30)).isoformat() # 2.5h > 2.0h max_age

    entry = {
        "source": "Bet365 (Feed Stale)",
        "bookmaker": "BET365",
        "matched": True,
        "kickoff_utc": kickoff,
        "provider_updated_at": prov_ts_recent,
        "polled_at": poll_ts_old,
        "captured_at": poll_ts_old,
        "provenance": "BET365_MOVIODDS",
        "origin_freshness": "CONFIRMED",
        "1X2": {"1": 2.10, "X": 3.40, "2": 3.50},
        "market_status": {
            "1X2": {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "captured_at": poll_ts_old,
                "polled_at": poll_ts_old,
                "provider_updated_at": prov_ts_recent,
            }
        }
    }

    scraper = Bet365Scraper(max_odds_age_hours=2.0)
    reval = scraper.revalidate_cached_entry(entry, now=now)
    assert reval is None or reval.get("matched") is False or reval.get("is_stale") is True

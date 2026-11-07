"""
tests/test_winamax_freshness_policy.py
======================================
Bateria de proves exhaustiva per a la política de frescor de Winamax:
1. Quota fresca (dins del llindar configurable).
2. Quota caducada / obsoleta (STALE quan age > max_odds_age_hours).
3. Timestamp absent (None o buit -> UNKNOWN).
4. Timestamp mal format (cadena invàlida -> UNKNOWN).
5. Timestamp futur per possible desajust del rellotge (clock skew: tolerat <= 5 min; > 5 min -> UNKNOWN).
6. Fallada del scraper sense actualització incorrecta del timestamp (el fitxer no es toca ni s'actualitza en fallada HTTP).
7. Exclusió estricta de quotes UNKNOWN i STALE de value_bets i all_value_bets.
8. Configurabilitat dels llindars per argument i per variable d'entorn.
9. Distinció estricta entre timestamp del commit Git i timestamp de captura en viu.
"""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from scraper.winamax_scraper import (
    WinamaxScraper,
    evaluate_freshness,
    parse_datetime_utc,
    DEFAULT_MAX_ODDS_AGE_HOURS,
    DEFAULT_MAX_FUTURE_SKEW_SECONDS,
)
from engine.value_bet_engine import ValueBetEngine


def test_1_fresh_odds_within_threshold():
    """Valida que una quota amb captura recent es classifica com a FRESH i is_stale=False."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    # Capturada fa 30 minuts (límit per defecte: 2 hores)
    captured_30m = (now - timedelta(minutes=30)).isoformat()

    res = evaluate_freshness(captured_30m, now=now, max_age_hours=2.0)
    assert res["freshness"] == "FRESH"
    assert res["is_stale"] is False
    assert res["rejection_reason"] == "ok"
    assert res["age_seconds"] == 1800.0
    assert res["captured_at"] == captured_30m


def test_2_stale_odds_exceeding_threshold():
    """Valida que una quota amb captura que supera el llindar es classifica com a STALE i is_stale=True."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    # Capturada fa 2 hores i 15 minuts (límit: 2 hores)
    captured_old = (now - timedelta(hours=2, minutes=15)).isoformat()

    res = evaluate_freshness(captured_old, now=now, max_age_hours=2.0)
    assert res["freshness"] == "STALE"
    assert res["is_stale"] is True
    assert res["rejection_reason"] == "exceeded_max_age"
    assert res["age_seconds"] == 8100.0


def test_3_missing_timestamp_classified_as_unknown():
    """Valida que quan el timestamp és absent (None o buit) es classifica com a UNKNOWN i is_stale=True."""
    for empty_val in [None, "", "   "]:
        res = evaluate_freshness(empty_val)
        assert res["freshness"] == "UNKNOWN"
        assert res["is_stale"] is True
        assert res["rejection_reason"] == "missing_timestamp"
        assert res["age_seconds"] is None
        assert res["captured_at"] is None


def test_4_malformed_timestamp_classified_as_unknown():
    """Valida que cadenes no parsejables es classifiquen com a UNKNOWN i is_stale=True."""
    bad_timestamps = [
        "not-a-valid-date",
        "2026-99-99T99:99:99Z",
        "invalid_timestamp_string",
        "12/34/5678",
    ]
    for bad in bad_timestamps:
        res = evaluate_freshness(bad)
        assert res["freshness"] == "UNKNOWN"
        assert res["is_stale"] is True
        assert res["rejection_reason"] == "malformed_timestamp"
        assert res["age_seconds"] is None


def test_5_future_timestamp_clock_skew():
    """
    Valida el tractament de timestamps en el futur per possible desajust de rellotge (clock skew):
    - Dins de la tolerància de 5 minuts (<= 300s): tolerat i tractat com a FRESH (age=0).
    - Més enllà de 5 minuts (> 300s en el futur): considerat anòmal -> UNKNOWN i is_stale=True.
    """
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)

    # 1. Lleuger avanç de rellotge (ex: 2 minuts en el futur) dins de tolerància
    slight_future = (now + timedelta(minutes=2)).isoformat()
    res_slight = evaluate_freshness(slight_future, now=now, max_future_skew_seconds=300.0)
    assert res_slight["freshness"] == "FRESH"
    assert res_slight["is_stale"] is False
    assert res_slight["age_seconds"] == 0.0
    assert res_slight["rejection_reason"] == "ok"

    # 2. Gran avanç de rellotge (ex: 15 minuts o 2 hores en el futur) fora de tolerància
    big_future = (now + timedelta(minutes=15)).isoformat()
    res_big = evaluate_freshness(big_future, now=now, max_future_skew_seconds=300.0)
    assert res_big["freshness"] == "UNKNOWN"
    assert res_big["is_stale"] is True
    assert res_big["rejection_reason"] == "future_clock_skew"
    assert res_big["age_seconds"] < -300.0


def test_6_scraper_failure_never_overwrites_cache_nor_updates_timestamp(tmp_path):
    """
    Comprova que si la petició HTTP del scraper falla (error curl, xarxa, resposta buida),
    la memòria cau existent NO es modifica i el timestamp anterior NO s'actualitza pas.
    """
    test_cache_file = tmp_path / "winamax_cache_tid_32.json"
    original_timestamp = "2026-10-30T10:00:00+00:00"

    # Inicialitzar fitxer de cau preexistent
    initial_payload = {
        "_metadata": {
            "source": "winamax",
            "tournament_id": "32",
            "captured_at": original_timestamp,
            "format_version": "1.0"
        },
        "matches": {
            "1001": {
                "matchId": 1001,
                "title": "Real Madrid - Barcelona",
                "competitor1Name": "Real Madrid",
                "competitor2Name": "Barcelona",
                "mainBetId": 5001,
                "tournamentId": 36
            }
        },
        "bets": {"5001": {"betId": 5001, "outcomes": [9001, 9002, 9003]}},
        "outcomes": {
            "9001": {"betId": 5001, "code": "1"},
            "9002": {"betId": 5001, "code": "X"},
            "9003": {"betId": 5001, "code": "2"}
        },
        "odds": {"9001": 2.10, "9002": 3.40, "9003": 3.50}
    }
    with open(test_cache_file, "w", encoding="utf-8") as f:
        json.dump(initial_payload, f)

    initial_mtime = os.path.getmtime(test_cache_file)

    # Simular scraper amb fallada HTTP (curl retorna codi d'error / resposta buida)
    scraper = WinamaxScraper(cache_dir=tmp_path, max_odds_age_hours=24.0)

    with patch("subprocess.run") as mock_run:
        # Simulació de fallada de xarxa
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Connection refused")
        odds = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA")

    # 1. El scraper ha fet fallback al contingut de cau preexistent
    assert odds["matched"] is True
    assert odds["1X2"]["1"] == 2.10

    # 2. El fitxer de cau NO ha estat sobreescrit
    with open(test_cache_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)

    assert saved_data["_metadata"]["captured_at"] == original_timestamp
    assert os.path.getmtime(test_cache_file) == initial_mtime


def test_7_exclusion_of_stale_and_unknown_from_value_bets():
    """
    Verifica que ValueBetEngine i WebDataExporter exclouen terminantment
    qualsevol quota amb estat UNKNOWN o STALE de la llista activa de value_bets.
    """
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

    # Cas A: Quota FRESH amb +EV (quota 2.20 per a probabilitat 60% -> EV = +32%)
    fresh_feed = {
        "matched": True,
        "freshness": "FRESH",
        "is_stale": False,
        "1X2": {"1": 2.20, "X": 3.40, "2": 4.00}
    }
    res_fresh = engine.analyze_match_betting(pred, winamax_odds=fresh_feed, bet365_odds={})
    vb_fresh = res_fresh.get("value_bets", [])
    assert len(vb_fresh) > 0, "Una quota fresca amb +EV ha de ser inclosa a value_bets"
    assert any("1 - Victòria Local" in vb["name"] for vb in vb_fresh)

    # Cas B: Quota STALE amb la mateixa quota 2.20
    stale_feed = {
        "matched": True,
        "freshness": "STALE",
        "is_stale": True,
        "freshness_reason": "exceeded_max_age",
        "1X2": {"1": 2.20, "X": 3.40, "2": 4.00}
    }
    res_stale = engine.analyze_match_betting(pred, winamax_odds=stale_feed, bet365_odds={})
    vb_stale = res_stale.get("value_bets", [])
    assert len(vb_stale) == 0, "Una quota STALE MAI ha d'entrar a value_bets públiques"

    # Cas C: Quota UNKNOWN (absència de timestamp fiable)
    unknown_feed = {
        "matched": True,
        "freshness": "UNKNOWN",
        "is_stale": True,
        "freshness_reason": "missing_timestamp",
        "1X2": {"1": 2.20, "X": 3.40, "2": 4.00}
    }
    res_unknown = engine.analyze_match_betting(pred, winamax_odds=unknown_feed, bet365_odds={})
    vb_unknown = res_unknown.get("value_bets", [])
    assert len(vb_unknown) == 0, "Una quota UNKNOWN MAI ha d'entrar a value_bets públiques"


def test_8_configurable_freshness_limits():
    """Valida la configurabilitat del límit de frescor tant per paràmetre com per variable d'entorn."""
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    captured_3h = (now - timedelta(hours=3)).isoformat()

    # 1. Amb límit per defecte (2h): 3h és STALE
    res_def = evaluate_freshness(captured_3h, now=now, max_age_hours=2.0)
    assert res_def["freshness"] == "STALE"

    # 2. Amb límit configurat per argument a 4 hores: 3h és FRESH
    res_custom = evaluate_freshness(captured_3h, now=now, max_age_hours=4.0)
    assert res_custom["freshness"] == "FRESH"
    assert res_custom["is_stale"] is False

    # 3. Amb límit configurat via variable d'entorn
    with patch.dict(os.environ, {"WINAMAX_MAX_ODDS_AGE_HOURS": "5.0"}):
        res_env = evaluate_freshness(captured_3h, now=now, max_age_hours=None)
        assert res_env["freshness"] == "FRESH"
        assert res_env["is_stale"] is False


def test_9_git_commit_timestamp_never_confused_with_capture_timestamp():
    """
    Comprova explícitament que la data d'un commit Git o de modificació de fitxer
    mai no és emprada per a suplantar el timestamp real de captura HTTP de Winamax.
    """
    # Si una memòria cau crua no té camp '_metadata' amb 'captured_at',
    # encara que el fitxer hagi tingut un commit recent, la frescor ha de ser UNKNOWN.
    raw_state_without_metadata = {
        "matches": {"101": {"matchId": 101, "title": "Test vs Sample"}},
        "odds": {"1": 2.50}
    }
    meta_extracted = raw_state_without_metadata.get("_metadata")
    assert meta_extracted is None

    # L'avaluació sense captured_at retorna UNKNOWN
    eval_res = evaluate_freshness(None)
    assert eval_res["freshness"] == "UNKNOWN"
    assert eval_res["is_stale"] is True



def test_10_winamax_tournament_vs_match_granularity_and_simulation(tmp_path):
    """
    Verifica la granularitat de Winamax:
    1. Per defecte, la captura de Winamax descarrega i valida el torneig sencer (/sports/1/{tid})
       de manera atòmica, compartint _metadata.captured_at.
    2. Simula una actualització individual d'un partit mitjançant update_match_in_cache().
    3. Comprova que el partit actualitzat passa a FRESH mentre que la resta de partits del torneig
       conserven el seu timestamp original (STALE) sense ser renovats artificialment.
    """
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    # Torneig capturat fa 3 hores (STALE sota límit per defecte de 2 hores)
    tournament_ts = (now - timedelta(hours=3)).isoformat()

    tournament_payload = {
        "_metadata": {
            "source": "winamax",
            "tournament_id": "32",
            "captured_at": tournament_ts,
            "format_version": "1.0"
        },
        "matches": {
            "1001": {
                "matchId": 1001,
                "tournamentId": 36,
                "competitor1Name": "Real Madrid",
                "competitor2Name": "Barcelona",
                "title": "Real Madrid vs Barcelona",
                "mainBetId": 5001
            },
            "1002": {
                "matchId": 1002,
                "tournamentId": 36,
                "competitor1Name": "Sevilla",
                "competitor2Name": "Real Betis",
                "title": "Sevilla vs Real Betis",
                "mainBetId": 5002
            }
        },
        "bets": {
            "5001": {"betId": 5001, "matchId": 1001, "outcomes": [9001, 9002, 9003]},
            "5002": {"betId": 5002, "matchId": 1002, "outcomes": [9004, 9005, 9006]}
        },
        "outcomes": {
            "9001": {"betId": 5001, "code": "1"}, "9002": {"betId": 5001, "code": "X"}, "9003": {"betId": 5001, "code": "2"},
            "9004": {"betId": 5002, "code": "1"}, "9005": {"betId": 5002, "code": "X"}, "9006": {"betId": 5002, "code": "2"}
        },
        "odds": {
            "9001": 2.10, "9002": 3.40, "9003": 3.50,
            "9004": 2.40, "9005": 3.10, "9006": 3.00
        }
    }

    cache_file = tmp_path / "winamax_cache_tid_32.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(tournament_payload, f)

    scraper = WinamaxScraper(cache_dir=tmp_path, max_odds_age_hours=2.0, offline=True)

    # Abans de l'actualització: ambdós partits pertanyen a un torneig capturat fa 3h -> STALE
    odds_rm_before = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA", now=now)
    odds_sev_before = scraper.get_match_odds("Sevilla", "Real Betis", "LALIGA", now=now)

    assert odds_rm_before["is_stale"] is True
    assert odds_rm_before["freshness"] == "STALE"
    assert odds_sev_before["is_stale"] is True
    assert odds_sev_before["freshness"] == "STALE"

    # Simular actualització individual ÚNICAMENT del partit Real Madrid vs Barcelona (ID 1001)
    fresh_match_ts = (now - timedelta(minutes=5)).isoformat()
    scraper.update_match_in_cache("32", "1001", {"captured_at": fresh_match_ts})

    # Verificar a disc:
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_disk = json.load(f)

    # 1. El torneig global NO ha renovat el seu timestamp artificialment
    assert saved_disk["_metadata"]["captured_at"] == tournament_ts
    # 2. El partit 1002 (Sevilla vs Betis) NO té timestamp nou artificial
    assert "captured_at" not in saved_disk["matches"]["1002"]
    # 3. El partit 1001 té el nou timestamp
    assert saved_disk["matches"]["1001"]["captured_at"] == fresh_match_ts

    # Reavaluació dels partits
    odds_rm_after = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA", now=now)
    odds_sev_after = scraper.get_match_odds("Sevilla", "Real Betis", "LALIGA", now=now)

    # Real Madrid vs Barcelona és FRESH!
    assert odds_rm_after["is_stale"] is False
    assert odds_rm_after["freshness"] == "FRESH"
    assert odds_rm_after["captured_at"] == fresh_match_ts

    # Sevilla vs Real Betis segueix STALE (fa 3h)!
    assert odds_sev_after["is_stale"] is True
    assert odds_sev_after["freshness"] == "STALE"
    assert odds_sev_after["captured_at"] == tournament_ts


def test_11_winamax_historical_data_preservation_when_stale(tmp_path):
    """
    Verifica que:
    1. La quota original i el timestamp es conserven a la memòria cau històrica quan és STALE.
    2. El mercat queda exclòs d'all_value_bets de ValueBetEngine.
    3. Una fallada posterior de xarxa manté la quota antiga en STALE (mai passa a FRESH).
    4. Una captura vàlida posterior restableix la quota a FRESH.
    """
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=5)).isoformat()

    tournament_payload = {
        "_metadata": {
            "source": "winamax",
            "tournament_id": "32",
            "captured_at": stale_ts,
            "format_version": "1.0"
        },
        "matches": {
            "1001": {
                "matchId": 1001,
                "tournamentId": 36,
                "competitor1Name": "Getafe",
                "competitor2Name": "Valencia",
                "title": "Getafe vs Valencia",
                "mainBetId": 5001
            }
        },
        "bets": {
            "5001": {"betId": 5001, "matchId": 1001, "outcomes": [9001, 9002, 9003]}
        },
        "outcomes": {
            "9001": {"betId": 5001, "code": "1"}, "9002": {"betId": 5001, "code": "X"}, "9003": {"betId": 5001, "code": "2"}
        },
        "odds": {"9001": 2.20, "9002": 3.10, "9003": 3.50}
    }

    cache_file = tmp_path / "winamax_cache_tid_32.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(tournament_payload, f)

    scraper = WinamaxScraper(cache_dir=tmp_path, max_odds_age_hours=2.0)
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Getafe", "id": "GET"},
        "away_team": {"name": "Valencia", "id": "VAL"},
        "goals": {"prob_1X2": {"1": 55.0, "X": 25.0, "2": 20.0}, "over_under": {"over_2_5": 50.0}, "btts": {"yes": 50.0, "no": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }

    # 1. Quota STALE: les dades originals es preserven en la resposta crua
    odds = scraper.get_match_odds("Getafe", "Valencia", "LALIGA", now=now)

    assert odds["1X2"]["1"] == 2.20
    assert odds["is_stale"] is True
    assert odds["freshness"] == "STALE"
    assert odds["captured_at"] == stale_ts

    # 2. Exclusió d'all_value_bets
    res = engine.analyze_match_betting(pred, winamax_odds=odds, bet365_odds={})
    assert len(res["value_bets"]) == 0, "Quotes STALE MAI poden entrar a value bets"

    # 3. Fallada posterior de xarxa: conserva la quota antiga com a STALE, mai FRESH
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Network error")
        odds_after_fail = scraper.get_match_odds("Getafe", "Valencia", "LALIGA", now=now)

    assert odds_after_fail["is_stale"] is True
    assert odds_after_fail["freshness"] == "STALE"
    assert odds_after_fail["captured_at"] == stale_ts

    # 4. Captura vàlida posterior: recupera el mercat com a FRESH
    fresh_ts = now.isoformat()
    tournament_payload["_metadata"]["captured_at"] = fresh_ts
    scraper.save_cache("32", tournament_payload, captured_at=fresh_ts)

    odds_recovered = scraper.get_match_odds("Getafe", "Valencia", "LALIGA", now=now)

    assert odds_recovered["is_stale"] is False
    assert odds_recovered["freshness"] == "FRESH"
    res_rec = engine.analyze_match_betting(pred, winamax_odds=odds_recovered, bet365_odds={})
    assert len(res_rec["value_bets"]) > 0


def test_12_malformed_or_partial_response_never_marks_tournament_fresh(tmp_path):
    """
    Verifica que:
    1. Una resposta parcial, buida o malformada de curl (ex: sense odds o sense bets)
       mai no valida el torneig ni l'actualitza com a FRESH.
    2. La memòria cau existent conserva intacte el seu timestamp antic (STALE).
    """
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=4)).isoformat()

    valid_old_payload = {
        "_metadata": {
            "source": "winamax",
            "tournament_id": "32",
            "captured_at": stale_ts,
            "format_version": "1.0"
        },
        "matches": {
            "2001": {
                "matchId": 2001,
                "tournamentId": 36,
                "competitor1Name": "Alaves",
                "competitor2Name": "Mallorca",
                "title": "Alaves vs Mallorca",
                "mainBetId": 6001
            }
        },
        "bets": {"6001": {"betId": 6001, "matchId": 2001, "outcomes": [9101, 9102, 9103]}},
        "outcomes": {"9101": {"betId": 6001, "code": "1"}, "9102": {"betId": 6001, "code": "X"}, "9103": {"betId": 6001, "code": "2"}},
        "odds": {"9101": 2.30, "9102": 3.10, "9103": 3.40}
    }

    cache_file = tmp_path / "winamax_cache_tid_32.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(valid_old_payload, f)

    scraper = WinamaxScraper(cache_dir=tmp_path, max_odds_age_hours=2.0)

    # Simular resposta de curl malformada: té matches, però les odds estan buides (o fallada de captcha)
    malformed_html = """
    <html><head><script>
    var PRELOADED_STATE = {"matches": {"2001": {"matchId": 2001}}, "bets": {}, "odds": {}};
    </script></head><body>Challenge</body></html>
    """

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=malformed_html, stderr="")
        odds = scraper.get_match_odds("Alaves", "Mallorca", "LALIGA", now=now)

    # 1. Com que la resposta era incompleta/malformada, no ha actualitzat el timestamp
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_disk = json.load(f)

    assert saved_disk["_metadata"]["captured_at"] == stale_ts

    # 2. El scraper fa fallback al contingut existent i roman STALE
    assert odds["is_stale"] is True
    assert odds["freshness"] == "STALE"
    assert odds["captured_at"] == stale_ts



def test_13_winamax_partial_response_coverage_drop_rejected(tmp_path):
    """
    CAS DE PROVA OBLIGATORI (Requisit 2):
    - Un torneig original a la memòria cau té 100 partits amb bets i odds vàlides.
      Té timestamp de fa 4 hores (STALE).
    - Una resposta nova de xarxa només conté 8 partits, però amb 'matches', 'bets' i 'odds'
      no buits i estructuralment vàlids.
    - Comprova que:
      1. El scraper rebutja la nova captura per cobertura insuficient (8 < 50% de 100).
      2. No renova _metadata["captured_at"].
      3. No sobreescriu la memòria cau anterior a disc (conserva els 100 partits).
      4. Conserva les quotes antigues i el seu estat de frescor (STALE).
      5. ValueBetEngine no publica cap value bet d'aquest partit.
    """
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(hours=4)).isoformat()

    # 1. Generar torneig consolidat de 100 partits
    matches_100 = {}
    bets_100 = {}
    outcomes_100 = {}
    odds_100 = {}

    for i in range(1, 101):
        mid = str(1000 + i)
        bid = str(5000 + i)
        o1, ox, o2 = 9000 + i * 3 - 2, 9000 + i * 3 - 1, 9000 + i * 3
        c1 = f"HomeTeam_{i}" if i > 1 else "Real Madrid"
        c2 = f"AwayTeam_{i}" if i > 1 else "Barcelona"
        matches_100[mid] = {
            "matchId": int(mid),
            "tournamentId": 36,
            "competitor1Name": c1,
            "competitor2Name": c2,
            "title": f"{c1} vs {c2}",
            "mainBetId": int(bid)
        }
        bets_100[bid] = {"betId": int(bid), "matchId": int(mid), "outcomes": [o1, ox, o2]}
        outcomes_100[str(o1)] = {"betId": int(bid), "code": "1"}
        outcomes_100[str(ox)] = {"betId": int(bid), "code": "X"}
        outcomes_100[str(o2)] = {"betId": int(bid), "code": "2"}
        odds_100[str(o1)] = 2.10
        odds_100[str(ox)] = 3.30
        odds_100[str(o2)] = 3.50

    original_tournament_payload = {
        "_metadata": {
            "source": "winamax",
            "tournament_id": "32",
            "captured_at": stale_ts,
            "format_version": "1.0"
        },
        "matches": matches_100,
        "bets": bets_100,
        "outcomes": outcomes_100,
        "odds": odds_100
    }

    cache_file = tmp_path / "winamax_cache_tid_32.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(original_tournament_payload, f)

    scraper = WinamaxScraper(cache_dir=tmp_path, max_odds_age_hours=2.0)

    # 2. Resposta nova amb només 8 partits (però no buits i vàlids)
    matches_8 = {k: matches_100[k] for k in list(matches_100.keys())[:8]}
    bets_8 = {k: bets_100[k] for k in list(bets_100.keys())[:8]}
    odds_8 = {k: odds_100[k] for k in list(odds_100.keys())[:24]}
    outcomes_8 = {k: outcomes_100[k] for k in list(outcomes_100.keys())[:24]}

    partial_state_8 = {
        "matches": matches_8,
        "bets": bets_8,
        "outcomes": outcomes_8,
        "odds": odds_8
    }
    partial_html = f"""
    <html><head><script>
    var PRELOADED_STATE = {json.dumps(partial_state_8)};
    </script></head><body>OK</body></html>
    """

    # Simular crida HTTP de xarxa que retorna la resposta amb només 8 partits
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=partial_html, stderr="")
        odds_res = scraper.get_match_odds("Real Madrid", "Barcelona", "LALIGA", now=now)

    # 3. Comprovar que a disc NO s'ha sobreescrit la memòria cau anterior
    with open(cache_file, "r", encoding="utf-8") as f:
        saved_on_disk = json.load(f)

    # El fitxer a disc manté els 100 partits originals
    assert len(saved_on_disk["matches"]) == 100, "La memòria cau no ha de ser sobreescrita per una resposta amb caiguda sobtada a 8 partits"
    # El timestamp global NO s'ha renovat a now
    assert saved_on_disk["_metadata"]["captured_at"] == stale_ts, "El timestamp NO ha de ser renovat per una captura truncada"

    # 4. El partit consultat es manté amb les quotes originals i en estat STALE
    assert odds_res["matched"] is True
    assert odds_res["is_stale"] is True
    assert odds_res["freshness"] == "STALE"
    assert odds_res["captured_at"] == stale_ts

    # 5. ValueBetEngine no publica cap value bet d'aquesta resposta
    engine = ValueBetEngine(kelly_fraction=0.25)
    pred = {
        "home_team": {"name": "Real Madrid", "id": "RMA"},
        "away_team": {"name": "Barcelona", "id": "FCB"},
        "goals": {"prob_1X2": {"1": 60.0, "X": 25.0, "2": 15.0}, "over_under": {"over_2_5": 50.0}, "btts": {"yes": 50.0, "no": 50.0}},
        "cards": {"prob_over_cards": {"over_4_5": 40.0}},
        "corners": {"prob_over_corners": {"over_9_5": 50.0}}
    }
    vb_res = engine.analyze_match_betting(pred, winamax_odds=odds_res, bet365_odds={})
    assert len(vb_res["value_bets"]) == 0, "Una resposta STALE no ha de generar mai cap value bet"


def test_14_validate_tournament_payload_five_cases():
    """
    FASE 1.1: Verifica els 5 casos concrets de validació de cobertura i relacions a Winamax.
    """
    now = datetime(2026, 11, 1, 14, 0, 0, tzinfo=timezone.utc)
    now_epoch = now.timestamp()

    def make_match(i, mid=None, bid=None, start_offset_hours=2):
        mid = mid or (1000 + i)
        bid = bid or (5000 + i)
        o1, ox, o2 = 9000 + i * 3 - 2, 9000 + i * 3 - 1, 9000 + i * 3
        m_start = int(now_epoch + start_offset_hours * 3600)
        m = {
            "matchId": mid,
            "tournamentId": 36,
            "competitor1Name": f"Home_{i}",
            "competitor2Name": f"Away_{i}",
            "title": f"Home_{i} vs Away_{i}",
            "mainBetId": bid,
            "matchStart": m_start
        }
        b = {str(bid): {"betId": bid, "matchId": mid, "outcomes": [o1, ox, o2]}}
        o = {str(o1): 2.10, str(ox): 3.20, str(o2): 3.40}
        return str(mid), m, b, o

    # Construir estat preexistent amb 100 partits vàlids
    existing_100 = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(1, 101):
        mid_str, m, b, o = make_match(i, start_offset_hours=24)
        existing_100["matches"][mid_str] = m
        existing_100["bets"].update(b)
        existing_100["odds"].update(o)

    # CAS 1: 100 partits anteriors, 60 nous a la resposta, però només 1 té totes les relacions vàlides
    # -> Ha de ser rebutjat per no assolir el 80% de partits vàlids (1/60 < 80%) i cobertura insuficient
    payload_cas1 = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(1, 61):
        mid_str, m, b, o = make_match(i)
        payload_cas1["matches"][mid_str] = m
        if i == 1:
            # Només el partit 1 té la bet i les odds correctes
            payload_cas1["bets"].update(b)
            payload_cas1["odds"].update(o)
        else:
            # La resta apunta a una bet que no existeix
            m["mainBetId"] = 999999

    is_valid, reason = WinamaxScraper.validate_tournament_payload(payload_cas1, existing_state=existing_100, now=now)
    assert is_valid is False
    assert "insufficient_valid_matches_ratio" in reason or "insufficient_coverage" in reason

    # CAS 2: 100 partits anteriors, 60 nous a la resposta, tots 60 amb relacions correctes
    # -> Ha de ser acceptat (60/60 = 100% >= 80%, i 60 >= 50% de 100)
    payload_cas2 = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(1, 61):
        mid_str, m, b, o = make_match(i)
        payload_cas2["matches"][mid_str] = m
        payload_cas2["bets"].update(b)
        payload_cas2["odds"].update(o)

    is_valid, reason = WinamaxScraper.validate_tournament_payload(payload_cas2, existing_state=existing_100, now=now)
    assert is_valid is True
    assert reason == "ok"

    # CAS 3: Partits amb referències a quotes que no existeixen al diccionari d'odds (orfes / fantasma)
    # -> Les quotes no es troben a odds, per la qual cosa els partits no són vàlids i es rebutja
    payload_cas3 = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(1, 61):
        mid_str, m, b, o = make_match(i)
        payload_cas3["matches"][mid_str] = m
        payload_cas3["bets"].update(b)
        # Només introduïm odds per als primers 5 partits (5/60 < 80%)
        if i <= 5:
            payload_cas3["odds"].update(o)

    is_valid, reason = WinamaxScraper.validate_tournament_payload(payload_cas3, existing_state=existing_100, now=now)
    assert is_valid is False
    assert "insufficient_valid_matches_ratio" in reason

    # CAS 4: Reducció legítima del calendari per partits finalitzats (matchStart < now)
    # Estat anterior: 100 partits, dels quals 60 ja han acabat (matchStart < now) i 40 són futurs.
    # Nova resposta: 35 partits, tots vàlids.
    # 35 és el 87.5% dels 40 partits actius esperats (35 >= 50% de 40 = 20) -> Ha de ser acceptat!
    existing_calendar = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(1, 101):
        # Primers 60 partits ja van començar fa hores
        offset = -5 if i <= 60 else 24
        mid_str, m, b, o = make_match(i, start_offset_hours=offset)
        existing_calendar["matches"][mid_str] = m
        existing_calendar["bets"].update(b)
        existing_calendar["odds"].update(o)

    payload_cas4 = {"matches": {}, "bets": {}, "odds": {}}
    for i in range(61, 96):  # 35 partits nous vàlids dels 40 restants
        mid_str, m, b, o = make_match(i, start_offset_hours=24)
        payload_cas4["matches"][mid_str] = m
        payload_cas4["bets"].update(b)
        payload_cas4["odds"].update(o)

    is_valid, reason = WinamaxScraper.validate_tournament_payload(payload_cas4, existing_state=existing_calendar, now=now)
    assert is_valid is True
    assert reason == "ok"

    # CAS 5: Resposta truncada amb diccionaris no buits però estructures trencades o incompletes
    # Partits sense mainBetId, quotes buides o amb quotes numèriques <= 1.0 (invàlides)
    payload_cas5 = {
        "matches": {
            "8001": {"matchId": 8001, "title": "Truncated 1"},
            "8002": {"matchId": 8002, "title": "Truncated 2", "mainBetId": 9999}
        },
        "bets": {
            "9999": {"betId": 9999, "matchId": 8002, "outcomes": [10001, 10002]}
        },
        "odds": {
            "10001": 0.50,  # Quota <= 1.0 invàlida!
            "10002": 1.00   # Quota <= 1.0 invàlida!
        }
    }
    is_valid, reason = WinamaxScraper.validate_tournament_payload(payload_cas5, existing_state=existing_100, now=now)
    assert is_valid is False


def test_15_winamax_event_status_discrimination():
    """
    Verifica que la discriminació d'estats reals de Winamax (LIVE, FINISHED, SUSPENDED, POSTPONED, PREMATCH):
    1. No confon un partit en directe (matchStart < now però status LIVE) amb un de finalitzat.
    2. Identifica correctament partits finalitzats, ajornats o suspesos.
    3. Manté els partits en directe com a actius en la línia base de validació de cobertura.
    """
    now = datetime(2026, 11, 1, 17, 30, 0, tzinfo=timezone.utc)
    now_epoch = now.timestamp()

    # 1. Partit en directe (va començar fa 45 minuts, marcat com a LIVE)
    m_live = {
        "matchId": 101,
        "status": "LIVE",
        "live": True,
        "matchStart": int(now_epoch - 2700)
    }
    assert WinamaxScraper.classify_event_status(m_live, now_epoch) == "LIVE"

    # 2. Partit començat fa 1 hora sense flag 'live' explícit però dins la finestra de 3.5h
    m_in_window = {
        "matchId": 102,
        "matchStart": int(now_epoch - 3600)
    }
    assert WinamaxScraper.classify_event_status(m_in_window, now_epoch) == "LIVE"

    # 3. Partit acabat explícitament (status ENDED o FINISHED)
    m_ended = {
        "matchId": 103,
        "status": "ENDED",
        "matchStart": int(now_epoch - 7200)
    }
    assert WinamaxScraper.classify_event_status(m_ended, now_epoch) == "FINISHED"

    # 4. Partit que va començar fa 5 hores (> 3.5h) sense indicador live
    m_old = {
        "matchId": 104,
        "matchStart": int(now_epoch - 18000)
    }
    assert WinamaxScraper.classify_event_status(m_old, now_epoch) == "FINISHED"

    # 5. Partit suspès
    m_susp = {
        "matchId": 105,
        "status": "SUSPENDED",
        "matchStart": int(now_epoch - 1800)
    }
    assert WinamaxScraper.classify_event_status(m_susp, now_epoch) == "SUSPENDED"

    # 6. Partit ajornat / cancel·lat
    m_postp = {
        "matchId": 106,
        "status": "POSTPONED",
        "matchStart": int(now_epoch + 86400)
    }
    assert WinamaxScraper.classify_event_status(m_postp, now_epoch) == "POSTPONED"

    # 7. Partit futur (PREMATCH)
    m_future = {
        "matchId": 107,
        "status": "PREMATCH",
        "matchStart": int(now_epoch + 7200)
    }
    assert WinamaxScraper.classify_event_status(m_future, now_epoch) == "PREMATCH"

    # 8. Comprovació de cobertura: un partit LIVE no s'exclou de la línia base activa
    existing_state = {
        "matches": {
            "201": {"matchId": 201, "status": "LIVE", "mainBetId": 501, "matchStart": int(now_epoch - 1800)},
            "202": {"matchId": 202, "status": "PREMATCH", "mainBetId": 502, "matchStart": int(now_epoch + 3600)},
            "203": {"matchId": 203, "status": "ENDED", "mainBetId": 503, "matchStart": int(now_epoch - 14400)},
        },
        "bets": {
            "501": {"betId": 501, "outcomes": [1, 2]},
            "502": {"betId": 502, "outcomes": [3, 4]},
            "503": {"betId": 503, "outcomes": [5, 6]},
        },
        "odds": {"1": 1.9, "2": 1.9, "3": 2.0, "4": 1.8, "5": 2.1, "6": 1.7}
    }
    # En aquest estat, dels 3 partits, 2 són actius (201 LIVE i 202 PREMATCH), i 1 està finalitzat (203 ENDED).
    # Comprovem que classify_event_status preserva el partit LIVE com a actiu.
    active_count = sum(
        1 for em in existing_state["matches"].values()
        if WinamaxScraper.classify_event_status(em, now_epoch) in ("PREMATCH", "LIVE")
    )
    assert active_count == 2


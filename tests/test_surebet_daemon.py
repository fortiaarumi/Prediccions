"""
tests/test_surebet_daemon.py
============================
Tests unitaris i de resiliència per a surebet_daemon.py:
1. Gestió atòmica de ProcessLock (concurrència, processos vius/morts, alliberament).
2. CircuitBreaker independent (comptador de fallades, suspensió automàtica, cooldown, recuperació).
3. Política de freqüències adaptativa (NO_UPCOMING, WITHIN_4H, WITHIN_2H, IMMINENT).
4. Aïllament de fallades entre cases d'apostes (Winamax, Bet365, Betway).
5. Execució d'un cicle del daemon amb mode --dry-run i actualització de dades.
"""

import os
import json
import time
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from surebet_daemon import (
    ProcessLock,
    CircuitBreaker,
    SurebetDaemon,
    POLICY_FREQUENCIES,
    CIRCUIT_BREAKER_MAX_FAILURES,
    CIRCUIT_BREAKER_COOLDOWN_SECONDS
)


# -----------------------------------------------------------------------------
# 1. Tests de ProcessLock
# -----------------------------------------------------------------------------
def test_process_lock_acquisition_and_release(tmp_path):
    lock_file = tmp_path / "test.lock"
    lock = ProcessLock(lock_path=lock_file)

    # 1. Adquisició neta
    assert lock.acquire() is True
    assert lock_file.exists()

    with open(lock_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["pid"] == os.getpid()
        assert "started_at" in data

    # 2. Segona adquisició sense force ha de fallar perquè el procés actual està viu
    lock2 = ProcessLock(lock_path=lock_file)
    assert lock2.acquire(force=False) is False

    # 3. Alliberament net
    lock.release()
    assert not lock_file.exists()


def test_process_lock_dead_pid_recovery(tmp_path):
    lock_file = tmp_path / "test_dead.lock"
    # Escrivim un PID fictici que sabem que no està viu (ex: 99999999)
    with open(lock_file, "w", encoding="utf-8") as f:
        json.dump({"pid": 99999999, "started_at": datetime.now(timezone.utc).isoformat()}, f)

    lock = ProcessLock(lock_path=lock_file)
    # Com que el PID està mort, ha de recuperar el lockfile i adquirir-lo
    with patch.object(lock, "_is_pid_alive", return_value=False):
        assert lock.acquire(force=False) is True
        assert lock.acquired is True
        lock.release()


def test_process_lock_corrupted_file_recovery(tmp_path):
    lock_file = tmp_path / "test_corrupt.lock"
    lock_file.write_text("CORRUPTED_JSON_NOT_VALID", encoding="utf-8")

    lock = ProcessLock(lock_path=lock_file)
    # Ha de sobreviure i adquirir el lock
    assert lock.acquire() is True
    lock.release()


# -----------------------------------------------------------------------------
# 2. Tests de CircuitBreaker
# -----------------------------------------------------------------------------
def test_circuit_breaker_lifecycle():
    cb = CircuitBreaker("betway", max_failures=3, cooldown_seconds=600)
    now = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)

    # Estat inicial actiu
    assert cb.status == "active"
    assert cb.is_available(now) is True

    # 1a fallada -> status "error", però encara available
    cb.record_failure("Timeout 1", now)
    assert cb.status == "error"
    assert cb.consecutive_failures == 1
    assert cb.is_available(now) is True

    # 2a fallada -> status "error", encara available
    cb.record_failure("Timeout 2", now)
    assert cb.status == "error"
    assert cb.consecutive_failures == 2
    assert cb.is_available(now) is True

    # 3a fallada -> ateny max_failures -> status "suspended"
    cb.record_failure("Timeout 3", now)
    assert cb.status == "suspended"
    assert cb.consecutive_failures == 3
    assert cb.suspended_until == now + timedelta(seconds=600)
    assert cb.is_available(now) is False

    # Abans d'expirar el cooldown continua suspès
    during_cooldown = now + timedelta(seconds=300)
    assert cb.is_available(during_cooldown) is False

    # Passat el cooldown, is_available ha de reactivar-lo
    after_cooldown = now + timedelta(seconds=601)
    assert cb.is_available(after_cooldown) is True
    assert cb.status == "active"
    assert cb.suspended_until is None

    # Registre d'èxit restableix tot
    cb.record_success(after_cooldown)
    assert cb.consecutive_failures == 0
    assert cb.status == "active"
    assert cb.last_error is None

    # Mètriques de to_dict
    d = cb.to_dict(after_cooldown + timedelta(seconds=45))
    assert d["status"] == "active"
    assert d["consecutive_failures"] == 0
    assert d["age_seconds"] == 45.0


# -----------------------------------------------------------------------------
# 3. Tests de Política de Freqüències Adaptativa
# -----------------------------------------------------------------------------
def test_adaptive_policy_determination():
    now_utc = datetime(2026, 11, 2, 18, 0, 0, tzinfo=timezone.utc)

    # 1. Sense partits -> NO_UPCOMING
    assert SurebetDaemon.determine_policy(None, now_utc) == POLICY_FREQUENCIES["NO_UPCOMING"]

    # 2. Partit a més de 4 hores (ex: a 5 hores) -> NO_UPCOMING
    t_5h = now_utc + timedelta(hours=5)
    assert SurebetDaemon.determine_policy(t_5h, now_utc) == POLICY_FREQUENCIES["NO_UPCOMING"]

    # 3. Partit a 3 hores -> WITHIN_4H
    t_3h = now_utc + timedelta(hours=3)
    assert SurebetDaemon.determine_policy(t_3h, now_utc) == POLICY_FREQUENCIES["WITHIN_4H"]

    # 4. Partit a 1.5 hores (90 min) -> WITHIN_2H
    t_90m = now_utc + timedelta(minutes=90)
    assert SurebetDaemon.determine_policy(t_90m, now_utc) == POLICY_FREQUENCIES["WITHIN_2H"]

    # 5. Partit imminent a 20 min -> IMMINENT
    t_20m = now_utc + timedelta(minutes=20)
    assert SurebetDaemon.determine_policy(t_20m, now_utc) == POLICY_FREQUENCIES["IMMINENT"]


# -----------------------------------------------------------------------------
# 4. Aïllament d'Errors entre Scrapers
# -----------------------------------------------------------------------------
def test_scrapers_error_isolation(tmp_path):
    """
    Si Bet365 falla repetidament, Winamax i Betway han de continuar
    proporcionant quotes sense que el cicle s'aturi ni bloquegi.
    """
    daemon = SurebetDaemon(dry_run=True, once=True)

    # Simulem que Bet365 té 3 fallades consecutives
    daemon.breakers["bet365"].record_failure("Blocked by bot detection")
    daemon.breakers["bet365"].record_failure("Blocked by bot detection")
    daemon.breakers["bet365"].record_failure("Blocked by bot detection")

    assert daemon.breakers["bet365"].status == "suspended"
    assert daemon.breakers["winamax"].status == "active"
    assert daemon.breakers["betway"].status == "active"

    # Verifiquem que els altres dos estan disponibles
    assert daemon.breakers["winamax"].is_available() is True
    assert daemon.breakers["betway"].is_available() is True
    assert daemon.breakers["bet365"].is_available() is False


# -----------------------------------------------------------------------------
# 5. Execució de Cicle complet en mode Dry-Run
# -----------------------------------------------------------------------------
def test_daemon_run_cycle_dry_run(tmp_path):
    daemon = SurebetDaemon(dry_run=True, once=True)

    fake_match = {
        "id": 99,
        "competition_id": "LALIGA",
        "date_time": "2026-11-03 20:00",
        "diff_hours": 2.5,
        "home_team": {"name": "Valencia CF"},
        "away_team": {"name": "Real Madrid"}
    }

    with patch.object(daemon, "get_upcoming_matches_window", return_value=([fake_match], "WITHIN_4H", 2.5)), \
         patch.object(daemon.winamax_scraper, "get_match_odds", return_value={"matched": False}), \
         patch.object(daemon.bet365_scraper, "get_match_odds", return_value={"matched": False}), \
         patch.object(daemon.betway_scraper, "get_match_odds", return_value={"matched": False}):

        result = daemon.run_cycle()
        assert result is not None
        assert "summary" in result
        assert "bookmakers_status" in result
        assert "winamax" in result["bookmakers_status"]
        assert "bet365" in result["bookmakers_status"]
        assert "betway" in result["bookmakers_status"]
        assert result["summary"]["total_surebets"] == 0


def test_daemon_export_live_isolated(tmp_path):
    live_file = tmp_path / "surebets_live.json"
    daemon = SurebetDaemon(dry_run=False, once=True, export_live_path=str(live_file))

    fake_payload = {
        "bookmakers_status": {"winamax": {"status": "ACTIVE"}},
        "surebets": []
    }
    daemon._update_web_data(fake_payload, "2026-11-02T20:00:00Z")

    assert live_file.exists()
    with open(live_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["metadata"]["export_mode"] == "LIVE_ISOLATED"
    assert data["arbitrage"]["bookmakers_status"]["winamax"]["status"] == "ACTIVE"
    assert "cost_guard" in data


# -----------------------------------------------------------------------------
# 6. Tests de Parseig de Dates i Finestra de Proximitat (DD.MM.YYYY, 48h, Timezones)
# -----------------------------------------------------------------------------
def test_parse_match_datetime_future_dotted():
    """
    1. Una data futura '08.11.2026 21:00' (format amb punts canònic de laliga.db)
       es parseja correctament assumint la zona horària Europe/Madrid (UTC+1 al novembre).
    """
    dt_str = "08.11.2026 21:00"
    dt_utc = SurebetDaemon.parse_match_datetime(dt_str)

    assert dt_utc is not None
    assert dt_utc.tzinfo == timezone.utc
    # 21:00 Europe/Madrid a l'hivern (CET, UTC+1) correspon a les 20:00 UTC
    expected_utc = datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)
    assert dt_utc == expected_utc


def test_past_match_dotted_is_discarded_by_window():
    """
    2. Una data passada amb el format amb punts es descarta a get_upcoming_matches_window()
       quan el partit ja ha començat fa més de 10 minuts (diff_hours < -0.15).
    """
    daemon = SurebetDaemon(dry_run=True, once=True)
    # Partit '08.11.2026 21:00' CET -> 20:00 UTC
    past_row = (1, "LALIGA", "08.11.2026 21:00", "Real Madrid", "Barcelona", "SCHEDULED")

    # Data de referència: 20 minuts després del xiulet inicial (20:20 UTC)
    ref_now_utc = datetime(2026, 11, 8, 20, 20, 0, tzinfo=timezone.utc)

    with patch.object(daemon.db, "get_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [past_row]
        mock_conn.return_value.cursor.return_value = mock_cursor

        matches, category, min_hours = daemon.get_upcoming_matches_window(now_utc=ref_now_utc)
        assert len(matches) == 0
        assert category == "NO_UPCOMING"
        assert min_hours == 999.0


def test_parse_match_datetime_future_with_seconds():
    """
    3. Una data futura amb segons en format punts '%d.%m.%Y %H:%M:%S' també es parseja.
    """
    dt_str = "08.11.2026 21:00:30"
    dt_utc = SurebetDaemon.parse_match_datetime(dt_str)

    assert dt_utc is not None
    assert dt_utc.tzinfo == timezone.utc
    expected_utc = datetime(2026, 11, 8, 20, 0, 30, tzinfo=timezone.utc)
    assert dt_utc == expected_utc


def test_parse_match_datetime_legacy_and_iso_formats():
    """
    4. Els formats antics (%Y-%m-%d %H:%M:%S, %Y-%m-%d %H:%M, %d/%m/%Y %H:%M, ISO)
       continuen funcionant amb plena compatibilitat cap enrere.
    """
    # 4.1 ISO amb Z
    dt1 = SurebetDaemon.parse_match_datetime("2026-11-08T20:00:00Z")
    assert dt1 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)

    # 4.2 ISO sense timezone (s'interpreta com Europe/Madrid)
    dt2 = SurebetDaemon.parse_match_datetime("2026-11-08T21:00:00")
    assert dt2 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)

    # 4.3 %Y-%m-%d %H:%M:%S
    dt3 = SurebetDaemon.parse_match_datetime("2026-11-08 21:00:00")
    assert dt3 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)

    # 4.4 %Y-%m-%d %H:%M
    dt4 = SurebetDaemon.parse_match_datetime("2026-11-08 21:00")
    assert dt4 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)

    # 4.5 %d/%m/%Y %H:%M
    dt5 = SurebetDaemon.parse_match_datetime("08/11/2026 21:00")
    assert dt5 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)

    # 4.6 %d/%m/%Y %H:%M:%S
    dt6 = SurebetDaemon.parse_match_datetime("08/11/2026 21:00:00")
    assert dt6 == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)


def test_parse_match_datetime_malformed_safe_handling():
    """
    5. Una data malformada o nul·la es gestiona de manera segura retornant None
       sense interrompre l'execució ni llançar excepcions no capturades.
    """
    malformed_inputs = [
        None,
        "",
        "   ",
        "invalid-date-string",
        "TBD",
        "Pendent",
        "postponed",
        "99.99.9999 99:99",
        "32.13.2026 25:99",
        123456,
        {"not": "a date"}
    ]
    for inp in malformed_inputs:
        res = SurebetDaemon.parse_match_datetime(inp)
        assert res is None, f"S'esperava None per l'input malformat: {inp!r}"


def test_get_upcoming_matches_window_with_dotted_dates():
    """
    6. get_upcoming_matches_window() retorna els partits futurs amb dates emmagatzemades amb punts,
       calcula el diff_hours correctament respecte now_utc i assigna la categoria de freqüència adequada.
    """
    daemon = SurebetDaemon(dry_run=True, once=True)

    # Simulem un estat determinista: now = 08.11.2026 17:00 UTC (18:00 hora local Madrid)
    ref_now_utc = datetime(2026, 11, 8, 17, 0, 0, tzinfo=timezone.utc)

    rows = [
        # Partit 1: 08.11.2026 21:00 Madrid (20:00 UTC) -> d'aquí a 3.0h (WITHIN_4H)
        (101, "LALIGA", "08.11.2026 21:00", "Real Madrid", "Barcelona", "SCHEDULED"),
        # Partit 2: 08.11.2026 15:00 Madrid (14:00 UTC) -> fa 3.0h (passat -> ha de ser descartat)
        (102, "LALIGA", "08.11.2026 15:00", "Sevilla", "Real Betis", "SCHEDULED"),
        # Partit 3: 09.11.2026 21:00 Madrid (20:00 UTC) -> d'aquí a 27.0h
        (103, "LALIGA", "09.11.2026 21:00", "Atletico Madrid", "Valencia", "SCHEDULED"),
        # Partit 4: data malformada -> ha de ser ignorat de forma segura
        (104, "LALIGA", "DATA_INVALIDA_TBD", "Villarreal", "Girona", "SCHEDULED")
    ]

    with patch.object(daemon.db, "get_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn.return_value.cursor.return_value = mock_cursor

        scheduled, category, min_hours = daemon.get_upcoming_matches_window(now_utc=ref_now_utc)

        # Han de quedar només el Partit 1 i el Partit 3
        assert len(scheduled) == 2
        p1 = scheduled[0]
        assert p1["id"] == 101
        assert p1["date_time"] == "08.11.2026 21:00"
        assert p1["kickoff_utc"] == datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)
        assert pytest.approx(p1["diff_hours"], 0.01) == 3.0

        p2 = scheduled[1]
        assert p2["id"] == 103
        assert p2["date_time"] == "09.11.2026 21:00"
        assert p2["kickoff_utc"] == datetime(2026, 11, 9, 20, 0, 0, tzinfo=timezone.utc)
        assert pytest.approx(p2["diff_hours"], 0.01) == 27.0

        # Categoria determinada pel partit més proper (3.0h -> WITHIN_4H)
        assert category == "WITHIN_4H"
        assert pytest.approx(min_hours, 0.01) == 3.0


def test_48h_window_filtering_logic():
    """
    7. El filtre de les pròximes 48 hores continua funcionant correctament:
       - Partits amb diff_hours <= 48.0 són prioritzats per a l'avaluació d'scrapers.
       - Partits més enllà de 48.0h queden exclosos si hi ha partits dins de la finestra.
       - Si no hi ha partits en 48.0h, s'activa el fallback per defecte (fins a 15 partits).
    """
    # 7.1 Cas amb partits dins i fora de la finestra de 48h
    sample_matches = [
        {"id": 1, "diff_hours": 1.5, "home_team": {"name": "A"}, "away_team": {"name": "B"}},
        {"id": 2, "diff_hours": 24.0, "home_team": {"name": "C"}, "away_team": {"name": "D"}},
        {"id": 3, "diff_hours": 47.9, "home_team": {"name": "E"}, "away_team": {"name": "F"}},
        {"id": 4, "diff_hours": 50.0, "home_team": {"name": "G"}, "away_team": {"name": "H"}},
        {"id": 5, "diff_hours": 72.0, "home_team": {"name": "I"}, "away_team": {"name": "J"}},
    ]

    matches_to_eval = [m for m in sample_matches if m.get("diff_hours", 999) <= 48.0]
    assert len(matches_to_eval) == 3
    assert [m["id"] for m in matches_to_eval] == [1, 2, 3]

    # 7.2 Cas sense cap partit en 48h -> Fallback als primers 15
    distant_matches = [
        {"id": i, "diff_hours": 60.0 + i, "home_team": {"name": f"H{i}"}, "away_team": {"name": f"A{i}"}}
        for i in range(25)
    ]
    matches_eval_fallback = [m for m in distant_matches if m.get("diff_hours", 999) <= 48.0]
    if not matches_eval_fallback:
        matches_eval_fallback = distant_matches[:15]

    assert len(matches_eval_fallback) == 15
    assert matches_eval_fallback[0]["id"] == 0
    assert matches_eval_fallback[-1]["id"] == 14



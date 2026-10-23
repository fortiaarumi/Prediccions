"""
Unit tests for league combo scheduling rules on kickoff day (Europe/Madrid).
Verifies:
1. Day before kickoff -> No generation (WAITING_FOR_MATCHDAY).
2. Kickoff day before match with referees -> Generates (READY).
3. Kickoff day with insufficient referees -> Retries / waits (WAITING_FOR_REFEREES).
4. Kickoff time passed -> No late prematch generation (MATCH_ALREADY_STARTED).
5. Already saved / frozen combos -> No duplication (ALREADY_SAVED).
6. State already predicted -> No duplication (ALREADY_PREDICTED).
7. Europe/Madrid timezone accuracy across UTC boundaries.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch
import pytest

from daily_runner import should_generate_league_combos, MADRID_TZ


def test_scheduling_day_before_kickoff():
    """Rule 4: Do not generate on the day before the match."""
    # First match is on 2026-10-24 at 21:00 Europe/Madrid
    match_dt = datetime(2026, 10, 24, 21, 0, tzinfo=MADRID_TZ)
    # Current time is 2026-10-23 at 14:00 Europe/Madrid
    simulated_now = datetime(2026, 10, 23, 14, 0, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []

    can_gen, reason = should_generate_league_combos(
        comp_id="LALIGA",
        jornada=10,
        first_match_dt=match_dt,
        current_dt_madrid=simulated_now,
        db=mock_db,
        check_referees=False
    )

    assert not can_gen
    assert "WAITING_FOR_MATCHDAY" in reason


def test_scheduling_kickoff_day_before_match_ready():
    """Rules 1, 2, 5: Same day before kickoff with sufficient referees -> READY."""
    # First match is on 2026-10-23 at 21:00 Europe/Madrid
    match_dt = datetime(2026, 10, 23, 21, 0, tzinfo=MADRID_TZ)
    # Current time is 2026-10-23 at 11:30 Europe/Madrid
    simulated_now = datetime(2026, 10, 23, 11, 30, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []

    with patch("daily_runner.has_sufficient_referees", return_value=True):
        can_gen, reason = should_generate_league_combos(
            comp_id="LALIGA",
            jornada=10,
            first_match_dt=match_dt,
            current_dt_madrid=simulated_now,
            db=mock_db,
            check_referees=True
        )

    assert can_gen
    assert "READY" in reason


def test_scheduling_kickoff_day_missing_referees():
    """Rule 6: Kickoff day before match, but referee information is insufficient."""
    match_dt = datetime(2026, 10, 23, 21, 0, tzinfo=MADRID_TZ)
    simulated_now = datetime(2026, 10, 23, 11, 30, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []

    with patch("daily_runner.has_sufficient_referees", return_value=False), \
         patch("daily_runner.update_referees_for_round", return_value=0):
        can_gen, reason = should_generate_league_combos(
            comp_id="LALIGA",
            jornada=10,
            first_match_dt=match_dt,
            current_dt_madrid=simulated_now,
            db=mock_db,
            check_referees=True
        )

    assert not can_gen
    assert "WAITING_FOR_REFEREES" in reason


def test_scheduling_after_kickoff_rejected():
    """Rule 8: If kickoff time has arrived/passed, do not generate late prematch combo."""
    match_dt = datetime(2026, 10, 23, 21, 0, tzinfo=MADRID_TZ)
    # Current time is 21:05 (past kickoff)
    simulated_now = datetime(2026, 10, 23, 21, 5, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []

    can_gen, reason = should_generate_league_combos(
        comp_id="LALIGA",
        jornada=10,
        first_match_dt=match_dt,
        current_dt_madrid=simulated_now,
        db=mock_db,
        check_referees=False
    )

    assert not can_gen
    assert "MATCH_ALREADY_STARTED" in reason


def test_scheduling_already_saved_frozen():
    """Rule 7: Once generated and saved, combos are frozen and never duplicated."""
    match_dt = datetime(2026, 10, 23, 21, 0, tzinfo=MADRID_TZ)
    simulated_now = datetime(2026, 10, 23, 14, 0, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    # Mock that 2 combos already exist in DB
    mock_db.get_combos_for_jornada.return_value = [
        {"combo_id": "COMBO_1"},
        {"combo_id": "COMBO_2"}
    ]

    can_gen, reason = should_generate_league_combos(
        comp_id="LALIGA",
        jornada=10,
        first_match_dt=match_dt,
        current_dt_madrid=simulated_now,
        db=mock_db,
        check_referees=False
    )

    assert not can_gen
    assert "ALREADY_SAVED" in reason


def test_scheduling_state_already_predicted():
    """Rule 7: Pipeline state reflects already predicted jornada."""
    match_dt = datetime(2026, 10, 23, 21, 0, tzinfo=MADRID_TZ)
    simulated_now = datetime(2026, 10, 23, 14, 0, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []
    state = {"LALIGA": {"last_predicted_jornada": 10}}

    can_gen, reason = should_generate_league_combos(
        comp_id="LALIGA",
        jornada=10,
        first_match_dt=match_dt,
        current_dt_madrid=simulated_now,
        db=mock_db,
        state=state,
        check_referees=False
    )

    assert not can_gen
    assert "ALREADY_PREDICTED" in reason


def test_scheduling_timezone_utc_handling():
    """Rule 2: Handles UTC datetime inputs and accurately checks Europe/Madrid calendar date."""
    # Match is at 2026-10-23 22:30 UTC -> which is 2026-10-24 00:30 in Madrid (+2)
    match_utc = datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc)
    # Current time in Madrid: 2026-10-23 15:00 (+2)
    # In Madrid, match date is Oct 24, today is Oct 23 -> WAITING_FOR_MATCHDAY
    simulated_madrid = datetime(2026, 10, 23, 15, 0, tzinfo=MADRID_TZ)

    mock_db = MagicMock()
    mock_db.get_combos_for_jornada.return_value = []

    can_gen, reason = should_generate_league_combos(
        comp_id="LALIGA",
        jornada=10,
        first_match_dt=match_utc,
        current_dt_madrid=simulated_madrid,
        db=mock_db,
        check_referees=False
    )

    assert not can_gen
    assert "WAITING_FOR_MATCHDAY" in reason

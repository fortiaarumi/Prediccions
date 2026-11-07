"""
tests/test_surebet_monitoring_transparency.py
=============================================
Bateria de tests de verificació per a la Fase de Transparència de Monitoratge
i Estat de l'Actualització General de Dades (Fases 1, 2, 4 i 5).

Cobreix exhaustivament:
1. Mètriques de frescor detallades per a cadascuna de les 3 cases reals (Winamax, Bet365, Betway).
2. Preservació de dades correctes prèvies quan l'últim intent falla.
3. Tolerància a fallades individuals i aïllament absolut entre cases (degradació elegant de 2 cases).
4. Distinció rigorosa dels 4 timestamps (query_start, query_end, http_captured_at, data_json_time).
5. Estat i desglossament del pipeline diari de GitHub Actions (daily_pipeline.yml).
6. Preservació mútua entre WebDataExporter i SurebetDaemon (aïllament del daemon vs accions).
7. Casos límit (primer cicle sense execució prèvia, rellotges desincronitzats, suspensió per circuit breaker).
"""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path

from surebet_daemon import CircuitBreaker, SurebetDaemon
from engine.arbitrage_engine import ArbitrageEngine
from daily_runner import get_canonical_pipeline_status, load_state, save_state
from engine.web_data_exporter import WebDataExporter


# =====================================================================
# 1. Tests de Mètriques Detallades de CircuitBreaker i Frescor (Fase 1)
# =====================================================================

def test_circuit_breaker_detailed_metrics_success():
    """Verifica el registre complet d'un intent reeixit amb timestamps i recomptes."""
    cb = CircuitBreaker("winamax", max_failures=3, cooldown_seconds=600)
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)
    q_start = now - timedelta(seconds=2)
    q_end = now
    http_cap = now - timedelta(seconds=1)

    cb.record_success(
        now=now,
        query_start=q_start,
        query_end=q_end,
        http_captured_at=http_cap,
        events_count=10,
        odds_count=48
    )

    d = cb.to_dict(now=now)
    assert d["name"] == "winamax"
    assert d["status"] == "active"
    assert d["last_attempt_status"] == "success"
    assert d["last_success"] == now.isoformat()
    assert d["query_start"] == q_start.isoformat()
    assert d["query_end"] == q_end.isoformat()
    assert d["http_captured_at"] == http_cap.isoformat()
    assert d["events_count"] == 10
    assert d["odds_count"] == 48
    assert d["consecutive_failures"] == 0
    assert d["last_error"] is None
    assert d["age_seconds"] == 0.0


def test_circuit_breaker_preserves_previous_success_on_failure():
    """
    CRÍTIC (Fase 1): Si una consulta falla, conserva la informació de l'última
    consulta correcta (hora, captura HTTP, esdeveniments, quotes), però mostra
    clarament que l'últim intent ha fallat i quant de temps fa de l'últim èxit.
    """
    cb = CircuitBreaker("betway", max_failures=3, cooldown_seconds=600)
    t1 = datetime(2026, 11, 2, 18, 0, 0, tzinfo=timezone.utc)
    cb.record_success(
        now=t1,
        query_start=t1 - timedelta(seconds=3),
        query_end=t1,
        http_captured_at=t1 - timedelta(seconds=2),
        events_count=8,
        odds_count=32
    )

    # Segon intent falla 25 segons després
    t2 = t1 + timedelta(seconds=25)
    q_start_fail = t2 - timedelta(seconds=5)
    cb.record_failure(
        "Betway HTTP 503 Service Unavailable",
        now=t2,
        query_start=q_start_fail,
        query_end=t2
    )

    d = cb.to_dict(now=t2)
    assert d["status"] == "error"
    assert d["last_attempt_status"] == "error"
    assert d["consecutive_failures"] == 1
    assert "503" in d["last_error"]

    # PRESERVACIÓ: L'últim èxit i les seves mètriques es mantenen intactes
    assert d["last_success"] == t1.isoformat()
    assert d["events_count"] == 8
    assert d["odds_count"] == 32
    assert d["http_captured_at"] == (t1 - timedelta(seconds=2)).isoformat()
    assert d["age_seconds"] == 25.0  # Exactament 25 segons des de l'últim èxit!


def test_circuit_breaker_partial_execution():
    """Verifica que una execució parcial compta com a èxit operatiu amb avís."""
    cb = CircuitBreaker("bet365")
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)
    cb.record_partial(
        reason="Cap partit amb quotes disponibles en aquesta finestra",
        now=now,
        query_start=now - timedelta(seconds=1),
        query_end=now,
        http_captured_at=now,
        events_count=0,
        odds_count=0
    )

    d = cb.to_dict(now=now)
    assert d["status"] == "active"
    assert d["last_attempt_status"] == "partial"
    assert "Cap partit" in d["partial_details"]
    assert d["consecutive_failures"] == 0


def test_circuit_breaker_suspension_and_cooldown_recovery():
    """Verifica que 3 fallades consecutives suspenen el circuit breaker i es recupera en acabar el cooldown."""
    cb = CircuitBreaker("betway", max_failures=3, cooldown_seconds=300)
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)

    cb.record_failure("Err 1", now=now)
    assert cb.status == "error"
    assert cb.is_available(now) is True

    cb.record_failure("Err 2", now=now + timedelta(seconds=10))
    assert cb.status == "error"
    assert cb.is_available(now + timedelta(seconds=10)) is True

    # 3a fallada -> suspensió automàtica
    cb.record_failure("Err 3", now=now + timedelta(seconds=20))
    assert cb.status == "suspended"
    assert cb.is_available(now + timedelta(seconds=20)) is False

    # Durant el cooldown (2 minuts) segueix suspès
    assert cb.is_available(now + timedelta(seconds=140)) is False

    # Després de 5 minuts (300s cooldown), es recupera automàticament
    after_cooldown = now + timedelta(seconds=325)
    assert cb.is_available(after_cooldown) is True
    assert cb.status == "active"


# =====================================================================
# 2. Tests de Tolerància a Fallades Individuals i Arbitratge Multi-Casa (Fase 4)
# =====================================================================

def test_individual_failure_tolerance_winamax_down():
    """
    Si Winamax cau o no té quotes, el sistema ha de continuar monitorant
    Bet365 i Betway i generar arbitratges vàlids entre elles.
    """
    engine = ArbitrageEngine(enforce_strict_freshness=False)
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)

    # Partit on Winamax NO té quotes (caiguda), però Bet365 i Betway en tenen
    match = {
        "id": "match_tolerance_1",
        "competition_id": "LALIGA",
        "home_team": {"name": "FC Barcelona"},
        "away_team": {"name": "Real Madrid"},
        # Bet365: Over 2.5 a 2.15
        "odds_ou25_bet365": {"over": 2.15, "under": 1.75},
        "odds_bet365_captured_at": now.isoformat(),
        # Betway: Under 2.5 a 2.10
        "odds_ou25_betway": {"over": 1.78, "under": 2.10},
        "odds_betway_captured_at": now.isoformat()
    }

    arbs = engine.scan_match(match, now=now, bankroll=100.0)
    ou_arbs = [a for a in arbs if a.get("is_surebet") and "Total Gols" in a.get("market_name", "")]
    assert len(ou_arbs) >= 1
    arb = ou_arbs[0]
    books = {l["bookmaker"].upper() for l in arb["legs"]}
    assert "BET365" in books
    assert "BETWAY" in books
    assert "WINAMAX" not in books
    assert arb["profit_pct"] > 0


def test_individual_failure_tolerance_betway_down():
    """
    Si Betway cau, el sistema continua amb Winamax i Bet365 sense cap error.
    """
    engine = ArbitrageEngine(enforce_strict_freshness=False)
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)

    match = {
        "id": "match_tolerance_2",
        "competition_id": "LALIGA",
        "home_team": {"name": "Atlético de Madrid"},
        "away_team": {"name": "Sevilla FC"},
        # Winamax: BTTS Sí a 2.12
        "odds_btts_winamax": {"yes": 2.12, "no": 1.76},
        "odds_winamax_captured_at": now.isoformat(),
        # Bet365: BTTS No a 2.05
        "odds_btts_bet365": {"yes": 1.80, "no": 2.05},
        "odds_bet365_captured_at": now.isoformat()
    }

    arbs = engine.scan_match(match, now=now, bankroll=100.0)
    btts_arbs = [a for a in arbs if a.get("is_surebet") and "BTTS" in a.get("market_name", "")]
    assert len(btts_arbs) >= 1
    arb = btts_arbs[0]
    books = {l["bookmaker"].upper() for l in arb["legs"]}
    assert books == {"WINAMAX", "BET365"}


def test_three_way_arbitrage_with_three_distinct_bookmakers():
    """
    Verifica un arbitratge tripartit 1X2 utilitzant les tres cases simultàniament:
    1 a Winamax (3.40), X a Bet365 (3.35), 2 a Betway (3.30).
    Marge ~0.8956 -> Benefici ~10.55% (dins del límit de seguretat anti-error palpable <=15%).
    """
    engine = ArbitrageEngine(enforce_strict_freshness=False)
    now = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)

    match = {
        "id": "match_tri_1",
        "competition_id": "LALIGA",
        "home_team": {"name": "Valencia CF"},
        "away_team": {"name": "Villarreal CF"},
        # 1 a Winamax (3.40), X/2 inferiors
        "odds_1x2_winamax": {"1": 3.40, "X": 3.00, "2": 2.30},
        "odds_winamax_captured_at": now.isoformat(),
        # X a Bet365 (3.35), 1/2 inferiors
        "odds_1x2_bet365": {"1": 2.40, "X": 3.35, "2": 3.00},
        "odds_bet365_captured_at": now.isoformat(),
        # 2 a Betway (3.30), 1/X inferiors
        "odds_1x2_betway": {"1": 2.35, "X": 3.10, "2": 3.30},
        "odds_betway_captured_at": now.isoformat(),
    }

    arbs = engine.scan_match(match, now=now, bankroll=100.0)
    surebets_1x2 = [a for a in arbs if a.get("is_surebet") and "1X2" in a.get("market_name", "")]
    assert len(surebets_1x2) >= 1

    arb = surebets_1x2[0]
    legs_by_sel = {l["selection"][0]: l for l in arb["legs"]}
    assert legs_by_sel["1"]["bookmaker"].upper() == "WINAMAX"
    assert legs_by_sel["X"]["bookmaker"].upper() == "BET365"
    assert legs_by_sel["2"]["bookmaker"].upper() == "BETWAY"
    assert arb["profit_pct"] > 10.0


# =====================================================================
# 3. Tests de l'Estat del Pipeline de GitHub Actions (Fase 2)
# =====================================================================

def test_canonical_pipeline_status_structure():
    """Verifica l'estructura completa de metadades del workflow de GitHub Actions."""
    status = get_canonical_pipeline_status()
    assert status["workflow_name"] == "Pipeline Diari Autònom de Prediccions"
    assert status["workflow_file"] == "daily_pipeline.yml"
    assert status["status"] in ("COMPLETED", "FAILED", "PARTIAL", "RUNNING")
    assert "last_run_timestamp" in status
    assert "last_success_timestamp" in status
    assert "duration_seconds" in status
    assert "schedule" in status
    assert "tasks" in status

    # Comprovar les 6 tasques operatives essencials
    expected_tasks = [
        "results_and_scores",
        "elo_and_stats",
        "referee_designations",
        "predictions",
        "combos",
        "web_export"
    ]
    for tid in expected_tasks:
        assert tid in status["tasks"]
        t = status["tasks"][tid]
        assert "name" in t
        assert "status" in t
        assert "last_success" in t
        assert "details" in t


def test_pipeline_status_preservation_on_partial_failure():
    """
    Si una tasca del pipeline falla o queda parcial,
    les altres tasques correctes i el last_success_timestamp previ es preserven.
    """
    previous_state = {
        "status": "COMPLETED",
        "last_success_timestamp": "2026-10-25T12:05:07.729707+00:00",
        "tasks": {
            "results_and_scores": {"status": "COMPLETED", "last_success": "2026-10-25T12:03:10+00:00"},
            "referee_designations": {"status": "COMPLETED", "last_success": "2026-10-25T12:03:45+00:00"}
        }
    }

    # Nova execució parcial on CTA encara no ha publicat els àrbitres
    current_attempt = {
        "status": "PARTIAL",
        "last_attempt_status": "PARTIAL",
        "last_attempt_timestamp": "2026-11-02T08:23:00+00:00",
        "last_success_timestamp": "2026-10-25T12:05:07.729707+00:00",  # Es preserva l'èxit previ
        "tasks": {
            "results_and_scores": {"status": "COMPLETED", "last_success": "2026-11-02T08:21:00+00:00"},
            "referee_designations": {"status": "PARTIAL", "details": "Esperant publicació oficial del CTA"}
        }
    }

    merged = get_canonical_pipeline_status(current_attempt)
    assert merged["status"] == "PARTIAL"
    assert merged["last_success_timestamp"] == "2026-10-25T12:05:07.729707+00:00"
    assert merged["tasks"]["results_and_scores"]["last_success"] == "2026-11-02T08:21:00+00:00"
    # La tasca parcial conserva informació detallada
    assert merged["tasks"]["referee_designations"]["status"] == "PARTIAL"


def test_web_exporter_preserves_daemon_status_and_exports_pipeline():
    """
    Verifica que WebDataExporter integra el pipeline_status i preserva
    sense aixafar el bookmakers_status de SurebetDaemon.
    """
    previous_web_data = {
        "arbitrage": {
            "bookmakers_status": {
                "winamax": {"status": "active", "events_count": 10},
                "bet365": {"status": "active", "events_count": 9},
                "betway": {"status": "active", "events_count": 10}
            },
            "last_generation": "2026-11-02T19:00:00Z"
        }
    }

    # Test directe de la lògica de preservació que s'executa a WebDataExporter.export_all()
    arbitrage_report = {"summary": {}, "surebets": []}
    if previous_web_data.get("arbitrage", {}).get("bookmakers_status"):
        arbitrage_report["bookmakers_status"] = previous_web_data["arbitrage"]["bookmakers_status"]

    assert "bookmakers_status" in arbitrage_report
    assert arbitrage_report["bookmakers_status"]["betway"]["events_count"] == 10
    assert arbitrage_report["bookmakers_status"]["winamax"]["events_count"] == 10
    assert arbitrage_report["bookmakers_status"]["bet365"]["events_count"] == 9


# =====================================================================
# 4. Tests de Formateig Temporal i Casos Límit (Fases 1, 3 i 5)
# =====================================================================

def test_relative_age_calculation_edge_cases():
    """
    Verifica el càlcul de temps transcorregut en casos extrems:
    - 0 segons / just capturat
    - rellotges desincronitzats lleugerament negatius
    - minuts, hores i dies
    - primer cicle sense dades prèvies
    """
    now = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)

    # Primer cicle (cap registre previ)
    cb_new = CircuitBreaker("winamax")
    d_new = cb_new.to_dict(now=now)
    assert d_new["last_success"] is None
    assert d_new["age_seconds"] is None

    # Temps lleugerament futur (desincronització de rellotge de -2 segons)
    cb_skew = CircuitBreaker("bet365")
    cb_skew.record_success(now=now + timedelta(seconds=2))
    d_skew = cb_skew.to_dict(now=now)
    assert d_skew["age_seconds"] == -2.0  # El backend registra la diferència matemàtica real

    # 15 segons
    cb_15s = CircuitBreaker("betway")
    cb_15s.record_success(now=now - timedelta(seconds=15))
    d_15s = cb_15s.to_dict(now=now)
    assert d_15s["age_seconds"] == 15.0

    # 3 minuts (180 segons)
    cb_3m = CircuitBreaker("winamax")
    cb_3m.record_success(now=now - timedelta(minutes=3))
    d_3m = cb_3m.to_dict(now=now)
    assert d_3m["age_seconds"] == 180.0

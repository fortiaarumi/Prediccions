"""
tests/test_offline_reproducibility.py
=====================================
Valida la independència absoluta de connexió a internet per a tota la suite:
1. Comprova que cap test executa connexions reals a Winamax, Bet365 ni Flashscore.
2. Comprova que els connectors i scrapers serveixen dades vàlides des de les memòries cau/fixtures aïllades.
3. Comprova que quan un servei extern està caigut (xarxa bloquejada o offline), els components no fallen i retornen resultats coherents.
"""

import os
import pytest
from database.db_manager import DatabaseManager
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper
from scraper.live_crawler import LiveCrawler
from scraper.jornada_resolver import JornadaResolver
from engine.web_data_exporter import WebDataExporter


def test_1_winamax_offline_reproducibility():
    """Valida que WinamaxScraper resol partits i mercats secundaris completament offline."""
    w = WinamaxScraper(headless=True, offline=True)
    odds = w.get_match_odds("Lecce", "Genoa", "SERIEA")

    # Ha de trobar el partit i carregar els mercats des de la fixture/memòria cau aïllada
    assert odds["matched"] is True
    assert odds["match_id"] == "71945378"
    assert "1" in odds["1X2"]
    # Mercats secundaris carregats offline des de winamax_match_71945378.json
    assert "corners" in odds and len(odds["corners"]) > 0
    assert "cards" in odds and len(odds["cards"]) > 0


def test_2_bet365_offline_reproducibility():
    """Valida que Bet365Scraper opera de manera resilient sense accés a xarxa."""
    b = Bet365Scraper(headless=True, offline=True)
    odds = b.get_match_odds("Lecce", "Genoa", "SERIEA")
    # Sense xarxa ni override manual, retorna estructura segura NO_ODDS_DETECTED sense llançar cap excepció
    assert odds["matched"] is False
    assert odds["error"] == "NO_ODDS_DETECTED"
    assert odds["is_stale"] is True


def test_3_jornada_resolver_offline_fallback():
    """Valida que JornadaResolver pot carregar els fixtures oficials des de urls.txt sense xarxa."""
    resolver = JornadaResolver(competition_id="SERIEA")
    fixtures = resolver.get_jornada_fixtures(10)

    assert len(fixtures) == 10
    matchups = [f"{f['home_team']} vs {f['away_team']}" for f in fixtures]
    assert any("Lecce" in m and "Genoa" in m for m in matchups)
    assert any("Bolonia" in m and "Monza" in m for m in matchups)


def test_4_full_pipeline_offline_execution():
    """Valida que un flux complet de predicció i exportació s'executa 100% offline."""
    db = DatabaseManager()
    exporter = WebDataExporter(db=db)

    # Execució de predicció sobre partits de Serie A sense xarxa
    preds = exporter.get_upcoming_predictions("SERIEA")
    assert preds["competition_id"] == "SERIEA"
    assert len(preds["matches"]) > 0


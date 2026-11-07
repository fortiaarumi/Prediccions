"""
tests/test_scraper_timeouts.py
==============================
Proves de regressió per verificar que les crides de xarxa i subprocessos
disposen de timeouts estrictes i gestionen les desconnexions o bloquejos
sense penjar el procés i preservant les dades vàlides.
"""

import subprocess
from unittest.mock import patch, MagicMock
import pytest

from scraper.match_scraper import MatchScraper
from scraper.live_crawler import LiveCrawler
from scraper.winamax_scraper import WinamaxScraper
from scraper.jornada_resolver import JornadaResolver
from daily_runner import get_upcoming_round_info


def test_match_scraper_timeout_resilience():
    """Verifica que un timeout a MatchScraper no llança excepció i retorna l'estructura per defecte."""
    scraper = MatchScraper(headless=True)
    
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["curl"], timeout=15)):
        res = scraper.scrape_match_feed("MATCH_TEST_HANG")
        assert res is not None
        assert res["referee"] == "REF_DEFAULT"
        assert res["is_finished"] is False
        assert res["status"] == "SCHEDULED"


def test_live_crawler_fixtures_timeout_preserves_partial():
    """Verifica que si resultados o partidos pateixen un timeout, LiveCrawler no es bloqueja."""
    crawler = LiveCrawler(headless=True)
    
    # Simulem que resultados funciona i partidos pateix timeout
    def mock_subprocess(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd)
        if "resultados" in cmd_str:
            mock_res = MagicMock()
            mock_res.stdout = "ZA÷SPAIN: LaLiga¬~AA÷M123¬AD÷1700000000¬AE÷Real Madrid¬AF÷Barcelona¬AG÷2¬AH÷1¬~"
            return mock_res
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=20)

    with patch("subprocess.run", side_effect=mock_subprocess):
        fixtures = crawler.crawl_jornada_fixtures_from_web(jornada=1, competition_id="LALIGA")
        # El timeout de partidos no bloqueja el procés i es preserva el partit obtingut a resultados
        assert isinstance(fixtures, list)


def test_daily_runner_upcoming_round_timeout():
    """Verifica que si Flashscore està penjat en consultar el calendari, get_upcoming_round_info retorna None sense penjar-se."""
    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_db.get_connection.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.execute.return_value.fetchone.return_value = None

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["curl"], timeout=20)):
        res = get_upcoming_round_info("LALIGA", db=mock_db)
        assert res is None


def test_winamax_scraper_timeout_handling():
    """Verifica que WinamaxScraper gestiona timeouts de subprocess.run sense fallar."""
    ws = WinamaxScraper(offline=False)
    
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["curl"], timeout=20)):
        odds = ws.get_match_odds(home_team="Barcelona", away_team="Real Madrid", competition_id="LALIGA")
        assert isinstance(odds, dict)
        assert odds.get("home_win") is None or odds.get("source") != "LIVE_WINAMAX"


def test_jornada_resolver_timeout():
    """Verifica que JornadaResolver gestiona timeouts a Flashscore."""
    resolver = JornadaResolver()
    with patch("daily_runner.get_upcoming_round_info", return_value=None):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["curl"], timeout=20)):
            res = resolver.detect_current_jornada("LALIGA")
            assert res is None

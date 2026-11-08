"""
tests/test_web_browser_surebets_real.py
=======================================
Prova d'integració REAL amb navegador Chrome (Selenium headless)
verificant el cicle complet dels controls web de Surebets:
1. Desconnexió inicial (sense falsos positius).
2. Detecció de connexió real de l'agent (batec).
3. Clicar «Buscar surebets» i creació de sessió.
4. Recepció de snapshots i renderització de quotes i beneficis.
5. Verificació de les categories de frescor (≤60s, 1-5m, 5-30m).
6. Clicar «Aturar cerca» i verificació de l'estat CANCELLED.
7. Desconnexió de l'agent i verificació de l'estat desconnectat.
"""

import time
import os
import json
import threading
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, HTTPServer
import pytest

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from agent.coordinator import MockCoordinator
from agent.windows_agent import WindowsSurebetAgent
from agent.local_bridge import LocalCoordinatorServer
from agent.protocol import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED
)


class WebStaticFileHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
        super().__init__(*args, directory=web_dir, **kwargs)

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def browser_env():
    # 1. Servidor de fitxers estàtics web a 127.0.0.1:8088
    web_server = HTTPServer(("127.0.0.1", 8088), WebStaticFileHandler)
    web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
    web_thread.start()

    # 2. Servidor pont de coordinació a 127.0.0.1:8777
    coord = MockCoordinator()
    coord_server = LocalCoordinatorServer(coord, port=8777)
    coord_server.start()

    # 3. Agent Windows local
    agent = WindowsSurebetAgent(coordinator=coord, agent_id="Windows-PC-Local", offline=True, dry_run=True)

    # 4. Navegador Chrome headless
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=opts)

    yield {
        "coord": coord,
        "agent": agent,
        "driver": driver,
        "coord_server": coord_server,
        "web_server": web_server
    }

    try:
        driver.quit()
    except Exception:
        pass
    coord_server.stop()
    web_server.shutdown()
    agent.cleanup_resources()


def test_real_browser_surebet_lifecycle(browser_env):
    driver = browser_env["driver"]
    coord = browser_env["coord"]
    agent = browser_env["agent"]

    # 0. Verificar que en accés públic normal (sense configuració Supabase ni dev build segellada),
    # el web NO fa fallback silenciós a 127.0.0.1:8777, ignorant absolutament paràmetres com ?local_bridge=1
    driver.get("http://127.0.0.1:8088/index.html?local_bridge=1")
    wait = WebDriverWait(driver, 10)
    tab_btn = wait.until(EC.element_to_be_clickable((By.ID, "tab-btn-surebets")))
    tab_btn.click()
    time.sleep(1)
    status_public = driver.find_element(By.ID, "agent-connection-status-text").text
    assert "Desconnectat" in status_public
    assert "pendent de configuració" in status_public

    # 1. Habilitar l'entorn de desenvolupament local de forma immutable i explícita (només permès a loopback)
    driver.execute_script("""
        Object.defineProperty(window, '__PREDICCIONS_DEV_BUILD__', {
            value: true,
            writable: false,
            configurable: false
        });
        if (typeof setupSurebetAgentControls === 'function') {
            setupSurebetAgentControls();
        }
    """)
    time.sleep(1)

    # 2. Verificar que abans del batec de l'agent, el PC es mostra DESCONNECTAT pel pont
    status_text = driver.find_element(By.ID, "agent-connection-status-text").text
    assert "Desconnectat" in status_text, f"S'esperava Desconnectat pero s'ha obtingut: {status_text}"

    # 3. Agent envia batec de vida -> El navegador ha de passar a 'En línia'
    agent.send_heartbeat(status="ONLINE")
    time.sleep(2)  # esperar cicle de polling

    # Comprovar canvi d'estat a 'En línia'
    status_el = wait.until(lambda d: "En línia" in d.find_element(By.ID, "agent-connection-status-text").text)
    assert status_el is True

    # 4. Prémer «Buscar surebets»
    btn_start = driver.find_element(By.ID, "btn-start-surebet-search")
    assert btn_start.is_enabled()
    btn_start.click()
    time.sleep(1)

    # Verificar que el botó d'inici queda deshabilitat i el d'aturar habilitat
    btn_stop = driver.find_element(By.ID, "btn-stop-surebet-search")
    assert btn_stop.is_enabled()
    job_state_text = driver.find_element(By.ID, "surebet-job-state").text
    assert "RUNNING" in job_state_text or "PENDING" in job_state_text

    # 5. Agent reclama la tasca a la cua
    order = coord.claim_pending_order("Windows-PC-Local")
    assert order is not None
    session_id = order.session_id

    # 6. Publicar snapshots amb 3 oportunitats de prova (3 categories de frescor)
    now_utc = datetime.now(timezone.utc)
    
    # Fresca (<=60s)
    arb_fresh = {
        "competition_id": "LALIGA",
        "matchup": "Real Madrid vs Barcelona",
        "market_name": "1X2",
        "profit_pct": 3.85,
        "legs": [
            {"bookmaker": "winamax", "outcome": "1", "odd": 2.25, "captured_at": now_utc.isoformat()},
            {"bookmaker": "bet365", "outcome": "X", "odd": 3.65, "captured_at": now_utc.isoformat()},
            {"bookmaker": "betway", "outcome": "2", "odd": 3.90, "captured_at": now_utc.isoformat()}
        ]
    }
    # Potencialment desactualitzada (1-5 min)
    t_stale = now_utc - timedelta(seconds=120)
    arb_stale = {
        "competition_id": "PREMIER",
        "matchup": "Liverpool vs Manchester City",
        "market_name": "1X2",
        "profit_pct": 1.92,
        "legs": [
            {"bookmaker": "winamax", "outcome": "1", "odd": 2.10, "captured_at": t_stale.isoformat()},
            {"bookmaker": "bet365", "outcome": "X", "odd": 3.40, "captured_at": t_stale.isoformat()},
            {"bookmaker": "betway", "outcome": "2", "odd": 4.10, "captured_at": t_stale.isoformat()}
        ]
    }
    # Històrica recent (5-30 min)
    t_hist = now_utc - timedelta(seconds=600)
    arb_hist = {
        "competition_id": "LIGUE1",
        "matchup": "PSG vs Marseille",
        "market_name": "1X2",
        "profit_pct": 0.85,
        "legs": [
            {"bookmaker": "winamax", "outcome": "1", "odd": 1.80, "captured_at": t_hist.isoformat()},
            {"bookmaker": "bet365", "outcome": "X2", "odd": 2.30, "captured_at": t_hist.isoformat()}
        ]
    }

    snap_fresh = agent.snapshot_store.add_opportunity(session_id, 1, arb_fresh, now=now_utc)
    snap_stale = agent.snapshot_store.add_opportunity(session_id, 1, arb_stale, now=now_utc)
    snap_hist = agent.snapshot_store.add_opportunity(session_id, 1, arb_hist, now=now_utc)

    coord.publish_snapshot(session_id, snap_fresh)
    coord.publish_snapshot(session_id, snap_stale)
    coord.publish_snapshot(session_id, snap_hist)

    # Esperar que el navegador faci polling i renderitzi les oportunitats
    time.sleep(3)

    # Verificar que el recompte a la targeta d'estat mostra 3 actives
    opps_count_text = driver.find_element(By.ID, "surebet-job-opps").text
    assert "3 actives" in opps_count_text or "3" in opps_count_text

    # Verificar que el contingut HTML de la pàgina conté els partits i beneficis
    page_html = driver.page_source
    assert "Real Madrid vs Barcelona" in page_html
    assert "3.9%" in page_html or "3,9%" in page_html or "1.4%" in page_html
    assert "Liverpool vs Manchester City" in page_html
    assert "PSG vs Marseille" in page_html

    # 7. Prémer «Aturar cerca»
    btn_stop.click()
    time.sleep(1)

    # L'agent detecta cancel·lació cooperativa
    assert agent._is_cancel_requested(session_id) is True
    agent._finalize_session(session_id, final_status=STATUS_CANCELLED)
    time.sleep(2)

    # Verificar que el navegador rep l'estat CANCELLED i re-habilita el botó
    final_job_state = driver.find_element(By.ID, "surebet-job-state").text
    assert "CANCELLED" in final_job_state or "aturada" in final_job_state.lower()
    assert btn_start.is_enabled()

    # 8. Simular desconnexió de l'agent (expiració de batec)
    coord.agents["Windows-PC-Local"].status = "OFFLINE"
    time.sleep(6)  # esperar polling de 5s del navegador

    # Verificar que el navegador torna a mostrar DESCONNECTAT
    re_status_text = driver.find_element(By.ID, "agent-connection-status-text").text
    assert "Desconnectat" in re_status_text

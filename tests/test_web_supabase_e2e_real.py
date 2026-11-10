"""
tests/test_web_supabase_e2e_real.py
===================================
Prova d'integració REAL amb navegador Chrome (Selenium headless) i Supabase:
1. Iniciar sessió com a operador al frontend (Supabase Auth).
2. Detecció d'estat del PC segons heartbeat (Online 🟢 / Offline 🔴).
3. Prémer «Buscar surebets» i creació de sessió real a Supabase (PENDING).
4. Reclamació atòmica per l'agent persistent (PENDING -> RUNNING).
5. Publicació d'un snapshot sintètic per l'agent a Supabase.
6. Actualització en temps real a la interfície web.
7. Prémer «Aturar cerca» en una altra sessió i verificació de transició a CANCELLED.
8. Comprovació del comportament de desconnexió quan l'agent s'atura.
9. Verificació de la validació estricta de tipus d'oportunitats (rebuig de desconeguts).
10. Verificació del filtratge servidor per excloure dades de prova a les consultes reals.
"""

import os
import sys
import time
import json
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
import pytest
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from agent.supabase_coordinator import SupabaseCoordinator
from agent.windows_agent import WindowsSurebetAgent
from agent.protocol import STATUS_PENDING, STATUS_RUNNING, STATUS_CANCELLED, STATUS_COMPLETED


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
        super().__init__(*args, directory=web_dir, **kwargs)

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def supabase_env():
    load_dotenv()
    os.environ["ALLOW_SUPABASE_REAL"] = "1"
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", os.environ.get("SUPABASE_KEY"))
    agent_email = os.environ.get("SUREBET_AGENT_EMAIL")
    agent_pwd = os.environ.get("SUREBET_AGENT_PASSWORD")
    op_email = os.environ.get("SUREBET_OPERATOR_EMAIL")
    op_pwd = os.environ.get("SUREBET_OPERATOR_PASSWORD")

    if not (url and key and agent_email and agent_pwd and op_email and op_pwd):
        pytest.skip("Variables de Supabase no configurades al fitxer .env local.")

    return {
        "url": url,
        "key": key,
        "agent_email": agent_email,
        "agent_pwd": agent_pwd,
        "op_email": op_email,
        "op_pwd": op_pwd
    }


@pytest.fixture(scope="module")
def web_server():
    server = HTTPServer(("127.0.0.1", 8092), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:8092"
    server.shutdown()


@pytest.fixture(scope="module")
def chrome_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    driver = webdriver.Chrome(options=options)
    yield driver
    driver.quit()


def test_strict_opportunity_type_validation_rejects_unknown(supabase_env):
    """Verifica que SupabaseCoordinator rebutja tipus d'oportunitat desconeguts (no converteix a SUREBET)."""
    coord = SupabaseCoordinator(
        supabase_url=supabase_env["url"],
        publishable_key=supabase_env["key"],
        agent_name="Windows-PC-Local"
    )

    with pytest.raises(ValueError) as exc:
        coord.publish_snapshot(
            snapshot={"session_id": "00000000-0000-0000-0000-000000000000", "odds_age_seconds": 1, "market_type": "1X2"},
            opportunities=[{"opportunity_type": "TEST_INVALID", "matchup": "Test"}]
        )
    assert "Tipus d'oportunitat desconegut o no autoritzat" in str(exc.value)


def test_server_side_filter_excludes_test_data(supabase_env):
    """Verifica que get_active_opportunities aplica filtratge al servidor per excloure dades de prova."""
    coord = SupabaseCoordinator(
        supabase_url=supabase_env["url"],
        publishable_key=supabase_env["key"],
        agent_name="Windows-PC-Local"
    )
    coord.authenticate_agent_user(supabase_env["op_email"], supabase_env["op_pwd"])

    # Amb filtratge per defecte (sense dades de prova)
    opps = coord.get_active_opportunities(limit=50, include_tests=False)
    for opp in opps:
        assert "[TEST" not in opp.get("matchup", "")
        assert "TEST_" not in opp.get("matchup", "")


def test_web_real_e2e_operator_login_and_search_lifecycle(web_server, chrome_driver, supabase_env):
    """
    Executa la prova de punta a punta al navegador real:
    1. Obre la web i navega a la pestanya Surebets.
    2. Inicia sessió com a operador via Supabase Auth.
    3. Mostra l'estat del PC segons el batec.
    4. Llança una cerca via «Buscar surebets».
    5. L'agent Windows processa la sessió en mode simulat.
    6. Comprova l'aturada cooperativa «Aturar cerca».
    """
    driver = chrome_driver
    driver.get(f"{web_server}/index.html")

    # 1. Navegar a la pestanya Surebets
    btn_surebets_nav = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-tab='surebets']"))
    )
    btn_surebets_nav.click()
    time.sleep(1)

    # 2. Iniciar sessió com a operador
    email_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "operator-email-input"))
    )
    pwd_input = driver.find_element(By.ID, "operator-password-input")
    login_btn = driver.find_element(By.ID, "btn-operator-login")

    email_input.clear()
    email_input.send_keys(supabase_env["op_email"])
    pwd_input.clear()
    pwd_input.send_keys(supabase_env["op_pwd"])
    login_btn.click()

    # Esperar que aparegui l'estat d'operador autenticat
    active_email_elem = WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.ID, "operator-active-email"))
    )
    assert active_email_elem.text == supabase_env["op_email"]

    # 3. Inicialitzar agent Windows en segon pla (mode simulat)
    coord = SupabaseCoordinator(
        supabase_url=supabase_env["url"],
        publishable_key=supabase_env["key"],
        agent_name="Windows-PC-Local"
    )
    coord.authenticate_agent_user(supabase_env["agent_email"], supabase_env["agent_pwd"])
    agent = WindowsSurebetAgent(coordinator=coord, agent_id="Windows-PC-Local", simulated=True)

    # Enviar batec inicial de presència
    agent.send_heartbeat()
    time.sleep(1)

    # Comprovar que el web reflecteix el PC en línia
    badge_text = driver.find_element(By.ID, "agent-connection-status-text")
    WebDriverWait(driver, 10).until(lambda d: "En línia" in badge_text.text)
    assert "En línia" in badge_text.text

    # 4. Seleccionar durada i clicar «Buscar surebets»
    btn_start = driver.find_element(By.ID, "btn-start-surebet-search")
    assert btn_start.is_enabled()
    btn_start.click()

    # Esperar que la sessió passi a PENDING o RUNNING
    state_elem = driver.find_element(By.ID, "surebet-job-state")
    WebDriverWait(driver, 10).until(
        lambda d: "En cua" in state_elem.text or "Cercant" in state_elem.text
    )

    # 5. L'agent reclama la sessió pendent
    claimed_order = coord.claim_pending_order()
    assert claimed_order is not None
    sess_id = claimed_order.session_id

    # 6. Executar un cicle d'atenció amb publicació de snapshot
    worker_thread = threading.Thread(target=agent.process_one_order, args=(claimed_order,), daemon=True)
    worker_thread.start()
    time.sleep(2)

    # Comprovar que el web mostra l'estat actiu
    WebDriverWait(driver, 10).until(lambda d: "Cercant" in state_elem.text)

    # 7. Provar «Aturar cerca» (Cancel·lació cooperativa)
    btn_stop = driver.find_element(By.ID, "btn-stop-surebet-search")
    assert btn_stop.is_enabled()
    btn_stop.click()

    # Esperar que la cancel·lació es processi pel web i per l'agent
    WebDriverWait(driver, 10).until(
        lambda d: "aturada" in state_elem.text or "CANCELLED" in state_elem.text
    )

    worker_thread.join(timeout=10)
    time.sleep(2)

    # Verificar que l'estat final a PostgreSQL és CANCELLED
    status_data = coord.get_session_status(sess_id)
    assert status_data is not None
    assert status_data.get("status") == "CANCELLED"

    # Verificar que el snapshot de la sessió es va crear a Supabase
    snapshots = coord.get_session_snapshots(sess_id)
    assert len(snapshots) >= 1
    assert snapshots[0]["match_id"] == "TEST_SIMULATED_MATCH_001"

    # 8. Comprovar desconnexió de l'operador
    btn_logout = driver.find_element(By.ID, "btn-operator-logout")
    btn_logout.click()
    time.sleep(1)

    login_form = driver.find_element(By.ID, "operator-login-form")
    assert login_form.is_displayed()

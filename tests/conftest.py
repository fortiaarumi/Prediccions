import os
import json
import hashlib
import shutil
import socket
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
FIXTURES_DIR = ROOT / "tests" / "fixtures"

PROTECTED_FILES = [
    DATA_DIR / "laliga.db",
    DATA_DIR / "winamax_cache_tid_31.json",
    DATA_DIR / "bet365_odds_cache.json",
]

def get_protected_hashes():
    hashes = {}
    for p in PROTECTED_FILES:
        if p.exists():
            hashes[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            hashes[str(p.relative_to(ROOT))] = None
    return hashes

# Forçar mode offline estricte per a tota la suite
os.environ["PREDDICIONS_TEST_OFFLINE"] = "1"
os.environ.setdefault("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
os.environ.setdefault("OCI_USER_OCID", "ocid1.user.oc1..test_user")
os.environ.setdefault("OCI_FINGERPRINT", "20:3b:97:13:55:1c:5b:0d:d3:37:d0:02:f4:91:38:13")
os.environ.setdefault("OCI_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----")
os.environ.setdefault("OCI_REGION", "eu-madrid-1")
os.environ.setdefault("OCI_COMPARTMENT_OCID", "ocid1.compartment.oc1..test_compartment")

@pytest.fixture(scope="session", autouse=True)
def isolate_data_and_enforce_offline_reproducibility(tmp_path_factory):
    """
    Session fixture que garanteix:
    1. Aïllament i immutabilitat absoluta dels fitxers reals de dades (laliga.db, winamax_cache, bet365_cache).
    2. Reproduïbilitat 100% offline: cap test depèn ni executa trucades de xarxa a Winamax, Bet365 ni Flashscore.
    3. Les respostes externes necessàries es serveixen directament des de les memòries cau i fixtures aïllades.
    4. Qualsevol connexió de xarxa externa no autoritzada queda interceptada i bloquejada.
    """
    initial_hashes = get_protected_hashes()

    # 1. Crear directori de dades temporal aïllat
    iso_dir = tmp_path_factory.mktemp("isolated_data")
    if DATA_DIR.exists():
        for item in DATA_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, iso_dir / item.name)

    # Copiar fixtures de partit si n'hi ha
    if FIXTURES_DIR.exists():
        for fix_item in FIXTURES_DIR.iterdir():
            if fix_item.is_file():
                shutil.copy2(fix_item, iso_dir / fix_item.name)

    # 2. Redirigir constants de mòduls cap a la còpia aïllada
    import scraper.winamax_scraper as ws
    import scraper.bet365_scraper as bs
    import scraper.live_crawler as lc
    import database.db_manager as dbm

    orig_ws_cache = ws.CACHE_DIR
    orig_bs_cache = bs.CACHE_DIR
    orig_lc_data = lc.DATA_DIR
    orig_db_path = dbm.DEFAULT_DB_PATH

    import engine.cost_guard as ecg
    orig_ecg_state_file = ecg.STATE_FILE
    orig_ecg_lock_file = ecg.DEFAULT_PERSISTENT_LOCK_FILE
    orig_env_lock = os.environ.get("COST_GUARD_PERSISTENCE_FILE")

    ws.CACHE_DIR = iso_dir
    bs.CACHE_DIR = iso_dir
    lc.DATA_DIR = iso_dir
    iso_db = iso_dir / "laliga.db"
    dbm.DEFAULT_DB_PATH = iso_db

    iso_state_file = iso_dir / "cost_guard_state.json"
    iso_lock_file = iso_dir / ".cost_guard_state_persistent_lock.json"
    iso_lock_file.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": "2026-11-03T00:00:00+00:00",
        "authorized_manual_override": False
    }), encoding="utf-8")
    iso_state_file.write_text(json.dumps({
        "status": "OK",
        "service_state": "RUNNING",
        "restart_locked": False,
        "economic_shutdown_triggered": False,
        "lock_reason": None,
        "consecutive_failures": 0,
        "last_cost_check": None,
        "estimated_cost_eur": 0.0,
        "official_cost_eur": 0.0,
        "uptime_hours": 0.0,
        "discovery_failed": False,
        "active_provisioning": None
    }), encoding="utf-8")
    ecg.STATE_FILE = iso_state_file
    ecg.DEFAULT_PERSISTENT_LOCK_FILE = iso_lock_file
    os.environ["COST_GUARD_PERSISTENCE_FILE"] = str(iso_lock_file)

    # Redirigir DatabaseManager.__init__ si s'indica 'data/laliga.db' o None
    orig_dbm_init = dbm.DatabaseManager.__init__
    def patched_dbm_init(self, db_path=None):
        if db_path is None or str(db_path) in (str(DATA_DIR / "laliga.db"), "data/laliga.db", r"data\laliga.db"):
            target_path = iso_db
        else:
            target_path = Path(db_path)
        orig_dbm_init(self, db_path=target_path)

    dbm.DatabaseManager.__init__ = patched_dbm_init

    # 3. Interceptar subprocess.run per a comandes 'curl' externes
    orig_subproc_run = subprocess.run
    def offline_subprocess_run(cmd, *args, **kwargs):
        cmd_list = cmd if isinstance(cmd, list) else str(cmd).split()
        is_curl = any("curl" in str(arg).lower() for arg in cmd_list)

        if is_curl:
            url = next((arg for arg in cmd_list if str(arg).startswith("http")), "")
            # Intercepció Winamax match 71945378
            if "71945378" in url:
                fix_file = FIXTURES_DIR / "winamax_match_71945378.json"
                if fix_file.exists():
                    with open(fix_file, "r", encoding="utf-8") as f:
                        payload = f.read()
                    mock_res = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=0,
                        stdout=f"var PRELOADED_STATE = {payload};",
                        stderr=""
                    )
                    return mock_res

            # Intercepció Winamax tournament cache (ex: Serie A tid 31)
            if "sports/1/31" in url or "tid_31" in url:
                tid_file = iso_dir / "winamax_cache_tid_31.json"
                if tid_file.exists():
                    with open(tid_file, "r", encoding="utf-8") as f:
                        payload = f.read()
                    return subprocess.CompletedProcess(
                        args=cmd,
                        returncode=0,
                        stdout=f"var PRELOADED_STATE = {payload};",
                        stderr=""
                    )

            # Qualsevol altra crida externa via curl en mode de test
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="[OFFLINE_TEST_MODE]: Accés a xarxa externa bloquejat pel harness de proves."
            )

        return orig_subproc_run(cmd, *args, **kwargs)

    subprocess.run = offline_subprocess_run

    # 4. Guardià de sockets: bloqueig estricte de connect i connect_ex cap a l'exterior
    orig_socket_connect = socket.socket.connect
    orig_socket_connect_ex = socket.socket.connect_ex

    def offline_socket_connect(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else str(address)
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_socket_connect(self, address)
        raise RuntimeError(
            f"[BLOCATGE OFFLINE]: S'ha interceptat un intent de connexió externa no permesa a '{address}' durant l'execució de tests."
        )

    def offline_socket_connect_ex(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else str(address)
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_socket_connect_ex(self, address)
        # 10061 (WSAECONNREFUSED / ECONNREFUSED) indica rebuig absolut de connexió offline
        return 10061

    socket.socket.connect = offline_socket_connect
    socket.socket.connect_ex = offline_socket_connect_ex

    # 5. Intercepció de subprocess.Popen per a qualsevol procés extern
    orig_popen = subprocess.Popen
    def offline_popen(cmd, *args, **kwargs):
        cmd_list = cmd if isinstance(cmd, list) else str(cmd).split()
        is_curl = any("curl" in str(arg).lower() for arg in cmd_list)
        if is_curl:
            url = next((arg for arg in cmd_list if str(arg).startswith("http")), "")
            if "71945378" in url:
                fix_file = FIXTURES_DIR / "winamax_match_71945378.json"
                if fix_file.exists():
                    payload = fix_file.read_text(encoding="utf-8")
                    # Crear procés simulat que retorna el payload
                    proc = orig_popen(["python", "-c", f"import sys; sys.stdout.write('var PRELOADED_STATE = ' + open(r'{fix_file}', encoding='utf-8').read() + ';')"], *args, **kwargs)
                    return proc
        return orig_popen(cmd, *args, **kwargs)

    subprocess.Popen = offline_popen

    # 6. Intercepció d'urllib.request.urlopen
    import urllib.request
    import urllib.error
    orig_urlopen = urllib.request.urlopen
    def offline_urlopen(url_or_req, *args, **kwargs):
        url_str = url_or_req.full_url if hasattr(url_or_req, "full_url") else str(url_or_req)
        # Si no és local, llançar error d'inaccessibilitat
        raise urllib.error.URLError(f"[BLOCATGE OFFLINE]: Accés HTTP extern bloquejat a '{url_str}' pel harness de proves.")

    urllib.request.urlopen = offline_urlopen

    try:
        yield {
            "isolated_dir": iso_dir,
            "isolated_db": iso_db,
            "initial_hashes": initial_hashes,
        }
    finally:
        # Restaurar
        ws.CACHE_DIR = orig_ws_cache
        bs.CACHE_DIR = orig_bs_cache
        lc.DATA_DIR = orig_lc_data
        dbm.DEFAULT_DB_PATH = orig_db_path
        dbm.DatabaseManager.__init__ = orig_dbm_init
        subprocess.run = orig_subproc_run
        subprocess.Popen = orig_popen
        socket.socket.connect = orig_socket_connect
        socket.socket.connect_ex = orig_socket_connect_ex
        urllib.request.urlopen = orig_urlopen
        ecg.STATE_FILE = orig_ecg_state_file
        ecg.DEFAULT_PERSISTENT_LOCK_FILE = orig_ecg_lock_file
        if orig_env_lock is not None:
            os.environ["COST_GUARD_PERSISTENCE_FILE"] = orig_env_lock
        else:
            os.environ.pop("COST_GUARD_PERSISTENCE_FILE", None)

        # Verificar immutabilitat
        final_hashes = get_protected_hashes()
        mismatches = []
        for f, h_init in initial_hashes.items():
            h_final = final_hashes.get(f)
            if h_init != h_final:
                mismatches.append(f"{f}: abans={h_init} després={h_final}")

        if mismatches:
            raise RuntimeError(
                "VIOLACIÓ CRÍTICA D'IMMUTABILITAT DE DADES durant l'execució de tests:\n"
                + "\n".join(mismatches)
            )

"""
tests/test_network_airgap.py
============================
Demostració del blindatge offline complet del sistema de proves:
1. Comprova que socket.socket.connect bloqueja qualsevol intent de connexió a internet.
2. Comprova que socket.socket.connect_ex retorna codi de rebuig (10061).
3. Comprova que urllib.request.urlopen bloqueja qualsevol petició HTTP externa.
4. Comprova que subprocess.run intercepta comandes externes de curl i no fa crides reals.
5. Comprova que no s'admeten excepcions per a cap host extern.
"""

import socket
import subprocess
import urllib.request
import urllib.error
import pytest


def test_socket_connect_airgap_blocks_external_hosts():
    """Valida que socket.socket.connect llança RuntimeError si s'intenta connectar a un host extern."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"\[BLOCATGE OFFLINE\]"):
            s.connect(("8.8.8.8", 53))
    finally:
        s.close()


def test_socket_connect_ex_airgap_refuses_connection():
    """Valida que socket.socket.connect_ex retorna codi de rebuig (10061) per a hosts externs."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        err = s.connect_ex(("1.1.1.1", 80))
        assert err == 10061
    finally:
        s.close()


def test_urllib_airgap_blocks_http_requests():
    """Valida que urllib.request.urlopen intercepta i bloqueja peticions HTTP externes."""
    with pytest.raises(urllib.error.URLError, match=r"\[BLOCATGE OFFLINE\]"):
        urllib.request.urlopen("https://api.odds-data-api.com/v1/bet365/upcoming", timeout=2)


def test_subprocess_curl_airgap_blocks_external_urls():
    """Valida que subprocess.run intercepta comandes de curl externes sense tocar la xarxa."""
    cmd = ["curl.exe", "-s", "https://unauthorized-external-site.com/odds"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 1
    assert "OFFLINE_TEST_MODE" in res.stderr


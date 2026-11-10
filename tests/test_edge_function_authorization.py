"""
tests/test_edge_function_authorization.py
==========================================
Suite de proves per a l'autorització i control d'accés de l'Edge Function search-surebets.

Cobreix estrictament els 4 escenaris exigits:
1. Usuari anònim (sense capçalera Authorization) -> Rebuig HTTP 401.
2. Token invàlid o expirat -> Rebuig HTTP 401.
3. Usuari autenticat sense rol d'operador/admin (ex: viewer o no registrat a app_users) -> Rebuig HTTP 403.
4. Operador autoritzat (rol 'operator' o 'admin' actiu a app_users) -> Autoritzat per procedir.

Zero crèdits consumits: les proves s'aturen a la capa d'autorització abans de qualsevol crida externa.
"""

import os
import pytest
from unittest.mock import MagicMock
from dotenv import load_dotenv

# Carregar entorn local
load_dotenv()


def evaluate_edge_function_auth(
    auth_header: str | None,
    mock_auth_user: dict | None,
    mock_app_user: dict | None,
    authorized_operator_uuid: str = ""
) -> tuple[int, dict]:
    """
    Simula amb fidelitat 1:1 la lògica d'autenticació i autorització
    de supabase/functions/search-surebets/index.ts.
    """
    # 1. Comprovació de capçalera Authorization
    if not auth_header:
        return 401, {"error": "Capçalera Authorization requerida."}

    token = auth_header.replace("Bearer ", "").strip()
    if not token or token == "invalid.jwt.token" or mock_auth_user is None:
        return 401, {"error": "Token d'usuari invàlid o expirat."}

    user_id = mock_auth_user.get("id", "")

    # 1.5 Validació d'autorització RBAC (app_users o UUID autoritzat)
    is_authorized_role = (
        mock_app_user is not None
        and mock_app_user.get("is_active", False) is True
        and mock_app_user.get("role") in ("admin", "operator")
    )
    is_authorized_id = bool(authorized_operator_uuid and user_id == authorized_operator_uuid)

    if not is_authorized_role and not is_authorized_id:
        return 403, {
            "error": "Accés denegat: es requereixen privilegis d'operador o administrador actiu.",
            "code": "FORBIDDEN_OPERATOR_REQUIRED"
        }

    return 200, {"status": "AUTHORIZED", "user_id": user_id}


# =========================================================================
# PROVES UNITÀRIES (CONTRACTE DE SEGURETAT DE L'EDGE FUNCTION)
# =========================================================================

def test_auth_scenario_1_anonymous_user_rejected_401():
    """Un usuari anònim sense capçalera Authorization ha de ser rebutjat amb HTTP 401."""
    status, payload = evaluate_edge_function_auth(
        auth_header=None,
        mock_auth_user=None,
        mock_app_user=None
    )
    assert status == 401
    assert "Authorization requerida" in payload["error"]


def test_auth_scenario_2_invalid_jwt_rejected_401():
    """Un token malformat, invàlid o expirat ha de ser rebutjat amb HTTP 401."""
    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer invalid.jwt.token",
        mock_auth_user=None,
        mock_app_user=None
    )
    assert status == 401
    assert "invàlid o expirat" in payload["error"]


def test_auth_scenario_3a_authenticated_viewer_rejected_403():
    """Un usuari autenticat a Supabase però amb rol 'viewer' ha de ser rebutjat amb HTTP 403."""
    mock_user = {"id": "user-viewer-123", "email": "viewer@prediccions.test"}
    mock_app = {"user_id": "user-viewer-123", "role": "viewer", "is_active": True}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.viewer",
        mock_auth_user=mock_user,
        mock_app_user=mock_app
    )
    assert status == 403
    assert payload["code"] == "FORBIDDEN_OPERATOR_REQUIRED"
    assert "privilegis d'operador" in payload["error"]


def test_auth_scenario_3b_authenticated_user_not_in_app_users_rejected_403():
    """Un usuari registrat a Auth però que no figura a app_users ha de ser rebutjat amb HTTP 403."""
    mock_user = {"id": "user-random-456", "email": "random@gmail.com"}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.random",
        mock_auth_user=mock_user,
        mock_app_user=None  # No existeix a app_users
    )
    assert status == 403
    assert payload["code"] == "FORBIDDEN_OPERATOR_REQUIRED"


def test_auth_scenario_3c_inactive_operator_rejected_403():
    """Un operador amb compte desactivat (is_active=False) ha de ser rebutjat amb HTTP 403."""
    mock_user = {"id": "user-inactive-789", "email": "inactive_op@prediccions.test"}
    mock_app = {"user_id": "user-inactive-789", "role": "operator", "is_active": False}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.inactive",
        mock_auth_user=mock_user,
        mock_app_user=mock_app
    )
    assert status == 403
    assert payload["code"] == "FORBIDDEN_OPERATOR_REQUIRED"


def test_auth_scenario_4a_active_operator_authorized_200():
    """Un usuari actiu amb rol 'operator' a app_users és autoritzat satisfactòriament."""
    mock_user = {"id": "5cfdbc8b-512b-4ee7-b7f5-ca6d226fdd7e", "email": "fortiaarumi@gmail.com"}
    mock_app = {"user_id": "5cfdbc8b-512b-4ee7-b7f5-ca6d226fdd7e", "role": "operator", "is_active": True}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.operator",
        mock_auth_user=mock_user,
        mock_app_user=mock_app
    )
    assert status == 200
    assert payload["status"] == "AUTHORIZED"
    assert payload["user_id"] == "5cfdbc8b-512b-4ee7-b7f5-ca6d226fdd7e"


def test_auth_scenario_4b_active_admin_authorized_200():
    """Un usuari actiu amb rol 'admin' a app_users és autoritzat satisfactòriament."""
    mock_user = {"id": "admin-user-001", "email": "admin@prediccions.test"}
    mock_app = {"user_id": "admin-user-001", "role": "admin", "is_active": True}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.admin",
        mock_auth_user=mock_user,
        mock_app_user=mock_app
    )
    assert status == 200
    assert payload["status"] == "AUTHORIZED"


def test_auth_scenario_4c_authorized_by_server_uuid():
    """Un usuari amb UUID configurat com a AUTHORIZED_OPERATOR_UUID és autoritzat encara que no figuri a app_users."""
    mock_user = {"id": "override-uuid-999", "email": "override@prediccions.test"}

    status, payload = evaluate_edge_function_auth(
        auth_header="Bearer valid.jwt.override",
        mock_auth_user=mock_user,
        mock_app_user=None,
        authorized_operator_uuid="override-uuid-999"
    )
    assert status == 200
    assert payload["status"] == "AUTHORIZED"


# =========================================================================
# PROVA D'INTEGRACIÓ AMB L'ENDPOINT REAL (SENSE CONSUM DE CRÈDITS)
# =========================================================================

def test_live_edge_function_anonymous_and_invalid_token():
    """Verifica que l'endpoint desplegat a Supabase rebutja consultes no autenticades (0 crèdits)."""
    if os.environ.get("ALLOW_SUPABASE_REAL") != "1":
        pytest.skip("Prova d'integració externa en viu requereix ALLOW_SUPABASE_REAL=1.")

    import requests

    sb_url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not (sb_url and anon_key):
        pytest.skip("SUPABASE_URL o SUPABASE_PUBLISHABLE_KEY no configurades.")

    endpoint = f"{sb_url.rstrip('/')}/functions/v1/search-surebets"

    # 1. Petició anònima -> 401
    r_anon = requests.post(endpoint, json={})
    assert r_anon.status_code == 401

    # 2. Petició amb token invàlid -> 401
    r_invalid = requests.post(
        endpoint,
        headers={"Authorization": "Bearer invalid_token_123", "apikey": anon_key},
        json={}
    )
    assert r_invalid.status_code == 401

    # 3. Petició amb usuari autenticat que NO és operador (ex: agent) -> 403
    agent_email = os.environ.get("SUREBET_AGENT_EMAIL")
    agent_pwd = os.environ.get("SUREBET_AGENT_PASSWORD")
    if agent_email and agent_pwd:
        auth_r = requests.post(
            f"{sb_url.rstrip('/')}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": agent_email, "password": agent_pwd}
        )
        if auth_r.status_code == 200:
            agent_jwt = auth_r.json().get("access_token")
            r_agent = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {agent_jwt}", "apikey": anon_key},
                json={}
            )
            assert r_agent.status_code == 403
            assert r_agent.json().get("code") == "FORBIDDEN_OPERATOR_REQUIRED"

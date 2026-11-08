"""
tests/test_migration_sql_idempotence.py
=======================================
Verifica l'auditoria estàtica i la garantia d'idempotència completa de la
migració canònica `migrations/001_surebet_monitor_schema.sql`.

Comprova:
1. Tots els CREATE TABLE incorporen IF NOT EXISTS.
2. Tots els CREATE [UNIQUE] INDEX incorporen IF NOT EXISTS.
3. Totes les funcions usen CREATE OR REPLACE FUNCTION.
4. Tots els triggers disposen d'un DROP TRIGGER IF EXISTS previ.
5. Totes les vistes usen CREATE OR REPLACE VIEW.
6. Totes les polítiques RLS (CREATE POLICY) van precedides d'un DROP POLICY IF EXISTS.
7. Totes les clàusules RLS usen ENABLE i NO FORCE.
8. Les instruccions GRANT i REVOKE són idempotents per disseny a PostgreSQL.
"""

import os
import re
import pytest

MIGRATION_PATH = os.path.join(os.path.dirname(__file__), "..", "migrations", "001_surebet_monitor_schema.sql")


def load_sql_content():
    with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_table_creation_idempotency():
    sql = load_sql_content()
    # Trobar tots els CREATE TABLE
    create_tables = re.findall(r"CREATE\s+TABLE\s+([^(\n]+)", sql, re.IGNORECASE)
    assert len(create_tables) > 0
    for ct in create_tables:
        assert "IF NOT EXISTS" in ct.upper(), f"CREATE TABLE sense IF NOT EXISTS: {ct}"


def test_index_creation_idempotency():
    sql = load_sql_content()
    # Trobar tots els CREATE INDEX i CREATE UNIQUE INDEX
    create_indexes = re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+([^\n]+)", sql, re.IGNORECASE)
    assert len(create_indexes) > 0
    for ci in create_indexes:
        assert "IF NOT EXISTS" in ci.upper(), f"CREATE INDEX sense IF NOT EXISTS: {ci}"


def test_function_creation_idempotency():
    sql = load_sql_content()
    # Trobar totes les definicions de funcions
    create_functions = re.findall(r"(CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+[^\n(]+)", sql, re.IGNORECASE)
    assert len(create_functions) > 0
    for cf in create_functions:
        assert "CREATE OR REPLACE FUNCTION" in cf.upper(), f"Funció sense CREATE OR REPLACE: {cf}"


def test_trigger_idempotency():
    sql = load_sql_content()
    # Trobar tots els CREATE TRIGGER: CREATE TRIGGER <nom> BEFORE/AFTER ... ON <taula>
    triggers = re.findall(r"CREATE\s+TRIGGER\s+([a-zA-Z0-9_]+)\s+(?:BEFORE|AFTER)[^\n]+ON\s+([a-zA-Z0-9_]+)", sql, re.IGNORECASE)
    assert len(triggers) > 0
    for trg_name, tbl_name in triggers:
        # Ha d'existir un DROP TRIGGER IF EXISTS trg_name ON tbl_name
        pattern = rf"DROP\s+TRIGGER\s+IF\s+EXISTS\s+{trg_name}\s+ON\s+{tbl_name}"
        assert re.search(pattern, sql, re.IGNORECASE) is not None, f"Trigger {trg_name} sense DROP IF EXISTS previ"


def test_view_creation_idempotency():
    sql = load_sql_content()
    views = re.findall(r"(CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[^\n(]+)", sql, re.IGNORECASE)
    assert len(views) > 0
    for v in views:
        assert "CREATE OR REPLACE VIEW" in v.upper(), f"Vista sense CREATE OR REPLACE: {v}"


def test_policy_creation_idempotency():
    sql = load_sql_content()
    # Trobar totes les polítiques: CREATE POLICY "nom" ON taula
    policies = re.findall(r'CREATE\s+POLICY\s+"([^"]+)"\s+ON\s+([a-zA-Z0-9_]+)', sql, re.IGNORECASE)
    assert len(policies) > 0
    for pol_name, tbl_name in policies:
        # Ha d'existir un DROP POLICY IF EXISTS "nom" ON taula
        pattern = rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"{re.escape(pol_name)}"\s+ON\s+{tbl_name}'
        assert re.search(pattern, sql, re.IGNORECASE) is not None, f'Política "{pol_name}" sense DROP previ'


def test_rls_configuration_security():
    sql = load_sql_content()
    # Verificar que totes les taules tenen ENABLE ROW LEVEL SECURITY
    tables = ["app_users", "daemon_agents", "surebet_sessions", "surebet_snapshots", "surebet_opportunities"]
    for t in tables:
        assert re.search(rf"ALTER\s+TABLE\s+{t}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE) is not None, f"Taula {t} sense ENABLE RLS"
        assert re.search(rf"ALTER\s+TABLE\s+{t}\s+NO\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", sql, re.IGNORECASE) is not None, f"Taula {t} hauria de tenir NO FORCE RLS"


def test_no_privileged_secrets_in_migration():
    sql = load_sql_content()
    # Eliminar comentaris SQL (-- i /* */) per avaluar exclusivament instruccions SQL actives
    code_only = re.sub(r"--[^\n]*", "", sql)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.DOTALL)

    # Les instruccions actives NO han de concedir ni utilitzar service_role ni sb_secret_
    assert "service_role" not in code_only.lower()
    assert "sb_secret_" not in code_only.lower()


def test_atomic_single_active_session_constraint():
    sql = load_sql_content()
    # Ha d'existir l'índex únic parcial a nivell de motor per impedir concurrència
    pattern = r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+uq_surebet_single_active_session\s+ON\s+surebet_sessions\s+\(\(id\s+IS\s+NOT\s+NULL\)\)\s+WHERE\s+status\s+IN\s+\('PENDING',\s*'RUNNING',\s*'CANCEL_REQUESTED'\)"
    assert re.search(pattern, sql, re.IGNORECASE) is not None, "Falta uq_surebet_single_active_session"


def test_trigger_function_revoked_from_data_api():
    sql = load_sql_content()
    # La funció trigger interna trg_fn_validate_session_integrity ha d'estar explícitament revocada
    pattern = r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+trg_fn_validate_session_integrity\(\)\s+FROM\s+PUBLIC,\s*anon,\s*authenticated"
    assert re.search(pattern, sql, re.IGNORECASE) is not None, "trg_fn_validate_session_integrity no està revocada per a Data API"


def test_idle_agent_heartbeat_rpc_present():
    sql = load_sql_content()
    # La funció register_agent_heartbeat ha d'existir i concedir EXECUTE només a authenticated
    assert "CREATE OR REPLACE FUNCTION register_agent_heartbeat()" in sql
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+register_agent_heartbeat\(\)\s+TO\s+authenticated", sql, re.IGNORECASE) is not None
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+register_agent_heartbeat\(\)\s+FROM\s+PUBLIC,\s*anon", sql, re.IGNORECASE) is not None


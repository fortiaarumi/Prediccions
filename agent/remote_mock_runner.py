"""
agent/remote_mock_runner.py
===========================
Execució de la prova remota simulada Windows ↔ Supabase (sense scraping).

Flux verificat:
1. Agent autenticat via JWT propi (Supabase Auth).
2. Heartbeat de presència periòdic per mantenir l'agent actiu.
3. Operador autenticat via JWT propi creant sessió de cerca via RPC.
4. Concorrència atòmica: rebuig de dues sessions actives simultànies (23505).
5. Reclamació atòmica de la sessió pendent per l'agent (PENDING -> RUNNING).
6. Seguretat RPC: rebuig de crides de publicació no autoritzades (42501).
7. Emissió de heartbeat de sessió activa per l'agent.
8. Publicació de snapshot sintètic clarament marcat TEST amb UUID únic.
9. Idempotència comprovada: reintent de publicació del mateix snapshot sense duplicats.
10. Finalització de sessió (RUNNING -> COMPLETED) ÚNICAMENT si la publicació té èxit.
    En cas de fallada en el snapshot, marca la sessió com a FAILED via fail_session().
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Dict, Any, Optional

from agent.supabase_coordinator import (
    SupabaseCoordinator,
    SupabaseCoordinatorError,
    SupabaseAuthenticationError
)
from agent.protocol import STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED


def _print_summary(results: Dict[str, Any], operator_email: Optional[str]) -> None:
    print("\n" + "=" * 70)
    print("      RESUM DE LA PROVA REMOTA SIMULADA")
    print("=" * 70)
    print(f"Sessió de prova:          {results.get('session_id')}")
    print(f"Estat final:              {results.get('final_status')}")
    print(f"Autenticació Agent:       {'PASS' if results.get('agent_auth') else 'FAIL'}")
    print(f"Heartbeat inicial:        {'PASS' if results.get('agent_heartbeat') else 'FAIL'}")
    if operator_email:
        print(f"Autenticació Operador:    {'PASS' if results.get('operator_auth') else 'FAIL'}")
        print(f"Creació de Sessió:        {'PASS' if results.get('session_created') else 'FAIL'}")
        print(f"Bloqueig Concurrència:    {'PASS' if results.get('concurrency_blocked') else 'FAIL'}")
        print(f"Bloqueig crida no autor.: {'PASS' if results.get('unauthorized_blocked') else 'FAIL'}")
    print(f"Reclamació de Sessió:     {'PASS' if results.get('session_claimed') else 'FAIL'}")
    print(f"Publicació Snapshot TEST: {'PASS' if results.get('snapshot_published') else 'FAIL'}")
    print(f"Idempotència Reintent:    {'PASS' if results.get('snapshot_idempotent') else 'FAIL'}")
    print(f"Completació Sessió:       {'PASS' if results.get('session_completed') else 'FAIL'}")
    print(f"Resultat Global:          {'PASS (EXIT CODE 0)' if results.get('success') else 'FAIL (EXIT CODE 1)'}")
    print("=" * 70 + "\n")


def run_remote_mock_flow(
    supabase_url: str,
    publishable_key: str,
    agent_email: str,
    agent_password: str,
    operator_email: Optional[str] = None,
    operator_password: Optional[str] = None,
    agent_name: str = "Windows-PC-Local",
    duration_seconds: int = 120
) -> Dict[str, Any]:
    """
    Executa el cicle complet de coordinació remota amb dades sintètiques.
    Retorna un resum detallat de les comprovacions i identificadors generats.
    """
    print("\n" + "=" * 70)
    print("      SUREBET MONITOR — PROVA REMOTA SIMULADA (WINDOWS ↔ SUPABASE)")
    print("=" * 70)
    print(f"Target URL:    {supabase_url}")
    print(f"Agent Name:    {agent_name}")
    print(f"Agent Email:   {agent_email}")
    if operator_email:
        print(f"Operador Email: {operator_email}")
    print("Mode:          SIMULAT (Zero peticions a bookmakers / Zero apostes)")
    print("-" * 70)

    results: Dict[str, Any] = {
        "success": False,
        "agent_auth": False,
        "agent_heartbeat": False,
        "operator_auth": False,
        "session_created": False,
        "session_id": None,
        "concurrency_blocked": False,
        "session_claimed": False,
        "unauthorized_blocked": False,
        "session_heartbeat": False,
        "snapshot_published": False,
        "snapshot_id": None,
        "snapshot_idempotent": False,
        "session_completed": False,
        "final_status": None,
        "errors": []
    }

    # -----------------------------------------------------------------
    # Pas 1: Autenticació de l'Agent i Heartbeat Inicial
    # -----------------------------------------------------------------
    print("\n[Pas 1] Inicialitzant agent i autenticant amb JWT propi...")
    try:
        agent_coord = SupabaseCoordinator(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            agent_name=agent_name
        )
        agent_token = agent_coord.authenticate_agent_user(agent_email, agent_password)
        results["agent_auth"] = True
        print(f"  [OK] Agent autenticat correctament a Supabase Auth (JWT rebut).")
    except Exception as e:
        err = f"Error autenticant agent ({agent_email}): {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)
        _print_summary(results, operator_email)
        return results

    print("\n[Pas 2] Enviant batec de presència inicial de l'agent (RPC register_agent_heartbeat)...")
    try:
        hb_res = agent_coord.send_heartbeat()
        if hb_res.alive:
            results["agent_heartbeat"] = True
            print("  [OK] RPC register_agent_heartbeat executada amb èxit (agent actiu i fresc).")
        else:
            raise SupabaseCoordinatorError("register_agent_heartbeat ha retornat false.")
    except Exception as e:
        err = f"Error enviant batec inicial: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)
        _print_summary(results, operator_email)
        return results

    # -----------------------------------------------------------------
    # Pas 3: Autenticació de l'Operador i Creació de Sessió
    # -----------------------------------------------------------------
    session_id: Optional[str] = None
    operator_coord: Optional[SupabaseCoordinator] = None

    if operator_email and operator_password:
        print(f"\n[Pas 3] Autenticant operador '{operator_email}' amb JWT propi...")
        try:
            operator_coord = SupabaseCoordinator(
                supabase_url=supabase_url,
                publishable_key=publishable_key,
                agent_name="OperatorClient"
            )
            operator_token = operator_coord.authenticate_agent_user(operator_email, operator_password)
            results["operator_auth"] = True
            print(f"  [OK] Operador autenticat correctament (JWT d'operador diferenciat).")
        except Exception as e:
            err = f"Error autenticant operador: {e}"
            print(f"  [ERROR] {err}")
            results["errors"].append(err)
            _print_summary(results, operator_email)
            return results

        print(f"\n[Pas 4] Operador crea la sessió de cerca via RPC create_search_session({duration_seconds}s)...")
        try:
            session_id = operator_coord.create_search_session(duration_seconds=duration_seconds)
            results["session_created"] = True
            results["session_id"] = session_id
            print(f"  [OK] Sessió creada amb èxit. Session ID: {session_id}")

            # Verificar estat PENDING via REST RLS
            st_data = operator_coord.get_session_status(session_id)
            if st_data and st_data.get("status") == STATUS_PENDING:
                print(f"  [OK] Estat verificat a Supabase: {st_data.get('status')}")
            else:
                print(f"  [!] Estat inesperat: {st_data}")
        except Exception as e:
            err = f"Error creant sessió: {e}"
            print(f"  [ERROR] {err}")
            results["errors"].append(err)
            _print_summary(results, operator_email)
            return results

        # -----------------------------------------------------------------
        # Pas 5: Verificació de Concurrència Atòmica (23505)
        # -----------------------------------------------------------------
        print("\n[Pas 5] Provant rebuig de sessió concurrent (RPC create_search_session duplicada)...")
        try:
            operator_coord.create_search_session(duration_seconds=duration_seconds)
            err = "La segona sessió concurrent ha estat permesa indegudament!"
            print(f"  [FAIL] {err}")
            results["errors"].append(err)
        except SupabaseCoordinatorError as e:
            if "23505" in str(e) or "Ja existeix una sessió" in str(e):
                results["concurrency_blocked"] = True
                print("  [OK] Concurrència bloquejada correctament (SQLSTATE 23505 detectat).")
            else:
                print(f"  [?] Error inesperat de concurrència: {e}")

    # -----------------------------------------------------------------
    # Pas 6: Reclamació Atòmica per l'Agent (claim_next_pending_session)
    # -----------------------------------------------------------------
    print("\n[Pas 6] Agent reclama la tasca pendent (RPC claim_next_pending_session)...")
    claimed_order = None
    try:
        claimed_order = agent_coord.claim_pending_order()
        if claimed_order:
            session_id = claimed_order.session_id
            results["session_claimed"] = True
            results["session_id"] = session_id
            print(f"  [OK] Sessió reclamada per l'agent: {session_id}")
            print(f"  [OK] Estat transitat a: {claimed_order.status} (Agent assignat: {claimed_order.assigned_agent_id})")
        else:
            raise SupabaseCoordinatorError("No s'ha trobat cap sessió pendent per reclamar.")
    except Exception as e:
        err = f"Error reclamant sessió pendent: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)
        _print_summary(results, operator_email)
        return results

    # -----------------------------------------------------------------
    # Pas 7: Verificació d'Autorització RPC (Rebuig d'usuaris no agent)
    # -----------------------------------------------------------------
    if operator_coord and session_id:
        print("\n[Pas 7] Verificant que l'operador NO pot publicar snapshots (RPC publish_session_snapshot)...")
        try:
            operator_coord.call_rpc("publish_session_snapshot", {
                "p_session_id": session_id,
                "p_odds_age_seconds": 1,
                "p_market_type": "1X2",
                "p_match_id": "TEST_UNAUTH",
                "p_payload": {},
                "p_opportunities": []
            })
            err = "L'operador ha pogut publicar un snapshot a una sessió que no té assignada!"
            print(f"  [FAIL] {err}")
            results["errors"].append(err)
        except SupabaseCoordinatorError as e:
            if "42501" in str(e) or "Accés denegat" in str(e) or "aliena" in str(e):
                results["unauthorized_blocked"] = True
                print("  [OK] Publicació indeguda denegada correctament (SQLSTATE 42501).")
            else:
                print(f"  [?] Error diferent a 42501: {e}")

    # -----------------------------------------------------------------
    # Pas 8: Heartbeat de Sessió Activa
    # -----------------------------------------------------------------
    print("\n[Pas 8] Agent envia batec de sessió activa (RPC send_session_heartbeat)...")
    try:
        shb = agent_coord.send_heartbeat(session_id=session_id)
        if shb.alive:
            results["session_heartbeat"] = True
            print("  [OK] Batec de sessió enregistrat correctament (alive=True).")
        else:
            print("  [!] Batec de sessió ha retornat alive=False.")
    except Exception as e:
        err = f"Error enviant batec de sessió: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)

    # -----------------------------------------------------------------
    # Pas 9: Publicació de Snapshot Sintètic Marcada com a TEST
    # -----------------------------------------------------------------
    test_snapshot_uuid = str(uuid.uuid4())
    print(f"\n[Pas 9] Publicant snapshot sintètic TEST amb UUID determinista {test_snapshot_uuid}...")
    try:
        simulated_snapshot_payload = {
            "session_id": session_id,
            "odds_age_seconds": 3,
            "market_type": "1X2",
            "match_id": "TEST_SIMULATED_MATCH_001",
            "payload": {
                "is_synthetic_test": True,
                "simulation_label": "PROVA_REMOTA_MOCK_WINDOWS_SUPABASE",
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "cli_remote_mock_runner"
            }
        }
        # Inserció amb opportunity_type = 'SUREBET' per respectar la constraint SQL
        # Manté identificació inequívoca de test a matchup, match_id i payload
        simulated_opportunities = [{
            "opportunity_type": "SUREBET",
            "matchup": "[TEST SINTÈTIC] Reial Madrid vs FC Barcelona",
            "market_key": "1X2",
            "net_roi_pct": 3.14,
            "gross_margin_pct": 3.50,
            "commission_deducted_pct": 0.36,
            "bookmakers": [
                {"bookmaker": "TEST_BOOKMAKER_A", "selection": "1", "odd": 3.10},
                {"bookmaker": "TEST_BOOKMAKER_B", "selection": "X", "odd": 3.40},
                {"bookmaker": "TEST_BOOKMAKER_C", "selection": "2", "odd": 2.45}
            ]
        }]

        snap_id = agent_coord.publish_snapshot(
            snapshot=simulated_snapshot_payload,
            opportunities=simulated_opportunities,
            snapshot_uuid=test_snapshot_uuid
        )
        if not snap_id:
            raise SupabaseCoordinatorError("La crida a publish_session_snapshot no ha retornat ID de snapshot")

        results["snapshot_published"] = True
        results["snapshot_id"] = snap_id or test_snapshot_uuid
        print(f"  [OK] Snapshot sintètic publicat satisfactòriament: {results['snapshot_id']}")

    except Exception as e:
        err = f"Error publicant snapshot: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)

        # Gestió estricta de fallada: no continuar amb idempotència ni completar com a èxit
        print(f"\n[!] Marcatge de la sessió com a FAILED per fallada en la publicació inicial...")
        try:
            agent_coord.fail_order(session_id, error_message=f"Fallada publicant snapshot sintètic: {e}")
            st_fail = agent_coord.get_session_status(session_id)
            if st_fail:
                results["final_status"] = st_fail.get("status")
            else:
                results["final_status"] = STATUS_FAILED
            print(f"  [OK] Sessió marcada com a {results['final_status']} a Supabase.")
        except Exception as fe:
            print(f"  [ERROR] No s'ha pogut marcar la sessió com a FAILED: {fe}")
            results["final_status"] = STATUS_FAILED

        results["success"] = False
        _print_summary(results, operator_email)
        return results

    # -----------------------------------------------------------------
    # Pas 10: Idempotència de Reintent
    # -----------------------------------------------------------------
    print("\n[Pas 10] Verificant idempotència: reintentant publicar el mateix snapshot UUID...")
    try:
        re_snap_id = agent_coord.publish_snapshot(
            snapshot=simulated_snapshot_payload,
            opportunities=simulated_opportunities,
            snapshot_uuid=test_snapshot_uuid
        )
        if re_snap_id == results["snapshot_id"]:
            results["snapshot_idempotent"] = True
            print("  [OK] Reintent idempotent acceptat sense errors ni duplicar files.")
        else:
            raise SupabaseCoordinatorError(f"Retorn inesperat a la idempotència: {re_snap_id} != {results['snapshot_id']}")
    except Exception as e:
        err = f"Error en la prova d'idempotència: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)

        print(f"\n[!] Marcatge de la sessió com a FAILED per fallada en la prova d'idempotència...")
        try:
            agent_coord.fail_order(session_id, error_message=f"Fallada prova idempotència: {e}")
            st_fail = agent_coord.get_session_status(session_id)
            results["final_status"] = st_fail.get("status") if st_fail else STATUS_FAILED
        except Exception:
            results["final_status"] = STATUS_FAILED

        results["success"] = False
        _print_summary(results, operator_email)
        return results

    # -----------------------------------------------------------------
    # Pas 11: Finalització de Sessió (complete_session)
    # ÚNICAMENT si els passos 9 i 10 han tingut èxit!
    # -----------------------------------------------------------------
    print(f"\n[Pas 11] Finalitzant la sessió a Supabase (RPC complete_session)...")
    try:
        summary_payload = {
            "test_run": True,
            "snapshots_generated": 1,
            "status": "SUCCESS",
            "execution_mode": "mock_simulated",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        complete_ok = agent_coord.complete_order(session_id, summary=summary_payload)
        if complete_ok:
            results["session_completed"] = True
            print("  [OK] RPC complete_session ha retornat true.")
        else:
            err = "RPC complete_session ha retornat false."
            print(f"  [ERROR] {err}")
            results["errors"].append(err)

        # Verificar estat final via REST
        st_final = agent_coord.get_session_status(session_id)
        if st_final:
            results["final_status"] = st_final.get("status")
            print(f"  [OK] Estat final verificat a PostgreSQL: {results['final_status']}")
    except Exception as e:
        err = f"Error completant sessió: {e}"
        print(f"  [ERROR] {err}")
        results["errors"].append(err)

    all_passed = (
        results["agent_auth"]
        and results["agent_heartbeat"]
        and results["session_claimed"]
        and results["snapshot_published"]
        and results["snapshot_idempotent"]
        and results["session_completed"]
        and results["final_status"] == STATUS_COMPLETED
        and len(results["errors"]) == 0
    )
    if operator_email:
        all_passed = (
            all_passed
            and results["operator_auth"]
            and results["session_created"]
            and results["concurrency_blocked"]
            and results["unauthorized_blocked"]
        )

    results["success"] = all_passed

    _print_summary(results, operator_email)
    return results

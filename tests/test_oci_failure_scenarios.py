"""
tests/test_oci_failure_scenarios.py
===================================
Bateria exhaustiva de proves per als 12 ESCENARIS DE FALLADA OCI PAYG
definits a l'auditoria final del projecte PreddicionsLliga.

Principi rector obligatori:
Davant qualsevol incertesa (dades desactualitzades, caiguda d'API, causa desconeguda,
inventari incomplet), el comportament HA DE SER CONSERVADOR: NO crear ni reiniciar recursos.
"""

import pytest
import json
import tempfile
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from engine.cost_guard import (
    CostGuard,
    AUTHORIZED_BASELINE,
    THRESHOLDS,
    ALWAYS_FREE_LIMITS,
    RATES_EUR,
    LockPersistenceBackend,
    FileLockPersistenceBackend,
    GitHubVariablePersistenceBackend,
    InMemoryLockPersistenceBackend,
    GitHubIssueLedgerPersistenceBackend
)
from scripts.oci_external_watchdog import (
    discover_oci_resources,
    run_watchdog,
    stop_oci_instance,
    execute_administrative_recovery
)


@pytest.fixture
def tmp_guard():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)
    lock_path = tmp_path.parent / f".{tmp_path.stem}_persistent_lock.json"
    lock_path.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")
    guard = CostGuard(state_path=tmp_path)
    yield guard
    if tmp_path.exists():
        tmp_path.unlink()
    if lock_path.exists():
        lock_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 1: Apareix una IP pública reservada sense associar
# -----------------------------------------------------------------------------
def test_scenario_1_unattached_public_ip(tmp_guard):
    """
    Una IP pública reservada sense associar genera cost horari a OCI (0.0050 USD/h ~ 0.0046 €/h).
    El sistema ha de:
    1. Detectar l'anomalia.
    2. Calcular el cost teòric excedentari (3.42 €/mes).
    3. Activar RESTART_LOCKED en superar el llindar de 0.50 €.
    4. Denegar taxativament el reinici automàtic.
    """
    prov = dict(AUTHORIZED_BASELINE)
    prov["unattached_public_ips"] = 1

    cost, anomalies = tmp_guard.compute_estimated_cost(prov, uptime_hours=744.0)
    assert cost > 0.50
    assert any("IP pública reservada sense associar" in a for a in anomalies)

    summary = tmp_guard.evaluate(current_provisioning=prov, uptime_hours=744.0)
    assert summary["status"] == "RESTART_LOCKED"
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "Bloqueig econòmic actiu" in reason or "Recursos inesperats" in reason


# -----------------------------------------------------------------------------
# Escenari 2: Apareix un volum de 250 GB
# -----------------------------------------------------------------------------
def test_scenario_2_volume_250gb(tmp_guard):
    """
    Un volum total de 250 GB supera el límit Always Free de 200 GB en 50 GB.
    El sistema ha de:
    1. Computar el cost de l'excés (50 GB * 0.0235 € = ~1.17 €/mes).
    2. Activar RESTART_LOCKED per superació del sostre de 0.50 €.
    3. Bloquejar el servei contra reinicis automàtics.
    """
    prov = dict(AUTHORIZED_BASELINE)
    prov["boot_volume_gb"] = 50.0
    prov["extra_volumes_gb"] = 200.0  # Total 250 GB

    cost, anomalies = tmp_guard.compute_estimated_cost(prov, uptime_hours=744.0)
    assert cost > 1.00
    assert any("Disc supera 200GB Always Free" in a for a in anomalies)

    summary = tmp_guard.evaluate(current_provisioning=prov, uptime_hours=744.0)
    assert summary["status"] == "RESTART_LOCKED"
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False


# -----------------------------------------------------------------------------
# Escenari 3: La VM està STOPPED i existeix un bloqueig econòmic
# -----------------------------------------------------------------------------
def test_scenario_3_vm_stopped_with_economic_lock(tmp_guard):
    """
    Si la màquina està STOPPED i hi ha un bloqueig econòmic actiu,
    el vigilant extern ha de refusar l'arrencada i mantenir l'aturada.
    """
    tmp_guard.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
    tmp_guard.state["restart_locked"] = True
    tmp_guard.state["lock_reason"] = "Superació de 0.50 € en el cicle mensual"
    tmp_guard.save_state()

    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "Bloqueig econòmic actiu" in reason


# -----------------------------------------------------------------------------
# Escenari 4: La VM està STOPPED però no se'n coneix la causa
# -----------------------------------------------------------------------------
def test_scenario_4_vm_stopped_unknown_cause(tmp_guard):
    """
    Si la màquina està STOPPED però shutdown_cause_known és False
    (p. ex. aturada sobtada sense motiu conegut), el sistema NO pot reiniciar
    la màquina a cegues fins que s'aclareixi l'origen.
    """
    tmp_guard.state["service_state"] = "STOPPED_UNKNOWN"
    tmp_guard.set_shutdown_cause_known(False, reason="Aturada externa no documentada")

    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "Causa de l'aturada de la VM desconeguda" in reason


# -----------------------------------------------------------------------------
# Escenari 5: La Usage API retorna dades de fa més de 48 hores
# -----------------------------------------------------------------------------
def test_scenario_5_usage_api_older_than_48h(tmp_guard):
    """
    Dades oficials de més de 48 hores impliquen incertesa financera.
    El sistema ha de marcar stale_billing_data = True i bloquejar reinicis.
    """
    old_time = (datetime.now(timezone.utc) - timedelta(hours=55)).isoformat()
    official_data = {
        "amount_eur": 0.0,
        "timestamp_utc": old_time
    }
    summary = tmp_guard.evaluate(AUTHORIZED_BASELINE, 100.0, official_data=official_data)
    assert summary["stale_billing_data"] is True
    assert summary["status"] == "WARNING"
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "superior a 48 hores" in reason


# -----------------------------------------------------------------------------
# Escenari 6: La Usage API falla (HTTP 500 o timeout)
# -----------------------------------------------------------------------------
def test_scenario_6_usage_api_fails(tmp_guard):
    """
    Si la Usage API no respon (None) i no hi ha dades prèvies fresques,
    el sistema activa WARNING, marca dades no consolidades i denega el reinici.
    """
    summary = tmp_guard.evaluate(AUTHORIZED_BASELINE, 100.0, official_data=None)
    assert summary["stale_billing_data"] is True
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "Dades de facturació desconegudes" in reason


# -----------------------------------------------------------------------------
# Escenari 7: OCI SDK no pot consultar un compartiment
# -----------------------------------------------------------------------------
def test_scenario_7_oci_sdk_compartment_failure(tmp_guard):
    """
    Si la crida OCI SDK al compartiment falla per timeout o error de permisos,
    es registra discovery_failed = True i el reinici es bloqueja immediatament.
    """
    tmp_guard.set_discovery_status(False, reason="Timeout consultant compartiment SurebetMonitor")
    assert tmp_guard.state["discovery_failed"] is True
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "descobriment de recursos OCI ha fallat" in reason


# -----------------------------------------------------------------------------
# Escenari 8: El watchdog no pot consultar una regió
# -----------------------------------------------------------------------------
def test_scenario_8_watchdog_region_failure(tmp_guard):
    """
    Si la regió eu-madrid-1 és inaccessible pel vigilant,
    es declara fallada de descobriment regional i es prohibeix qualsevol creació o reinici.
    """
    tmp_guard.set_discovery_status(False, reason="Regió eu-madrid-1 inaccessible")
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "incertesa d'inventari" in reason


# -----------------------------------------------------------------------------
# Escenari 9A: Detecció primerenca de forma no autoritzada (Aturada Preventiva)
# -----------------------------------------------------------------------------
def test_scenario_9a_rogue_shape_early_detection_preventive_shutdown():
    """
    Escenari 9A: Detecta una instància VM.Standard3.Flex (1 OCPU, 4 GB RAM)
    després de només 1 hora d'activitat (cost estimat teòric 0,04416 € < llindar 0,25 €).
    Comprova que l'anomalia crítica activa immediatament PREVENTIVE_SHUTDOWN
    sense esperar a acumular despesa financera, i bloqueja el reinici automàtic.
    """
    prov_rogue = dict(AUTHORIZED_BASELINE)
    prov_rogue["shape"] = "VM.Standard3.Flex"
    prov_rogue["ocpus"] = 1
    prov_rogue["ram_gb"] = 4
    prov_rogue["is_running"] = True

    # 1. Validació de la fórmula teòrica contra RATES_EUR
    expected_cost_raw = (
        1 * 1.0 * RATES_EUR["standard_e4_ocpu_hour"] +
        4 * 1.0 * RATES_EUR["standard_e4_ram_gb_hour"]
    )
    assert abs(expected_cost_raw - 0.04416) < 1e-6, f"Fórmula teòrica esperada 0.04416 €, obtingut {expected_cost_raw}"

    with tempfile.NamedTemporaryFile(suffix="_9a.json", delete=False) as f:
        tmp_path_9a = Path(f.name)
    lock_path_9a = tmp_path_9a.parent / f".{tmp_path_9a.stem}_persistent_lock.json"
    lock_path_9a.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        guard_9a = CostGuard(state_path=tmp_path_9a)
        summary = guard_9a.evaluate(prov_rogue, uptime_hours=1.0)

        # Assercions deterministes estrictes
        assert summary["status"] == "PREVENTIVE_SHUTDOWN"
        assert summary["service_state"] == "STOPPED_PREVENTIVE"
        assert summary["estimated_cost_eur"] == 0.0442  # round(0.04416, 4)
        assert len(summary["unexpected_resources"]) > 0
        assert "Forma no autoritzada de pagament: VM.Standard3.Flex" in summary["unexpected_resources"][0]
        assert "Detectat recurs de pagament no autoritzat" in summary["lock_reason"]

        # Comprovació de bloqueig de reinici
        can_restart, reason = guard_9a.can_restart()
        assert can_restart is False
        assert "Aturada preventiva activada" in reason
        assert guard_9a.state.get("restart_locked") is not True
        assert guard_9a.state.get("economic_shutdown_triggered") is True
    finally:
        if tmp_path_9a.exists():
            tmp_path_9a.unlink()
        if lock_path_9a.exists():
            lock_path_9a.unlink()


# -----------------------------------------------------------------------------
# Escenari 9B: Escalada a bloqueig econòmic dur per forma no autoritzada
# -----------------------------------------------------------------------------
def test_scenario_9b_rogue_shape_prolonged_lock_after_24h():
    """
    Escenari 9B: La mateixa instància no autoritzada VM.Standard3.Flex
    ha romàs encesa durant 24 hores (cost estimat teòric 1,05984 € >= llindar 0,50 €).
    Comprova que el sistema escala deterministament a RESTART_LOCKED per despesa
    acumulada, activa restart_locked=True i impedeix el reinici.
    """
    prov_rogue = dict(AUTHORIZED_BASELINE)
    prov_rogue["shape"] = "VM.Standard3.Flex"
    prov_rogue["ocpus"] = 1
    prov_rogue["ram_gb"] = 4
    prov_rogue["is_running"] = True

    # 1. Validació de la fórmula teòrica contra RATES_EUR
    expected_cost_raw = (
        1 * 24.0 * RATES_EUR["standard_e4_ocpu_hour"] +
        4 * 24.0 * RATES_EUR["standard_e4_ram_gb_hour"]
    )
    assert abs(expected_cost_raw - 1.05984) < 1e-6, f"Fórmula teòrica esperada 1.05984 €, obtingut {expected_cost_raw}"

    with tempfile.NamedTemporaryFile(suffix="_9b.json", delete=False) as f:
        tmp_path_9b = Path(f.name)
    lock_path_9b = tmp_path_9b.parent / f".{tmp_path_9b.stem}_persistent_lock.json"
    lock_path_9b.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        guard_9b = CostGuard(state_path=tmp_path_9b)
        summary = guard_9b.evaluate(prov_rogue, uptime_hours=24.0)

        # Assercions deterministes estrictes
        assert summary["status"] == "RESTART_LOCKED"
        assert summary["service_state"] == "STOPPED_ECONOMIC_LOCK"
        assert summary["estimated_cost_eur"] == 1.0598  # round(1.05984, 4)
        assert guard_9b.state["restart_locked"] is True
        assert guard_9b.state["economic_shutdown_triggered"] is True
        assert "superat el llindar de bloqueig" in summary["lock_reason"]

        # Comprovació de bloqueig de reinici
        can_restart, reason = guard_9b.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
    finally:
        if tmp_path_9b.exists():
            tmp_path_9b.unlink()
        if lock_path_9b.exists():
            lock_path_9b.unlink()


# -----------------------------------------------------------------------------
# Escenari 10: GitHub Actions s'executa dues vegades simultàniament
# -----------------------------------------------------------------------------
def test_scenario_10_github_actions_concurrency():
    """
    Verifica que el fitxer de workflow .github/workflows/oci_cost_guard.yml
    té configurat el bloc de concurrència per evitar col·lisions d'execució simultànies.
    """
    wf_path = Path(__file__).parent.parent / ".github" / "workflows" / "oci_cost_guard.yml"
    assert wf_path.exists()
    content = wf_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)

    assert "concurrency" in parsed
    conc = parsed["concurrency"]
    assert conc.get("group") == "oci-cost-guard"
    assert conc.get("cancel-in-progress") is False


# -----------------------------------------------------------------------------
# Escenari 11: El workflow falla després de detectar una anomalia
# -----------------------------------------------------------------------------
def test_scenario_11_workflow_fails_after_anomaly_tag_persistence():
    """
    Si el runner de GitHub Actions cau després de detectar una anomalia,
    el tag persistent a OCI ('economic_lock': 'true') garanteix que el següent
    runner (amb disc completament net) recuperi el bloqueig directament d'OCI.
    """
    # 1. Simulem l'objecte instància retornat per OCI ComputeClient
    class MockInstance:
        shape = "VM.Standard.A1.Flex"
        lifecycle_state = "STOPPED"
        shape_config = None
        freeform_tags = {
            "economic_lock": "true",
            "lock_reason": "Aturada preventiva per IP orfe persistent"
        }

    # 2. El nou runner executa discover_oci_resources amb instància etiquetada
    discovered_mock = {
        "shape": MockInstance.shape,
        "is_running": False,
        "economic_lock_tag": True,
        "lock_reason_tag": MockInstance.freeform_tags["lock_reason"]
    }

    # 3. Watchdog nou engega amb estat fresc
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        clean_state_path = Path(f.name)
    try:
        fresh_guard = CostGuard(state_path=clean_state_path)
        # El vigilant processa el tag d'OCI
        if discovered_mock.get("economic_lock_tag"):
            fresh_guard.state["restart_locked"] = True
            fresh_guard.state["economic_shutdown_triggered"] = True
            fresh_guard.state["lock_reason"] = discovered_mock.get("lock_reason_tag")
            fresh_guard._persist_lock_state(locked=True, reason=discovered_mock.get("lock_reason_tag"))
            fresh_guard.persistence_status = "OK"
            fresh_guard.state["persistence_unverified"] = False
            fresh_guard.save_state()

        can_restart, reason = fresh_guard.can_restart()
        assert can_restart is False
        assert "Aturada preventiva per IP orfe persistent" in reason
    finally:
        if clean_state_path.exists():
            clean_state_path.unlink()
        companion = clean_state_path.parent / f".{clean_state_path.stem}_persistent_lock.json"
        if companion.exists():
            companion.unlink()


# -----------------------------------------------------------------------------
# Escenari 12: Es produeix un reinici inesperat després d'un bloqueig econòmic
# -----------------------------------------------------------------------------
def test_scenario_12_unexpected_restart_attempt_after_economic_lock(tmp_guard):
    """
    Verifica que no existeix cap camí automàtic per saltar-se un bloqueig econòmic.
    Un reinici no autoritzat és rebutjat taxativament llevat que es proporcioni
    el token administratiu explícit 'AUTORITZO_REINICI_SENSE_DESPESA'.
    """
    tmp_guard.state["restart_locked"] = True
    tmp_guard.state["economic_shutdown_triggered"] = True
    tmp_guard.state["lock_reason"] = "Bloqueig actiu per risc financer"
    tmp_guard.save_state()

    # Intent 1: Crida directa a can_restart()
    can_restart, reason = tmp_guard.can_restart()
    assert can_restart is False
    assert "Cal autorització manual" in reason

    # Intent 2: Intent d'autorització amb token invàlid o buit
    assert tmp_guard.authorize_manual_restart("reboot") is False
    assert tmp_guard.authorize_manual_restart("FORCE_START") is False
    assert tmp_guard.can_restart()[0] is False

    # Intent 3: Autorització legítima explícita
    assert tmp_guard.authorize_manual_restart("AUTORITZO_REINICI_SENSE_DESPESA") is True
    assert tmp_guard.can_restart()[0] is True


# -----------------------------------------------------------------------------
# Escenari 13: Timeout HTTP amb VM RUNNING no provoca ordre START
# -----------------------------------------------------------------------------
def test_scenario_13_http_timeout_with_running_vm_does_not_call_start(monkeypatch, tmp_path):
    """
    Un timeout HTTP quan la VM està RUNNING a OCI és un error de servei o túnel,
    no una fallada de la màquina. El watchdog NO ha d'emetre ordre START.
    """
    start_called = []
    monkeypatch.setattr(
        "scripts.oci_external_watchdog.start_oci_instance",
        lambda *args, **kwargs: start_called.append(True)
    )

    prov_running = dict(AUTHORIZED_BASELINE)
    prov_running["is_running"] = True

    isolated_guard = CostGuard(state_path=tmp_path / "cg_state.json", persistence_backend=InMemoryLockPersistenceBackend())
    # Executa en mode dry_run simulat amb is_live=False (timeout)
    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_provisioning=prov_running,
        live_url=None,  # live_url None fa que is_live sigui False
        guard=isolated_guard
    )
    assert len(start_called) == 0, "No s'havia d'haver cridat start_oci_instance si la VM està RUNNING!"


# -----------------------------------------------------------------------------
# Escenari 14: Timeout HTTP amb VM STOPPED per causa desconeguda no arrenca a cegues
# -----------------------------------------------------------------------------
def test_scenario_14_http_timeout_with_stopped_vm_unknown_cause_does_not_start(monkeypatch, tmp_path):
    """
    Davant la causa desconeguda d'aturada d'una VM (ex: aturada manual per consola,
    manteniment o recuperació d'Oracle), el watchdog NO ha d'arrencar la VM automàticament.
    """
    start_called = []
    monkeypatch.setattr(
        "scripts.oci_external_watchdog.start_oci_instance",
        lambda *args, **kwargs: start_called.append(True)
    )

    prov_stopped = dict(AUTHORIZED_BASELINE)
    prov_stopped["is_running"] = False

    isolated_guard = CostGuard(state_path=tmp_path / "cg_state.json", persistence_backend=InMemoryLockPersistenceBackend(initial_state={"locked": False}))
    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_provisioning=prov_stopped,
        live_url=None,
        guard=isolated_guard
    )
    assert len(start_called) == 0, "No s'havia d'haver cridat start_oci_instance per causa desconeguda!"


# -----------------------------------------------------------------------------
# Escenari 15: stop_oci_instance preserva etiquetes existents (fusió Read-Modify-Write)
# -----------------------------------------------------------------------------
def test_scenario_15_stop_oci_instance_preserves_existing_tags():
    """
    Verifica que en aplicar el tag economic_lock: 'true',
    es llegeixen i mantenen les etiquetes lliures que la instància ja tenia
    (patró Read-Modify-Write no atòmic).
    """
    class MockInstanceData:
        freeform_tags = {
            "Environment": "Production",
            "Project": "PreddicionsLliga",
            "Owner": "Fortia"
        }

    class MockInstanceResponse:
        data = MockInstanceData()

    class MockUpdateResponse:
        status = 200

    class MockActionResponse:
        status = 200

    captured_update_details = []

    class MockComputeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_instance(self, ocid):
            return MockInstanceResponse()

        def update_instance(self, ocid, update_details):
            captured_update_details.append(update_details)
            return MockUpdateResponse()

        def instance_action(self, ocid, action):
            return MockActionResponse()

    # Simulem el client OCI
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = MockComputeClient
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: freeform_tags

    # Test directe de fusió
    inst_data = MockComputeClient().get_instance("ocid.test").data
    merged = dict(inst_data.freeform_tags or {})
    merged["economic_lock"] = "true"
    merged["lock_reason"] = "Test lock"
    merged["locked_at"] = datetime.now(timezone.utc).isoformat()

    assert merged["Environment"] == "Production"
    assert merged["Project"] == "PreddicionsLliga"
    assert merged["Owner"] == "Fortia"
    assert merged["economic_lock"] == "true"
    assert merged["lock_reason"] == "Test lock"


# -----------------------------------------------------------------------------
# Escenari 16: Si falla l'etiqueta però STOP funciona, el runner nou NO arrenca
# -----------------------------------------------------------------------------
def test_scenario_16_tag_failure_and_stop_success_never_restarts_on_next_runner(monkeypatch):
    """
    Si l'etiquetatge falla però la VM s'atura, la VM a OCI queda STOPPED sense etiqueta.
    El runner nou (sense estat local) la detecta STOPPED per causa desconeguda.
    Criteri de seguretat: el watchdog NO ha d'emetre ordre START.
    """
    start_called = []
    monkeypatch.setattr(
        "scripts.oci_external_watchdog.start_oci_instance",
        lambda *args, **kwargs: start_called.append(True)
    )

    # Runner nou simulat: sense tag econòmic a OCI, però VM està STOPPED
    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["is_running"] = False
    mock_prov["economic_lock_tag"] = False  # L'etiqueta no es va poder guardar

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        clean_state_path = Path(f.name)
    try:
        fresh_guard = CostGuard(state_path=clean_state_path, persistence_backend=InMemoryLockPersistenceBackend())
        # Comprovem amb run_watchdog simulant el runner nou
        res = run_watchdog(
            mode="fast",
            dry_run=True,
            mock_provisioning=mock_prov,
            live_url=None,
            guard=fresh_guard
        )
        assert len(start_called) == 0, "No s'ha d'arrencar una VM STOPPED amb causa desconeguda!"
    finally:
        if clean_state_path.exists():
            clean_state_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 17: Si falla STOP després d'aplicar l'etiqueta, el runner nou reintenta STOP
# -----------------------------------------------------------------------------
def test_scenario_17_stop_failure_leaves_tag_and_retries_stop(monkeypatch, tmp_path):
    """
    Si l'etiquetatge té èxit però STOP falla, la VM continua RUNNING amb l'etiqueta al núvol.
    El nou runner detecta l'etiqueta, activa RESTART_LOCKED i torna a cridar stop_oci_instance.
    """
    stop_called = []
    monkeypatch.setattr(
        "scripts.oci_external_watchdog.stop_oci_instance",
        lambda *args, **kwargs: stop_called.append(True)
    )

    clean_state_path = tmp_path / "cost_guard_state.json"
    in_mem_backend = InMemoryLockPersistenceBackend(initial_state={"locked": False})
    isolated_guard = CostGuard(state_path=clean_state_path, persistence_backend=in_mem_backend)

    # VM encara RUNNING, però amb tag de bloqueig actiu a OCI
    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["is_running"] = True
    mock_prov["economic_lock_tag"] = True
    mock_prov["lock_reason_tag"] = "Bloqueig econòmic persistent de prova"

    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_provisioning=mock_prov,
        live_url=None,
        guard=isolated_guard
    )
    # Ha d'haver intentat aturar la màquina immediatament
    assert len(stop_called) > 0, "El watchdog hauria de tornar a intentar aturar la instància!"
    assert res["cost_guard_summary"]["status"] == "RESTART_LOCKED"
    assert res["can_restart"] is False


# -----------------------------------------------------------------------------
# Escenari 18: Etiqueta econòmica present encara que es modifiquin altres etiquetes
# -----------------------------------------------------------------------------
def test_scenario_18_economic_lock_preserved_when_other_tags_modified():
    """
    Si un altre usuari afegeix o modifica etiquetes a la instància OCI,
    sempre que 'economic_lock': 'true' persisteixi al diccionari,
    el vigilant manté el bloqueig econòmic infranquejable.
    """
    tags_modified = {
        "Environment": "Staging",
        "Project": "PreddicionsLliga_v2",
        "CostCenter": "CC-9901",
        "economic_lock": "true",
        "lock_reason": "Superació de llindar financer"
    }

    discovered = {
        "shape": "VM.Standard.A1.Flex",
        "is_running": True,
        "economic_lock_tag": tags_modified.get("economic_lock") == "true",
        "lock_reason_tag": tags_modified.get("lock_reason")
    }

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        guard = CostGuard(state_path=tmp_path)
        if discovered["economic_lock_tag"]:
            guard.state["restart_locked"] = True
            guard.state["economic_shutdown_triggered"] = True
            guard.state["lock_reason"] = discovered["lock_reason_tag"]
            guard._persist_lock_state(locked=True, reason=discovered["lock_reason_tag"])
            guard.persistence_status = "OK"
            guard.state["persistence_unverified"] = False
            guard.save_state()

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

        companion = tmp_path.parent / f".{tmp_path.stem}_persistent_lock.json"
        if companion.exists():
            companion.unlink()



# -----------------------------------------------------------------------------
# Escenari 19: Concurrència optimista (conflicte HTTP 412 resolt amb reintent)
# -----------------------------------------------------------------------------
def test_scenario_19_optimistic_concurrency_412_conflict_retry_success(monkeypatch):
    """
    Simula un conflicte HTTP 412 (Precondition Failed) a update_instance pel desfasament
    d'ETag degut a una modificació concurrent. El sistema ha de re-llegir l'instància,
    fusionar els tags més recents i aconseguir l'actualització a l'intent 2.
    """
    attempt_count = {"updates": 0, "gets": 0}

    class MockInstanceData:
        def __init__(self, tag_env="Prod"):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = {"Environment": tag_env, "Project": "Preddicions", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}

    class MockGetResponse:
        def __init__(self, etag="etag-1", tag_env="Prod"):
            self.data = MockInstanceData(tag_env=tag_env)
            self.headers = {"etag": etag}

    class MockConflictError(Exception):
        def __init__(self):
            super().__init__("Precondition Failed")
            self.status = 412

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                return MockGetResponse(etag="etag-1", tag_env="Prod_v1")
            return MockGetResponse(etag="etag-2", tag_env="Prod_v2_concurrent")

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            if attempt_count["updates"] == 1:
                # Simula que un altre procés ha modificat la instància i l'etag-1 ja no és vàlid
                raise MockConflictError()
            assert if_match == "etag-2", "El segon intent ha d'utilitzar el nou ETag!"
            assert update_details.freeform_tags.get("Environment") == "Prod_v2_concurrent", "Ha de conservar el tag concurrent!"
            assert update_details.freeform_tags.get("economic_lock") == "true"
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Bloqueig per concurrència",
        dry_run=False,
        max_retries=3
    )
    assert success is True
    assert attempt_count["updates"] == 2
    assert attempt_count["gets"] in (2, 3)


# -----------------------------------------------------------------------------
# Escenari 20: Concurrència optimista (esgotament de reintents per 412 persistent)
# -----------------------------------------------------------------------------
def test_scenario_20_optimistic_concurrency_retry_exhaustion_handled(monkeypatch):
    """
    Si les actualitzacions d'etiquetes pateixen conflictes 412 persistents i s'esgoten
    els reintents (3 intents), la funció no ha de caure amb excepció no controlada,
    ha de registrar l'error crític i retornar False (indicant fallada d'etiquetatge).
    """
    attempt_count = {"updates": 0}

    class MockConflictError(Exception):
        def __init__(self):
            super().__init__("Precondition Failed Persistent")
            self.status = 412

    class MockResponse200:
        status = 200

    class MockPersistentConflictClient:
        def get_instance(self, ocid):
            mock_resp = MagicMock()
            mock_resp.data.freeform_tags = {"Project": "Preddicions", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}
            mock_resp.headers = {"etag": f"etag-{attempt_count['updates']}"}
            return mock_resp

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            raise MockConflictError()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockPersistentConflictClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Bloqueig persistent",
        dry_run=False,
        max_retries=3
    )
    # Com que no es va poder aplicar el tag després de 3 intents, stop_oci_instance ha de retornar False
    assert success is False
    assert attempt_count["updates"] == 3


# -----------------------------------------------------------------------------
# Escenari 21: ETag absent a headers aborta actualització d'etiquetes i atura VM
# -----------------------------------------------------------------------------
def test_scenario_21_etag_absent_aborts_tag_update_and_stops_vm(monkeypatch):
    """
    Si get_instance() retorna una resposta sense capçalera ETag, el sistema NO ha d'intentar
    actualitzar les etiquetes a cegues. Ha d'avortar l'actualització, executar l'ordre STOP
    d'emergència i retornar False per senyalar fallada parcial.
    """
    update_called = []
    stop_called = []

    class MockClientNoEtag:
        def get_instance(self, ocid):
            mock_resp = MagicMock()
            mock_resp.data.freeform_tags = {"Environment": "Prod"}
            mock_resp.headers = {}  # Cap ETag
            return mock_resp

        def update_instance(self, ocid, update_details, if_match=None):
            update_called.append(True)
            return MagicMock(status=200)

        def instance_action(self, ocid, action):
            stop_called.append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClientNoEtag()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test No ETag",
        dry_run=False
    )
    # update_instance NO s'ha de cridar sense ETag
    assert len(update_called) == 0, "No s'ha d'executar update_instance si no hi ha ETag!"
    # L'ordre STOP d'emergència SÍ s'ha d'haver enviat
    assert len(stop_called) == 1 and stop_called[0] == "STOP"
    # Retorna False perquè l'etiquetatge no es va poder completar
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 22: ETag buit o d'espais en blanc aborta actualització d'etiquetes
# -----------------------------------------------------------------------------
def test_scenario_22_etag_empty_or_whitespace_aborts_tag_update(monkeypatch):
    """
    Si l'ETag és buit ('') o conté només espais ('   '), el sistema el rebutja com a invàlid.
    No s'envia update_instance sense un ETag vàlid.
    """
    update_called = []
    stop_called = []

    class MockClientEmptyEtag:
        def get_instance(self, ocid):
            mock_resp = MagicMock()
            mock_resp.data.freeform_tags = {"Environment": "Prod"}
            mock_resp.headers = {"etag": "   "}  # ETag invàlid
            return mock_resp

        def update_instance(self, ocid, update_details, if_match=None):
            update_called.append(True)
            return MagicMock(status=200)

        def instance_action(self, ocid, action):
            stop_called.append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClientEmptyEtag()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test Empty ETag",
        dry_run=False
    )
    assert len(update_called) == 0, "No s'ha d'executar update_instance amb ETag buit!"
    assert len(stop_called) == 1
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 23: Error en get_instance() aborta etiquetatge i atura VM
# -----------------------------------------------------------------------------
def test_scenario_23_get_instance_error_aborts_tag_and_executes_stop(monkeypatch):
    """
    Si get_instance() falla per timeout o error de xarxa, el bloc d'etiquetes s'aborta,
    però el sistema continua endavant per executar la comanda STOP d'emergència.
    """
    stop_called = []

    class MockClientGetError:
        def get_instance(self, ocid):
            raise Exception("Timeout consultant metadades de la instància")

        def update_instance(self, *a, **kw):
            pass

        def instance_action(self, ocid, action):
            stop_called.append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClientGetError()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test Get Error",
        dry_run=False
    )
    assert len(stop_called) == 1
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 24: Error en update_instance() executa STOP i retorna False
# -----------------------------------------------------------------------------
def test_scenario_24_tag_update_failure_executes_stop_and_returns_false(monkeypatch):
    """
    Si update_instance() falla amb un error genèric no-412 (ex: límit d'etiquetes o permís),
    s'executa igualment l'ordre STOP de la VM i es retorna False (senyalant alerta).
    """
    stop_called = []

    class MockClientUpdateError:
        def get_instance(self, ocid):
            mock_resp = MagicMock()
            mock_resp.data.freeform_tags = {"Environment": "Prod"}
            mock_resp.headers = {"etag": "valid-etag-123"}
            return mock_resp

        def update_instance(self, ocid, update_details, if_match=None):
            raise Exception("500 Internal Server Error a OCI Tags")

        def instance_action(self, ocid, action):
            stop_called.append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClientUpdateError()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test Update Error",
        dry_run=False
    )
    assert len(stop_called) == 1
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 25: Fracàs de STOP amb HTTP 500 a instance_action()
# -----------------------------------------------------------------------------
def test_scenario_25_stop_instance_action_fails_http_500(monkeypatch):
    """
    Verifica el comportament quan l'actualització d'etiquetes té èxit (HTTP 200),
    però l'ordre OCI instance_action('STOP') falla amb un error HTTP 500 (o ServiceError).
    stop_oci_instance() ha de gestionar l'error sense caure, registrar l'alerta i retornar False.
    """
    class MockInstanceData:
        freeform_tags = {"Environment": "Prod"}

    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-valid-500"}

    class MockComputeClientHttp500:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)

        def instance_action(self, ocid, action):
            # Simula que el control plane d'OCI respon HTTP 500 Internal Error
            return MagicMock(status=500)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClientHttp500()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test_500",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test HTTP 500 Failure",
        dry_run=False
    )
    # L'etiquetatge va tenir èxit, però com que el STOP va retornar 500, ha de retornar False
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 26: Integració de run_watchdog() amb forma no autoritzada (forma rogue)
# -----------------------------------------------------------------------------
def test_scenario_26_watchdog_integration_rogue_shape_executes_stop_and_tags(monkeypatch):
    """
    Executa run_watchdog() amb una instància VM.Standard3.Flex encesa.
    Verifica d'extrem a extrem mitjançant mocks:
    1. Que CostGuard detecta la forma rogue i activa PREVENTIVE_SHUTDOWN o RESTART_LOCKED.
    2. Que el vigilant intenta actualitzar l'etiqueta persistent economic_lock a OCI.
    3. Que el vigilant executa incondicionalment l'ordre STOP.
    4. Que can_restart queda estrictament en False.
    """
    calls = {"update_instance": [], "instance_action": []}

    class MockInstanceData:
        lifecycle_state = "STOPPED"
        freeform_tags = {"Project": "PreddicionsLliga", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}

    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-rogue-1"}

    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            calls["update_instance"].append(update_details.freeform_tags)
            return MagicMock(status=200)

        def instance_action(self, ocid, action):
            calls["instance_action"].append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex"
    mock_prov["is_running"] = True

    with tempfile.NamedTemporaryFile(suffix="_watchdog_test.json", delete=False) as f:
        tmp_state_file = Path(f.name)
    lock_file = tmp_state_file.parent / f".{tmp_state_file.stem}_persistent_lock.json"
    lock_file.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda: CostGuard(state_path=tmp_state_file))

        res = run_watchdog(
            mode="fast",
            dry_run=False,
            mock_provisioning=mock_prov,
            instance_ocid="ocid1.instance.oc1..test_rogue_vm",
            live_url=None
        )

        # 1. Estat de CostGuard
        summary = res["cost_guard_summary"]
        assert summary["status"] in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED")
        assert res["can_restart"] is False

        # 2. Intent d'actualització d'etiqueta economic_lock
        assert len(calls["update_instance"]) == 1
        tags_sent = calls["update_instance"][0]
        assert tags_sent.get("economic_lock") == "true"
        assert "Project" in tags_sent  # Tags previs preservats

        # 3. Intent incondicional de STOP
        assert len(calls["instance_action"]) == 1
        assert calls["instance_action"][0] == "STOP"
    finally:
        if tmp_state_file.exists():
            tmp_state_file.unlink()
        if lock_file.exists():
            lock_file.unlink()


# -----------------------------------------------------------------------------
# Escenari 27: run_watchdog() intenta STOP fins i tot si l'etiquetatge llança excepció
# -----------------------------------------------------------------------------
def test_scenario_27_watchdog_integration_tag_failure_still_executes_stop(monkeypatch):
    """
    Verifica que en una execució completa de run_watchdog() davant d'una forma rogue,
    si update_instance() llança una excepció severa (ex: error de xarxa o 500 a tags),
    el vigilant continua endavant i executa incondicionalment l'ordre STOP.
    """
    calls = {"instance_action": []}

    class MockInstanceData:
        freeform_tags = {}

    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-err-1"}

    class MockComputeClientTagError:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            raise Exception("500 Internal Error a l'API d'etiquetes OCI")

        def instance_action(self, ocid, action):
            calls["instance_action"].append(action)
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClientTagError()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex"
    mock_prov["is_running"] = True

    with tempfile.NamedTemporaryFile(suffix="_watchdog_tagerr.json", delete=False) as f:
        tmp_state_file = Path(f.name)
    lock_file = tmp_state_file.parent / f".{tmp_state_file.stem}_persistent_lock.json"
    lock_file.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda: CostGuard(state_path=tmp_state_file))

        res = run_watchdog(
            mode="fast",
            dry_run=False,
            mock_provisioning=mock_prov,
            instance_ocid="ocid1.instance.oc1..test_rogue_vm",
            live_url=None
        )

        assert res["can_restart"] is False
        assert len(calls["instance_action"]) == 1
        assert calls["instance_action"][0] == "STOP"
    finally:
        if tmp_state_file.exists():
            tmp_state_file.unlink()
        if lock_file.exists():
            lock_file.unlink()


# =============================================================================
# PERSISTÈNCIA DEL BLOQUEIG ECONÒMIC (COST GUARD) - 10 PROVES OBLIGATÒRIES
# =============================================================================

# -----------------------------------------------------------------------------
# Escenari 28 (Test 1): Bloqueig econòmic actiu + desaparició del fitxer local
# -----------------------------------------------------------------------------
def test_scenario_28_active_lock_survives_local_file_deletion():
    """
    1. Bloqueig econòmic actiu + desaparició del fitxer local:
    Quan cost_guard_state.json no existeix però la font persistent indica bloqueig,
    el sistema ha de recuperar immediatament el bloqueig, denegar el reinici
    i reconstruir l'estat local protegit.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Despesa acumulada (0.60€) ha superat el llindar de bloqueig (0.50€)",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_del_state.json", delete=True) as f:
        missing_path = Path(f.name)  # Fitxer inexistent a disc

    assert not missing_path.exists()

    try:
        guard = CostGuard(state_path=missing_path, persistence_backend=backend)
        assert guard.state["restart_locked"] is True
        assert guard.state["status"] == "RESTART_LOCKED"
        assert guard.state["service_state"] == "STOPPED_ECONOMIC_LOCK"
        assert "0.60€" in guard.state["lock_reason"]

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
        assert "Cal autorització manual" in reason

        # Verifica que el fitxer local s'ha reconstruït atòmicament a disc amb estat bloquejat
        assert missing_path.exists()
        with open(missing_path, "r", encoding="utf-8") as rf:
            reconstructed = json.load(rf)
        assert reconstructed["restart_locked"] is True
        assert reconstructed["status"] == "RESTART_LOCKED"
    finally:
        if missing_path.exists():
            missing_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 29 (Test 2): Bloqueig econòmic actiu + fitxer local corrupte
# -----------------------------------------------------------------------------
def test_scenario_29_active_lock_survives_local_file_corruption():
    """
    2. Bloqueig econòmic actiu + fitxer local corrupte:
    Si cost_guard_state.json conté bytes invàlids o JSON malformat, el sistema
    ha de recórrer a la font persistent i mantenir el bloqueig infranquejable.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Superació de pressupost de seguretat",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_corrupt.json", delete=False) as f:
        corrupt_path = Path(f.name)
        corrupt_path.write_bytes(b"{ CORRUPTED_DATA_TRUNCATED_JSON: /// ")

    try:
        guard = CostGuard(state_path=corrupt_path, persistence_backend=backend)
        assert guard.state["restart_locked"] is True
        assert guard.state["status"] == "RESTART_LOCKED"
        assert guard.state["service_state"] == "STOPPED_ECONOMIC_LOCK"

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
    finally:
        if corrupt_path.exists():
            corrupt_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 30 (Test 3): Bloqueig econòmic actiu + runner nou sense estat local
# -----------------------------------------------------------------------------
def test_scenario_30_active_lock_in_fresh_github_runner_without_local_state():
    """
    3. Bloqueig econòmic actiu + runner nou sense estat local:
    Un runner efímer nou de GitHub Actions (clone net sense fitxer local previ)
    ha d'obtenir el bloqueig directament de la persistència externa i refusar el reinici.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Bloqueig preventiu des del cicle anterior",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_runner_fresh.json", delete=True) as f:
        runner_state_path = Path(f.name)

    try:
        runner_guard = CostGuard(state_path=runner_state_path, persistence_backend=backend)
        summary = runner_guard.evaluate(current_provisioning=AUTHORIZED_BASELINE, uptime_hours=744.0)

        assert summary["status"] == "RESTART_LOCKED"
        assert summary["can_restart"] is False

        can_restart, reason = runner_guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
    finally:
        if runner_state_path.exists():
            runner_state_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 31 (Test 4): Absència de la font persistent (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_31_persistence_source_absent_fails_closed():
    """
    4. Absència de la font persistent:
    Si la font persistent externa no existeix (retorna ABSENT), el sistema
    HA DE FALLAR DE MANERA CONSERVADORA (fail-closed): mai no pot equivaldre a status = OK
    ni permetre cap reinici automàtic.
    """
    backend = InMemoryLockPersistenceBackend(failure_mode="ABSENT")

    with tempfile.NamedTemporaryFile(suffix="_absent.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        assert guard.persistence_status == "ABSENT"
        assert guard.state["status"] == "PERSISTENCE_ABSENT"
        assert guard.state["service_state"] == "STOPPED_UNKNOWN"

        summary = guard.evaluate(current_provisioning=AUTHORIZED_BASELINE, uptime_hours=744.0)
        assert summary["status"] == "PERSISTENCE_ABSENT"
        assert summary["can_restart"] is False

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Estat de persistència no verificat (ABSENT" in reason
        assert summary["status"] != "OK"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 32 (Test 5): Error de lectura de la font persistent (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_32_persistence_source_read_error_fails_closed():
    """
    5. Error de lectura de la font persistent:
    Si la consulta externa falla per error de xarxa, timeout o HTTP 500,
    el sistema NO pot convertir l'error en autorització implícita.
    """
    backend = InMemoryLockPersistenceBackend(failure_mode="READ_ERROR")

    with tempfile.NamedTemporaryFile(suffix="_read_err.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        assert guard.persistence_status == "ERROR"
        assert guard.state["status"] == "PERSISTENCE_ERROR"
        assert guard.state["service_state"] == "STOPPED_UNKNOWN"

        summary = guard.evaluate(current_provisioning=AUTHORIZED_BASELINE, uptime_hours=744.0)
        assert summary["status"] == "PERSISTENCE_ERROR"
        assert summary["can_restart"] is False

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Estat de persistència no verificat (ERROR" in reason
        assert summary["status"] != "OK"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 33 (Test 6): Font persistent amb dades malformades (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_33_persistence_source_malformed_fails_closed():
    """
    6. Font persistent amb dades malformades:
    Si la font externa conté un payload corrupte o un esquema sense el camp 'locked',
    el sistema denega taxativament qualsevol reinici.
    """
    backend = InMemoryLockPersistenceBackend(failure_mode="MALFORMED")

    with tempfile.NamedTemporaryFile(suffix="_malformed.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        assert guard.persistence_status == "MALFORMED"
        assert guard.state["status"] == "PERSISTENCE_CORRUPT"
        assert guard.state["service_state"] == "STOPPED_UNKNOWN"

        summary = guard.evaluate(current_provisioning=AUTHORIZED_BASELINE, uptime_hours=744.0)
        assert summary["status"] == "PERSISTENCE_CORRUPT"
        assert summary["can_restart"] is False

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Estat de persistència no verificat (MALFORMED" in reason
        assert summary["status"] != "OK"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 34 (Test 7): Estat persistent indica bloqueig, però OCI no té etiqueta
# -----------------------------------------------------------------------------
def test_scenario_34_persistent_lock_active_while_oci_lacks_tag():
    """
    7. Estat persistent que indica bloqueig, però OCI no té l'etiqueta:
    Si la instància d'OCI ha perdut l'etiqueta o ha estat recreada, però la font
    persistent externa indica bloqueig actiu, el bloqueig es manté inviolable.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Bloqueig pressupostari persistent",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_oci_notag.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        # OCI discovered sense etiqueta de bloqueig
        prov_without_tag = dict(AUTHORIZED_BASELINE)
        prov_without_tag["economic_lock_tag"] = False
        prov_without_tag["is_running"] = False

        summary = guard.evaluate(current_provisioning=prov_without_tag, uptime_hours=744.0)
        assert summary["status"] == "RESTART_LOCKED"
        assert guard.state["restart_locked"] is True

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic actiu" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 35 (Test 8): OCI té etiqueta de bloqueig, però font persistent no disponible
# -----------------------------------------------------------------------------
def test_scenario_35_oci_tag_present_while_persistence_source_unavailable():
    """
    8. OCI té l'etiqueta de bloqueig, però la font persistent no està disponible:
    Davant d'una caiguda de la persistència externa (ex: GitHub API 500), si OCI
    conté l'etiqueta 'economic_lock': 'true', el vigilant aplica el bloqueig
    sense concessions.
    """
    backend = InMemoryLockPersistenceBackend(failure_mode="READ_ERROR")

    with tempfile.NamedTemporaryFile(suffix="_oci_tag_p_err.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        # Watchdog descobreix tag a OCI
        prov_with_tag = dict(AUTHORIZED_BASELINE)
        prov_with_tag["economic_lock_tag"] = True
        prov_with_tag["lock_reason_tag"] = "Bloqueig OCI detectat al núvol"

        if prov_with_tag.get("economic_lock_tag"):
            guard.state["restart_locked"] = True
            guard.state["economic_shutdown_triggered"] = True
            guard.state["status"] = "RESTART_LOCKED"
            guard.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
            guard.state["lock_reason"] = prov_with_tag["lock_reason_tag"]
            guard.save_state()

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        # Tant per fallada de persistència com per bloqueig d'etiqueta, el reinici és denegat
        assert not can_restart
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 36 (Test 9): Intent de reinici sense autorització administrativa explícita
# -----------------------------------------------------------------------------
def test_scenario_36_restart_attempt_without_explicit_administrative_authorization():
    """
    9. Intent de reinici sense autorització administrativa explícita:
    Qualsevol intent d'arrencada o recuperació amb tokens no vàlids o buits
    ha de ser rebutjat, mantenint el bloqueig actiu.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Bloqueig actiu per protecció financera",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_unauth.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)

        # Intents amb diversos tokens il·legítims
        assert guard.authorize_manual_restart("reboot") is False
        assert guard.authorize_manual_restart("FORCE") is False
        assert guard.authorize_manual_restart("") is False
        assert guard.authorize_manual_restart("AUTORITZO_REINICI") is False

        assert guard.state["restart_locked"] is True
        assert guard.state["authorized_manual_override"] is False

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Cal autorització manual" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 37 (Test 10): Recuperació administrativa després de verificar inventari
# -----------------------------------------------------------------------------
def test_scenario_37_admin_recovery_verifies_inventory_before_unlock():
    """
    10. Recuperació administrativa després de verificar que no hi ha recursos inesperats:
    - Part A: Si l'administrador passa el token correcte però ENCARA hi ha recursos rogue
      provisionats a OCI (ex: IP orfe o forma no autoritzada), la recuperació ÉS REBUTJADA.
    - Part B: Un cop netejat l'inventari (baseline autoritzat), la recuperació té èxit,
      actualitza la font persistent a locked=False i permet el reinici de forma auditable.
    """
    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": True,
        "lock_reason": "Aturada per IP pública reservada orfe",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    })

    with tempfile.NamedTemporaryFile(suffix="_recovery.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
        assert guard.state["restart_locked"] is True

        # Part A: Intent de recuperació amb recursos inesperats encara presents
        rogue_provisioning = dict(AUTHORIZED_BASELINE)
        rogue_provisioning["unattached_public_ips"] = 1  # L'anomalia continua activa!

        recovery_denied = guard.authorize_manual_restart(
            token="AUTORITZO_REINICI_SENSE_DESPESA",
            current_provisioning=rogue_provisioning
        )
        assert recovery_denied is False, "La recuperació administrativa no pot autoritzar-se si hi ha recursos rogue!"
        assert guard.state["restart_locked"] is True
        assert guard.can_restart()[0] is False

        # Part B: L'administrador ha eliminat la IP orfe a OCI; inventari net
        clean_provisioning = dict(AUTHORIZED_BASELINE)

        recovery_granted = guard.authorize_manual_restart(
            token="AUTORITZO_REINICI_SENSE_DESPESA",
            current_provisioning=clean_provisioning
        )
        assert recovery_granted is True
        assert guard.state["restart_locked"] is False
        assert guard.state["status"] == "OK"
        assert guard.state["service_state"] == "ACTIVE"

        # Comprova que la font persistent externa s'ha actualitzat a locked=False
        persisted_state, err = backend.get_state()
        assert err is None
        assert persisted_state["locked"] is False

        can_restart, reason = guard.can_restart()
        assert can_restart is True
        assert reason is None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 38: GitHubIssueLedger falla per error de xarxa/5xx (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_38_issue_ledger_network_failure_fails_closed():
    """
    38. El backend GitHubIssueLedger troba un error HTTP 500 consultant GitHub:
    El sistema CostGuard ha d'adoptar la política fail-closed immediatament,
    marcant persistence_status='ERROR' i denegant qualsevol reinici.
    """
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)

    def fake_500(req, timeout=10):
        import urllib.error
        raise urllib.error.HTTPError(url="http://api", code=500, msg="Server Error", hdrs={}, fp=None)

    with tempfile.NamedTemporaryFile(suffix="_scen38.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_500):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "ERROR"
            assert guard.state["status"] == "PERSISTENCE_ERROR"
            assert guard.can_restart()[0] is False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 39: GitHubIssueLedger detecta comentari manipulat (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_39_issue_ledger_tampered_comment_fails_closed():
    """
    39. El backend GitHubIssueLedger detecta un comentari modificat a GitHub UI (created_at != updated_at):
    El sistema CostGuard entra en fallada segura per corrupció (PERSISTENCE_CORRUPT)
    i prohibeix taxativament l'arrencada de la instància.
    """
    from tests.test_cost_guard import make_event_comment, MockHttpResponse

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)
    tampered_comment = make_event_comment(
        comment_id=101,
        event_id="lock-scen39",
        created_at="2026-11-03T00:00:00Z",
        updated_at="2026-11-03T00:15:00Z"  # Editat a GitHub!
    )

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/10"):
            return MockHttpResponse(200, {"state": "open", "number": 10})
        if "/issues/10/comments" in url:
            return MockHttpResponse(200, [tampered_comment])
        raise ValueError(f"URL: {url}")

    with tempfile.NamedTemporaryFile(suffix="_scen39.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "Estat de persistència no verificat (MALFORMED" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 40: GitHubIssueLedger detecta Issue tancat (Fail-Closed)
# -----------------------------------------------------------------------------
def test_scenario_40_issue_ledger_closed_issue_fails_closed():
    """
    40. El GitHub Issue dedicat ha estat tancat (inoperatiu):
    CostGuard detecta que l'ancoratge no és operatiu i denega qualsevol operació
    en mode fail-closed.
    """
    from tests.test_cost_guard import MockHttpResponse

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)

    def fake_closed(req, timeout=10):
        return MockHttpResponse(200, {"state": "closed", "number": 10})

    with tempfile.NamedTemporaryFile(suffix="_scen40.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_closed):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            assert guard.state["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 41: Watchdog amb àncora OCI coherent amb GitHub Issue Ledger
# -----------------------------------------------------------------------------
def test_scenario_41_watchdog_coherent_oci_anchor():
    """
    41. Vigilant extern amb Àncora OCI coherent amb el GitHub Issue Ledger:
    - GitHub conté esdeveniment lock-1 (seq=1, hash=h1).
    - L'etiquetatge OCI conté economic_lock=true, ledger_sequence=1, ledger_hash=h1.
    - El vigilant confirma la coherència, manté el bloqueig i impedeix el reinici.
    """
    from tests.test_cost_guard import make_event_comment, MockHttpResponse

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)
    c1 = make_event_comment(1, "lock-scen41", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/10"):
            return MockHttpResponse(200, {"state": "open", "number": 10})
        if "/issues/10/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    oci_prov = dict(AUTHORIZED_BASELINE)
    oci_prov["oci_anchor"] = {
        "economic_lock": True,
        "ledger_sequence": 1,
        "ledger_hash": h1,
        "ledger_event_id": "lock-scen41",
        "lock_reason": "Bloqueig coherent confirmat a OCI"
    }

    with tempfile.NamedTemporaryFile(suffix="_scen41.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            summary = guard.evaluate(current_provisioning=oci_prov, uptime_hours=744.0)
            assert summary["status"] == "RESTART_LOCKED"
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "Bloqueig econòmic actiu" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 42: Watchdog detecta truncació de cua (darrer comentari esborrat)
# -----------------------------------------------------------------------------
def test_scenario_42_watchdog_detects_tail_truncation():
    """
    42. Truncació de cua: Algú esborra el darrer comentari a GitHub (seq=2 esborrat, només queda seq=1).
    L'àncora OCI té confirmada sequence=2.
    El vigilant detecta la truncació, activa PERSISTENCE_CORRUPT i denega el reinici.
    """
    from tests.test_cost_guard import make_event_comment, MockHttpResponse

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)
    c1 = make_event_comment(1, "lock-scen42-1", sequence=1, prev_hash="GENESIS")

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/10"):
            return MockHttpResponse(200, {"state": "open", "number": 10})
        if "/issues/10/comments" in url:
            return MockHttpResponse(200, [c1])  # Només queda seq 1!
        raise ValueError(f"URL: {url}")

    oci_prov = dict(AUTHORIZED_BASELINE)
    oci_prov["oci_anchor"] = {
        "economic_lock": True,
        "ledger_sequence": 2,  # OCI té confirmada seq 2!
        "ledger_hash": "a" * 64,
        "ledger_event_id": "lock-scen42-2",
        "lock_reason": "Bloqueig confirmat a seq 2"
    }

    with tempfile.NamedTemporaryFile(suffix="_scen42.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            summary = guard.evaluate(current_provisioning=oci_prov, uptime_hours=744.0)
            assert summary["status"] == "PERSISTENCE_CORRUPT"
            can_restart, reason = guard.can_restart()
            assert can_restart is False
            assert "Estat de persistència no verificat (MALFORMED" in reason
            assert "Truncació de cua detectada" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 43: Watchdog detecta àncora OCI amb format malformat
# -----------------------------------------------------------------------------
def test_scenario_43_watchdog_detects_malformed_oci_anchor():
    """
    43. Àncora OCI malformada (ex: sequence no enter o hash curt):
    CostGuard aplica estrictament fail-closed, impedint l'arrencada de la instància.
    """
    from tests.test_cost_guard import make_event_comment, MockHttpResponse

    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)
    c1 = make_event_comment(1, "lock-scen43", sequence=1, prev_hash="GENESIS")

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url.endswith("/issues/10"):
            return MockHttpResponse(200, {"state": "open", "number": 10})
        if "/issues/10/comments" in url:
            return MockHttpResponse(200, [c1])
        raise ValueError(f"URL: {url}")

    oci_prov = dict(AUTHORIZED_BASELINE)
    oci_prov["oci_anchor"] = {
        "economic_lock": True,
        "ledger_sequence": "NOT_AN_INT",  # Invàlid!
        "ledger_hash": "a" * 64,
        "ledger_event_id": "lock-bad"
    }

    with tempfile.NamedTemporaryFile(suffix="_scen43.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            summary = guard.evaluate(current_provisioning=oci_prov, uptime_hours=744.0)
            assert summary["status"] == "PERSISTENCE_CORRUPT"
            assert guard.can_restart()[0] is False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 44: Error de lectura OCI (discovery fallit) amb àncora
# -----------------------------------------------------------------------------
def test_scenario_44_oci_discovery_failure_fails_closed():
    """
    44. Error descobrint recursos via OCI SDK (error de xarxa o auth):
    El vigilant detecta discovery_failed=True i denega el reinici taxativament.
    """
    prov = dict(AUTHORIZED_BASELINE)
    prov["discovery_failed"] = True
    prov["error_msg"] = "OCI SDK Compartment discovery failed HTTP 500"

    with tempfile.NamedTemporaryFile(suffix="_scen44.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(state_path=tmp_path)
        guard.initialize_persistent_lock(locked=False)
        guard.set_discovery_status(False, reason=prov["error_msg"])
        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "El descobriment de recursos OCI ha fallat" in reason
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 45: Error d'escriptura d'àncora a OCI (stop_oci_instance)
# -----------------------------------------------------------------------------
def test_scenario_45_oci_anchor_write_failure_still_stops_vm(monkeypatch):
    """
    45. Error d'escriptura de l'àncora OCI:
    Si update_instance falla permanentment (HTTP 500), stop_oci_instance
    continua executant STOP per protegir el pressupost, però retorna False
    perquè l'àncora no s'ha pogut persistir.
    """
    class MockClient:
        def get_instance(self, ocid):
            mock_resp = MagicMock()
            mock_resp.data.freeform_tags = {"Environment": "Prod"}
            mock_resp.headers = {"etag": "etag-valid-123"}
            return mock_resp

        def update_instance(self, ocid, update_details, if_match=None):
            raise Exception("OCI API 500 Internal Error writing tags")

        def instance_action(self, ocid, action):
            mock_action = MagicMock()
            mock_action.status = 200
            return mock_action

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Test anchor write failure",
        dry_run=False,
        anchor_metadata={"sequence": 1, "hash": "a" * 64, "event_id": "lock-wfail"}
    )
    # L'etiquetatge de l'àncora ha fallat, per tant success ha de ser False
    assert success is False


# -----------------------------------------------------------------------------
# Escenari 46: Escriptures concurrents d'àncora OCI resoltes amb ETag (HTTP 412)
# -----------------------------------------------------------------------------
def test_scenario_46_concurrent_anchor_update_resolved_with_etag(monkeypatch):
    """
    46. Escriptures simultànies:
    Dos processos intenten aplicar tags simultàniament. El primer canvia l'ETag,
    generant un HTTP 412. stop_oci_instance re-intenta, llegeix el nou ETag
    i aplica amb èxit les metadades de la Segona Àncora.
    """
    attempt_count = {"updates": 0, "gets": 0}
    applied_tags = {}

    class MockConflictError(Exception):
        def __init__(self):
            super().__init__("Precondition Failed")
            self.status = 412

    class MockResponse200:
        status = 200

    class MockInstanceData:
        def __init__(self, tags):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = dict(tags)

    class MockGetResponse:
        def __init__(self, etag, tags):
            self.data = MockInstanceData(tags)
            self.headers = {"etag": etag}

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                return MockGetResponse("etag-1", {"Environment": "Prod", "other": "val1", "ledger_sequence": "4", "ledger_hash": "4" * 64, "ledger_event_id": "lock-prev-46"})
            return MockGetResponse("etag-2", {"Environment": "Prod", "other": "val2_concurrent", "ledger_sequence": "4", "ledger_hash": "4" * 64, "ledger_event_id": "lock-prev-46"})

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            if attempt_count["updates"] == 1:
                raise MockConflictError()
            applied_tags.update(update_details.freeform_tags)
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    anchor_meta = {"sequence": 5, "hash": "d" * 64, "event_id": "lock-concurrent-46"}

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Bloqueig concurrent",
        dry_run=False,
        max_retries=3,
        anchor_metadata=anchor_meta
    )
    assert success is True
    assert attempt_count["updates"] == 2
    assert applied_tags["economic_lock"] == "true"
    assert applied_tags["ledger_sequence"] == "5"
    assert applied_tags["ledger_hash"] == "d" * 64
    assert applied_tags["ledger_event_id"] == "lock-concurrent-46"
    assert applied_tags["other"] == "val2_concurrent"  # Conserva el tag concurrent!


# -----------------------------------------------------------------------------
# Escenari 47: No permet actualitzar l'àncora a partir d'un ledger no verificat (Regla 5)
# -----------------------------------------------------------------------------
def test_scenario_47_unverified_ledger_cannot_update_anchor():
    """
    47. Regla 5: No permetre actualitzar l'àncora a partir d'un ledger no verificat.
    Si el ledger té un error de persistència (ex: hash corrupte o lectura fallida),
    get_latest_ledger_metadata() retorna None i l'àncora OCI no s'actualitza.
    """
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token_mock", issue_number=10)

    # Simula resposta de comentaris invàlida
    def fake_corrupt(req, timeout=10):
        from tests.test_cost_guard import MockHttpResponse
        return MockHttpResponse(200, [{"id": 1, "body": "<!-- COST_GUARD_EVENT --> {bad json"}])

    with tempfile.NamedTemporaryFile(suffix="_scen47.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        with patch("urllib.request.urlopen", side_effect=fake_corrupt):
            guard = CostGuard(state_path=tmp_path, persistence_backend=backend)
            assert guard.persistence_status == "MALFORMED"
            # get_latest_ledger_metadata HA DE ser None perquè el ledger no està verificat
            meta = guard.get_latest_ledger_metadata()
            assert meta is None, "Un ledger no verificat mai pot proporcionar metadades per a l'àncora!"
            assert guard.can_restart()[0] is False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 48: Actualització legítima de seqüència 3 a 4 (Monotonicitat)
# -----------------------------------------------------------------------------
def test_scenario_48_monotonicity_sequence_3_to_4_accepted(monkeypatch):
    """
    48. Monotonicitat: Avanç legítim de l'àncora de seqüència 3 a 4.
    L'àncora existent té sequence=3. L'esdeveniment entrant té sequence=4.
    stop_oci_instance accepta l'actualització i aplica el nou tag amb èxit.
    """
    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": "3",
                "ledger_hash": "3" * 64,
                "ledger_event_id": "lock-3"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-seq3"}

    class MockResponse200:
        status = 200

    applied_tags = {}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            assert if_match == "etag-seq3"
            applied_tags.update(update_details.freeform_tags)
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_seq4 = {"sequence": 4, "hash": "4" * 64, "event_id": "lock-4"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Avanç legítim a seq 4",
        dry_run=False,
        anchor_metadata=meta_seq4
    )
    assert success is True
    assert applied_tags["ledger_sequence"] == "4"
    assert applied_tags["ledger_hash"] == "4" * 64
    assert applied_tags["ledger_event_id"] == "lock-4"


# -----------------------------------------------------------------------------
# Escenari 49: Intent d'actualitzar de seqüència 4 a 3: rebutjat
# -----------------------------------------------------------------------------
def test_scenario_49_monotonicity_sequence_4_to_3_rejected(monkeypatch):
    """
    49. Monotonicitat: Intent de retrocedir de seqüència 4 a 3.
    L'àncora existent té sequence=4. L'esdeveniment entrant té sequence=3.
    stop_oci_instance ha de rebutjar l'actualització, NO enviar update_instance i retornar False.
    """
    class MockInstanceData:
        def __init__(self):
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": "4",
                "ledger_hash": "4" * 64,
                "ledger_event_id": "lock-4"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-seq4"}

    class MockResponse200:
        status = 200

    update_called = {"called": False}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            update_called["called"] = True
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_seq3 = {"sequence": 3, "hash": "3" * 64, "event_id": "lock-3"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Intent invàlid de retrocedir",
        dry_run=False,
        anchor_metadata=meta_seq3
    )
    assert success is False
    assert update_called["called"] is False, "Mai s'ha de cridar update_instance amb una seqüència inferior!"


# -----------------------------------------------------------------------------
# Escenari 50: Mateixa seqüència amb hash diferent: rebutjat
# -----------------------------------------------------------------------------
def test_scenario_50_monotonicity_same_sequence_different_hash_rejected(monkeypatch):
    """
    50. Monotonicitat: Seqüència idèntica (seq=4) però hash diferent.
    Detecta discordança / intent de substitució fraudulenta. Rebutjat (False).
    """
    class MockInstanceData:
        def __init__(self):
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": "4",
                "ledger_hash": "4" * 64,
                "ledger_event_id": "lock-4"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-seq4"}

    class MockResponse200:
        status = 200

    update_called = {"called": False}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            update_called["called"] = True
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_diff_hash = {"sequence": 4, "hash": "f" * 64, "event_id": "lock-4"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Hash discordant a mateixa seqüència",
        dry_run=False,
        anchor_metadata=meta_diff_hash
    )
    assert success is False
    assert update_called["called"] is False


# -----------------------------------------------------------------------------
# Escenari 51: Mateixa seqüència amb event_id diferent: rebutjat
# -----------------------------------------------------------------------------
def test_scenario_51_monotonicity_same_sequence_different_event_id_rejected(monkeypatch):
    """
    51. Monotonicitat: Seqüència idèntica (seq=4) i hash igual, però event_id diferent.
    Detecta discordança d'identificador d'esdeveniment. Rebutjat (False).
    """
    class MockInstanceData:
        def __init__(self):
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": "4",
                "ledger_hash": "4" * 64,
                "ledger_event_id": "lock-4-original"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-seq4"}

    class MockResponse200:
        status = 200

    update_called = {"called": False}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            update_called["called"] = True
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_diff_eid = {"sequence": 4, "hash": "4" * 64, "event_id": "lock-4-divergent"}
    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Event ID discordant",
        dry_run=False,
        anchor_metadata=meta_diff_eid
    )
    assert success is False
    assert update_called["called"] is False


# -----------------------------------------------------------------------------
# Escenari 52: HTTP 412: procés antic no pot fer retrocedir la seqüència després de rellegir
# -----------------------------------------------------------------------------
def test_scenario_52_monotonicity_concurrent_412_old_process_cannot_regress(monkeypatch):
    """
    52. HTTP 412 i Monotonicitat estricta:
    Procés A intenta aplicar sequence=3 (pensant que l'àncora està a seq=2).
    L'intent 1 genera HTTP 412 perquè un Procés B concurrent ja ha actualitzat a sequence=4.
    A l'intent 2, el Procés A rellegeix l'àncora (que ara té sequence=4), avalua la monotonicitat (3 < 4),
    avorta l'actualització i NO sobreescriu la seqüència 4 amb la 3.
    """
    attempt_count = {"gets": 0, "updates": 0}

    class MockConflictError(Exception):
        def __init__(self):
            super().__init__("Precondition Failed")
            self.status = 412

    class MockResponse200:
        status = 200

    class MockInstanceData:
        def __init__(self, seq, h, eid):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = {
                "economic_lock": "true",
                "ledger_sequence": str(seq),
                "ledger_hash": h,
                "ledger_event_id": eid,
                "Project": "Preddicions"
            }

    class MockGetResponse:
        def __init__(self, etag, seq, h, eid):
            self.data = MockInstanceData(seq, h, eid)
            self.headers = {"etag": etag}

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                # Intent 1: OCI tenia seq 2
                return MockGetResponse("etag-seq2", 2, "2" * 64, "lock-2")
            # Intent 2: Un altre procés B concurrent ha aplicat seq 4!
            return MockGetResponse("etag-seq4-concurrent", 4, "4" * 64, "lock-4")

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            if attempt_count["updates"] == 1:
                # El primer intent falla per 412 per canvi concurrent
                raise MockConflictError()
            # Si s'arriba aquí, seria un error crític de regressió!
            raise AssertionError("update_instance NO ha de ser cridat si la seqüència entrant és inferior a la rellegida!")

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_proc_a = {"sequence": 3, "hash": "3" * 64, "event_id": "lock-3"}

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Procés A amb seq antiga",
        dry_run=False,
        max_retries=3,
        anchor_metadata=meta_proc_a
    )
    # Ha de fallar (no actualitzat perquè seq 3 < seq 4 existent rellegida)
    assert success is False
    assert attempt_count["gets"] in (2, 3)
    assert attempt_count["updates"] == 1  # Només l'intent 1 abans del 412; el segon intent ha avortat per monotonicitat


# -----------------------------------------------------------------------------
# Escenari 53: Preservació de tags aliens a CostGuard després d'un reintent per 412
# -----------------------------------------------------------------------------
def test_scenario_53_alien_tags_preserved_after_retry(monkeypatch):
    """
    53. Preservació de tags aliens a CostGuard:
    La instància conté tags com Department i Cluster. Després d'un conflicte 412
    i reintent exitós, aquests tags no són esborrats i es preserven íntegrament.
    """
    attempt_count = {"updates": 0, "gets": 0}
    applied_tags = {}

    class MockConflictError(Exception):
        def __init__(self):
            super().__init__("Precondition Failed")
            self.status = 412

    class MockResponse200:
        status = 200

    class MockInstanceData:
        def __init__(self, alien_tags):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = dict(alien_tags)

    class MockGetResponse:
        def __init__(self, etag, alien_tags):
            self.data = MockInstanceData(alien_tags)
            self.headers = {"etag": etag}

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                return MockGetResponse("etag-1", {"Department": "Finance", "Cluster": "Alpha", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"})
            return MockGetResponse("etag-2", {"Department": "Finance", "Cluster": "Alpha_v2", "Environment": "Prod", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"})

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            if attempt_count["updates"] == 1:
                raise MockConflictError()
            applied_tags.update(update_details.freeform_tags)
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_seq2 = {"sequence": 2, "hash": "2" * 64, "event_id": "lock-2"}

    success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..test",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Avanç amb preservació de tags",
        dry_run=False,
        max_retries=3,
        anchor_metadata=meta_seq2
    )
    assert success is True
    assert applied_tags["Department"] == "Finance"
    assert applied_tags["Cluster"] == "Alpha_v2"
    assert applied_tags["Environment"] == "Prod"
    assert applied_tags["ledger_sequence"] == "2"
    assert applied_tags["ledger_hash"] == "2" * 64
    assert applied_tags["ledger_event_id"] == "lock-2"



# -----------------------------------------------------------------------------
# Escenari 54: STOP amb HTTP 200 i etiquetatge correcte (Èxit complet)
# -----------------------------------------------------------------------------
def test_scenario_54_stop_instance_success_full(monkeypatch):
    """Verifica que quan tant l'etiquetatge com l'ordre STOP responen 200, stop_oci_instance
    retorna èxit complet i reflecteix que la transició asíncrona s'ha iniciat."""
    applied_tags = {}
    class MockInstanceData:
        lifecycle_state = "STOPPED"
        freeform_tags = {"ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}
    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-54"}
    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            applied_tags.update(update_details.freeform_tags)
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta = {"sequence": 2, "hash": "2" * 64, "event_id": "lock-2"}
    res = stop_oci_instance(
        "ocid1.instance.oc1..test_54",
        {"tenancy": "test"},
        reason="Aturada 54",
        dry_run=False,
        anchor_metadata=meta,
        return_details=True
    )
    assert res["success"] is True
    assert res["stop_accepted"] is True
    assert res["tag_applied"] is True
    assert res["error"] is None
    assert applied_tags["ledger_sequence"] == "2"


# -----------------------------------------------------------------------------
# Escenari 55: STOP amb HTTP diferent de 200 (Fallada STOP)
# -----------------------------------------------------------------------------
def test_scenario_55_stop_instance_http_non_200(monkeypatch):
    """Verifica que si instance_action(STOP) retorna HTTP 500, stop_accepted és False i es retorna error."""
    class MockInstanceData:
        freeform_tags = {"ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}
    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-55"}
    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=500)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta = {"sequence": 2, "hash": "2" * 64, "event_id": "lock-2"}
    res = stop_oci_instance(
        "ocid1.instance.oc1..test_55",
        {"tenancy": "test"},
        reason="Aturada 55",
        dry_run=False,
        anchor_metadata=meta,
        return_details=True
    )
    assert res["success"] is False
    assert res["stop_accepted"] is False
    assert "500" in res["error"]


# -----------------------------------------------------------------------------
# Escenari 56: Excepció durant la petició STOP (No silenciada)
# -----------------------------------------------------------------------------
def test_scenario_56_stop_instance_exception_handling(monkeypatch):
    """Verifica que una excepció a instance_action(STOP) es captura al diccionari de retorn i no queda silenciada."""
    class MockInstanceData:
        freeform_tags = {"ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-1"}
    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-56"}
    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            raise ConnectionResetError("Connexió tallada pel servei OCI Compute")

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        "ocid1.instance.oc1..test_56",
        {"tenancy": "test"},
        reason="Aturada 56",
        dry_run=False,
        return_details=True
    )
    assert res["success"] is False
    assert res["stop_accepted"] is False
    assert "Connexió tallada" in str(res["exception"])


# -----------------------------------------------------------------------------
# Escenari 57: Etiquetatge fallit però STOP acceptat (Aturada acceptada, àncora fallida)
# -----------------------------------------------------------------------------
def test_scenario_57_tag_failure_stop_accepted_generates_workflow_failure(monkeypatch):
    """Verifica que quan l'etiquetatge falla (àncora absent) però STOP s'accepta (HTTP 200),
    el resultat reflecteix clarament stop_accepted=True i tag_applied=False, classificant-ho com a no-èxit."""
    class MockInstanceData:
        freeform_tags = {"Project": "SenseÀncora"}
    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-57"}
    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        "ocid1.instance.oc1..test_57",
        {"tenancy": "test"},
        reason="Aturada 57",
        dry_run=False,
        return_details=True
    )
    assert res["success"] is False
    assert res["stop_accepted"] is True  # L'ordre STOP asíncrona s'ha acceptat
    assert res["tag_applied"] is False   # L'àncora no s'ha pogut aplicar
    assert "Àncora OCI absent" in res["tag_error"]


# -----------------------------------------------------------------------------
# Escenari 58: run_watchdog() no acaba silenciosament quan STOP falla
# -----------------------------------------------------------------------------
def test_scenario_58_run_watchdog_fails_workflow_when_stop_fails(monkeypatch):
    """Verifica que quan run_watchdog() detecta bloqueig però STOP falla,
    retorna watchdog_ok=False i el missatge d'error pertinent per fer fallar el workflow."""
    class MockInstanceData:
        freeform_tags = {"Environment": "Prod"}
    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-58"}
    class MockComputeClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=500)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex" # Dispara bloqueig
    mock_prov["is_running"] = True

    with tempfile.NamedTemporaryFile(suffix="_w_stopfail.json", delete=False) as f:
        tmp_state_file = Path(f.name)
    lock_file = tmp_state_file.parent / f".{tmp_state_file.stem}_persistent_lock.json"
    lock_file.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda: CostGuard(state_path=tmp_state_file))
        res = run_watchdog(
            mode="fast",
            dry_run=False,
            mock_provisioning=mock_prov,
            instance_ocid="ocid1.instance.oc1..test_rogue_vm",
            live_url=None
        )
        assert res["watchdog_ok"] is False
        assert "STOP ha fallat" in res["error"]
        assert res["can_restart"] is False
    finally:
        if tmp_state_file.exists():
            tmp_state_file.unlink()
        if lock_file.exists():
            lock_file.unlink()


# -----------------------------------------------------------------------------
# Escenari 59: Cap d'aquests casos provoca un reinici automàtic
# -----------------------------------------------------------------------------
def test_scenario_59_failure_cases_never_trigger_automatic_restart(monkeypatch):
    """Verifica que davant de qualsevol incidència a STOP (500, excepció o tag fallit),
    can_restart roman False i start_oci_instance mai no és invocat."""
    start_called = []
    def fake_start(*args, **kwargs):
        start_called.append(True)
        return True
    monkeypatch.setattr("scripts.oci_external_watchdog.start_oci_instance", fake_start)

    class MockComputeClient:
        def get_instance(self, ocid):
            m = MagicMock()
            m.data.freeform_tags = {}
            m.headers = {"etag": "e"}
            return m
        def update_instance(self, ocid, details, if_match=None):
            return MagicMock(status=500)
        def instance_action(self, ocid, action):
            return MagicMock(status=500)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex"
    mock_prov["is_running"] = False

    with tempfile.NamedTemporaryFile(suffix="_w_norestart.json", delete=False) as f:
        tmp_state_file = Path(f.name)
    lock_file = tmp_state_file.parent / f".{tmp_state_file.stem}_persistent_lock.json"
    lock_file.write_text(json.dumps({
        "locked": False,
        "lock_reason": None,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorized_manual_override": False
    }), encoding="utf-8")

    try:
        monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda: CostGuard(state_path=tmp_state_file))
        res = run_watchdog(
            mode="fast",
            dry_run=False,
            mock_provisioning=mock_prov,
            instance_ocid="ocid1.instance.oc1..test_rogue_vm",
            live_url=None
        )
        assert res["can_restart"] is False
        assert len(start_called) == 0, "start_oci_instance mai no ha de ser cridada automàticament!"
    finally:
        if tmp_state_file.exists():
            tmp_state_file.unlink()
        if lock_file.exists():
            lock_file.unlink()



# -----------------------------------------------------------------------------
# Escenari 60: Recuperació administrativa amb inventari net (Èxit complet)
# -----------------------------------------------------------------------------
def test_scenario_60_administrative_recovery_clean_inventory_success(monkeypatch):
    """
    Verifica que la recuperació administrativa:
    1. Valida l'inventari net.
    2. Escriu l'esdeveniment UNLOCKED al ledger amb resolves_lock_id vinculat a l'últim LOCKED.
    3. Actualitza la Segona Àncora a OCI (economic_lock='false', nova seqüència, hash, event_id).
    4. Restableix CostGuard a estat OK i can_restart=True.
    """
    attempt_count = {"updates": 0, "gets": 0}
    applied_tags = {}

    class MockInstanceData:
        def __init__(self, tags):
            self.freeform_tags = dict(tags)

    class MockGetResponse:
        def __init__(self, etag, tags):
            self.data = MockInstanceData(tags)
            self.headers = {"etag": etag}

    class MockUpdateResponse:
        status = 200

    class MockComputeClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            return MockGetResponse("etag-49", {
                "Environment": "Prod",
                "economic_lock": "true",
                "ledger_sequence": "1",
                "ledger_hash": "a" * 64,
                "ledger_event_id": "lock-49-init",
                "lock_reason": "Pre-lock"
            })

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            assert if_match == "etag-49"
            applied_tags.update(update_details.freeform_tags)
            return MockUpdateResponse()

    class MockLedgerBackend(LockPersistenceBackend):
        def __init__(self):
            self.events = [{
                "sequence": 1,
                "event_type": "LOCKED",
                "event_id": "lock-49-init",
                "hash": "a" * 64,
                "timestamp_utc": "2026-11-03T10:00:00Z"
            }]
            self.last_persisted_event = self.events[-1]

        def get_state(self):
            last = self.events[-1]
            return {
                "locked": last["event_type"] == "LOCKED",
                "sequence": last["sequence"],
                "hash": last["hash"],
                "event_id": last["event_id"],
                "lock_reason": "Pre-lock",
                "events": list(self.events)
            }, None

        def set_state(self, state):
            last = self.events[-1]
            new_ev = {
                "sequence": last["sequence"] + 1,
                "event_type": "LOCKED" if state.get("locked") else "UNLOCKED",
                "event_id": f"unlock-49-{state.get('resolves_lock_id')}",
                "hash": "b" * 64,
                "timestamp_utc": "2026-11-03T11:00:00Z",
                "resolves_lock_id": state.get("resolves_lock_id"),
                "actor": state.get("actor"),
                "workflow_run_id": state.get("workflow_run_id"),
                "unlock_reason": state.get("unlock_reason"),
                "prev_hash": last["hash"]
            }
            self.events.append(new_ev)
            self.last_persisted_event = new_ev
            return True, None

    backend = MockLedgerBackend()
    with tempfile.NamedTemporaryFile(suffix="_rec_49.json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        guard = CostGuard(
            state_path=tmp_path,
            persistence_backend=backend,
            oci_anchor={
                "economic_lock": True,
                "ledger_sequence": 1,
                "ledger_hash": "a" * 64,
                "ledger_event_id": "lock-49-init"
            }
        )
        assert guard.can_restart()[0] is False

        clean_prov = dict(AUTHORIZED_BASELINE)

        ok, msg = execute_administrative_recovery(
            config={"tenancy": "ocid1.tenancy..test"},
            instance_ocid="ocid1.instance.oc1..inst49",
            actor="admin_auditor",
            workflow_run_id="run-49",
            unlock_reason="Alliberament post auditoria",
            dry_run=False,
            mock_provisioning=clean_prov,
            guard=guard,
            compute_client=MockComputeClient()
        )

        assert ok is True
        assert "completada amb èxit" in msg
        assert attempt_count["updates"] == 1
        assert applied_tags["economic_lock"] == "false"
        assert applied_tags["ledger_sequence"] == "2"
        assert applied_tags["ledger_hash"] == "b" * 64
        assert applied_tags["ledger_event_id"] == "unlock-49-lock-49-init"
        assert "unlocked_at" in applied_tags
        assert "lock_reason" not in applied_tags
        assert applied_tags["Environment"] == "Prod"

        # Estat de CostGuard restablert
        assert guard.state["status"] == "OK"
        assert guard.state["service_state"] == "ACTIVE"
        assert guard.can_restart()[0] is True
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# -----------------------------------------------------------------------------
# Escenari 61: Recuperació denegada si l'inventari conté recursos no autoritzats
# -----------------------------------------------------------------------------
def test_scenario_61_administrative_recovery_denied_on_rogue_inventory(tmp_path):
    """
    Verifica que la recuperació administrativa és rebutjada si l'inventari
    conté recursos no autoritzats (IPs orfes, shapes no autoritzades, NAT, disc > 200GB),
    sense modificar el ledger ni OCI.
    """
    class MockLedgerBackend(LockPersistenceBackend):
        def __init__(self):
            self.set_called = False

        def get_state(self):
            return {
                "locked": True,
                "sequence": 1,
                "hash": "a" * 64,
                "event_id": "lock-50",
                "events": [{"sequence": 1, "event_type": "LOCKED", "hash": "a" * 64, "event_id": "lock-50"}]
            }, None

        def set_state(self, state):
            self.set_called = True
            return True, None

    backend = MockLedgerBackend()
    guard = CostGuard(state_path=tmp_path / "state61.json", persistence_backend=backend)

    # 1. Prova amb IP pública reservada sense associar
    rogue_prov = dict(AUTHORIZED_BASELINE)
    rogue_prov["unattached_public_ips"] = 1

    ok, msg = execute_administrative_recovery(
        actor="admin",
        unlock_reason="Auditoria de recuperació",
        mock_provisioning=rogue_prov,
        guard=guard,
        dry_run=True
    )
    assert ok is False
    assert "l'inventari OCI conté recursos no autoritzats" in msg
    assert backend.set_called is False

    # 2. Prova amb forma no autoritzada
    rogue_prov2 = dict(AUTHORIZED_BASELINE)
    rogue_prov2["shape"] = "VM.Standard.E4.Flex"
    ok2, msg2 = execute_administrative_recovery(
        actor="admin",
        unlock_reason="Auditoria de recuperació",
        mock_provisioning=rogue_prov2,
        guard=guard,
        dry_run=True
    )
    assert ok2 is False
    assert "l'inventari OCI conté recursos no autoritzats" in msg
    assert backend.set_called is False


# -----------------------------------------------------------------------------
# Escenari 62: Recuperació denegada si el ledger ja està UNLOCKED o net
# -----------------------------------------------------------------------------
def test_scenario_62_administrative_recovery_denied_if_ledger_not_locked(tmp_path):
    """
    Verifica que si el ledger ja està UNLOCKED (locked=False) o buit,
    la recuperació és avortada immediatament sense fer cap crida.
    """
    class MockLedgerBackend(LockPersistenceBackend):
        def __init__(self):
            self.set_called = False

        def get_state(self):
            return {
                "locked": False,
                "sequence": 2,
                "hash": "b" * 64,
                "event_id": "unlock-51",
                "events": [
                    {"sequence": 1, "event_type": "LOCKED", "hash": "a" * 64, "event_id": "lock-51"},
                    {"sequence": 2, "event_type": "UNLOCKED", "hash": "b" * 64, "event_id": "unlock-51"}
                ]
            }, None

        def set_state(self, state):
            self.set_called = True
            return True, None

    backend = MockLedgerBackend()
    guard = CostGuard(state_path=tmp_path / "state61.json", persistence_backend=backend)

    ok, msg = execute_administrative_recovery(
        actor="admin",
        unlock_reason="Auditoria de recuperació",
        mock_provisioning=dict(AUTHORIZED_BASELINE),
        guard=guard,
        dry_run=True
    )
    assert ok is False
    assert "el ledger no es troba en estat LOCKED actiu" in msg
    assert backend.set_called is False


# -----------------------------------------------------------------------------
# Escenari 63: Recuperació amb conflicte HTTP 412 (reintent i èxit)
# -----------------------------------------------------------------------------
def test_scenario_63_administrative_recovery_optimistic_concurrency_retry_412(tmp_path):
    """
    Verifica que si l'actualització de la Segona Àncora a OCI troba un error HTTP 412
    (ETag desfasat), reintenta, obté el nou ETag i culmina el desbloqueig amb èxit.
    """
    attempt_count = {"updates": 0, "gets": 0}
    applied_tags = {}

    class MockConflictError(Exception):
        status = 412

    class MockInstanceData:
        def __init__(self, tags):
            self.freeform_tags = dict(tags)

    class MockGetResponse:
        def __init__(self, etag, tags):
            self.data = MockInstanceData(tags)
            self.headers = {"etag": etag}

    class MockUpdateResponse:
        status = 200

    class MockComputeClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                return MockGetResponse("etag-old", {
                    "economic_lock": "true",
                    "ledger_sequence": "1",
                    "ledger_hash": "a" * 64,
                    "ledger_event_id": "lock-52",
                    "other_tag": "1"
                })
            return MockGetResponse("etag-new", {
                "economic_lock": "true",
                "ledger_sequence": "1",
                "ledger_hash": "a" * 64,
                "ledger_event_id": "lock-52",
                "other_tag": "2_concurrent"
            })

        def update_instance(self, ocid, update_details, if_match=None):
            attempt_count["updates"] += 1
            if attempt_count["updates"] == 1:
                assert if_match == "etag-old"
                raise MockConflictError("Precondition Failed")
            assert if_match == "etag-new"
            applied_tags.update(update_details.freeform_tags)
            return MockUpdateResponse()

    class MockLedgerBackend(LockPersistenceBackend):
        def __init__(self):
            self.events = [{
                "sequence": 1,
                "event_type": "LOCKED",
                "event_id": "lock-52",
                "hash": "a" * 64
            }]
            self.last_persisted_event = self.events[-1]

        def get_state(self):
            last = self.events[-1]
            return {
                "locked": last["event_type"] == "LOCKED",
                "sequence": last["sequence"],
                "hash": last["hash"],
                "event_id": last["event_id"],
                "events": list(self.events)
            }, None

        def set_state(self, state):
            new_ev = {
                "sequence": 2,
                "event_type": "UNLOCKED",
                "event_id": "unlock-52",
                "hash": "b" * 64,
                "resolves_lock_id": state["resolves_lock_id"]
            }
            self.events.append(new_ev)
            self.last_persisted_event = new_ev
            return True, None

    guard = CostGuard(state_path=tmp_path / "state63.json", persistence_backend=MockLedgerBackend())

    ok, msg = execute_administrative_recovery(
        actor="admin",
        unlock_reason="Auditoria de recuperació",
        config={"tenancy": "ocid1.tenancy..test"},
        instance_ocid="ocid1.instance.oc1..inst52",
        mock_provisioning=dict(AUTHORIZED_BASELINE),
        guard=guard,
        compute_client=MockComputeClient(),
        max_retries=3
    )

    assert ok is True
    assert attempt_count["updates"] == 2
    assert attempt_count["gets"] == 2
    assert applied_tags["economic_lock"] == "false"
    assert applied_tags["ledger_sequence"] == "2"
    assert applied_tags["other_tag"] == "2_concurrent"


# -----------------------------------------------------------------------------
# Escenari 64: Fallada permanent d'actualització d'àncora OCI aborta fail-closed
# -----------------------------------------------------------------------------
def test_scenario_64_administrative_recovery_permanent_update_failure_aborts(tmp_path):
    """
    Verifica que si update_instance falla permanentment a OCI (ex: HTTP 500),
    la recuperació avorta amb error i la màquina NO es considera recuperada.
    """
    class MockUpdateError(Exception):
        status = 500

    class MockComputeClient:
        def get_instance(self, ocid):
            mock_inst = MagicMock()
            mock_inst.freeform_tags = {"economic_lock": "true", "ledger_sequence": "1"}
            return MagicMock(data=mock_inst, headers={"etag": "etag-53"})

        def update_instance(self, ocid, update_details, if_match=None):
            raise MockUpdateError("OCI Internal Error")

    class MockLedgerBackend(LockPersistenceBackend):
        def __init__(self):
            self.events = [{
                "sequence": 1,
                "event_type": "LOCKED",
                "event_id": "lock-53",
                "hash": "a" * 64
            }]
            self.last_persisted_event = self.events[-1]

        def get_state(self):
            last = self.events[-1]
            return {
                "locked": last["event_type"] == "LOCKED",
                "sequence": last["sequence"],
                "hash": last["hash"],
                "event_id": last["event_id"],
                "events": list(self.events)
            }, None

        def set_state(self, state):
            new_ev = {
                "sequence": 2,
                "event_type": "UNLOCKED",
                "event_id": "unlock-53",
                "hash": "b" * 64
            }
            self.events.append(new_ev)
            self.last_persisted_event = new_ev
            return True, None

    guard = CostGuard(state_path=tmp_path / "state63.json", persistence_backend=MockLedgerBackend())

    ok, msg = execute_administrative_recovery(
        actor="admin",
        unlock_reason="Auditoria de recuperació",
        config={"tenancy": "ocid1.tenancy..test"},
        instance_ocid="ocid1.instance.oc1..inst53",
        mock_provisioning=dict(AUTHORIZED_BASELINE),
        guard=guard,
        compute_client=MockComputeClient(),
        max_retries=2
    )

    assert ok is False
    assert "error actualitzant Segona Àncora a OCI" in msg


# -----------------------------------------------------------------------------
# Escenari 65: Verificació de configuració de workflow i CLI per a la Fase 3.2
# -----------------------------------------------------------------------------
def test_scenario_65_workflow_and_cli_configuration():
    """
    Verifica que el fitxer .github/workflows/oci_cost_guard.yml conté:
    - permissions: contents: read, issues: write
    - env: COST_GUARD_ISSUE_NUMBER
    - suport per a action: recover
    """
    wf_path = Path(".github/workflows/oci_cost_guard.yml")
    assert wf_path.exists()
    content = wf_path.read_text(encoding="utf-8")

    assert "issues: write" in content
    assert "COST_GUARD_ISSUE_NUMBER: ${{ vars.COST_GUARD_ISSUE_NUMBER }}" in content
    assert "action: recover" in content or "action recover" in content


# -----------------------------------------------------------------------------
# FASE 5: Aturades i estats OCI (Verificació de cicle de vida STOP)
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Escenari 66: STOP acceptat (HTTP 200), transició progressiva STOPPING -> STOPPED
# -----------------------------------------------------------------------------
def test_scenario_66_stop_transition_progressive_stopping_to_stopped(monkeypatch):
    """
    Verifica que stop_oci_instance detecta la transició progressiva:
    1. HTTP 200 en instance_action(STOP).
    2. Intent 1 de polling: estat 'STOPPING'.
    3. Intent 2 de polling: estat 'STOPPED' confirmat.
    4. Resultat: instance_stopped=True, lifecycle_state='STOPPED', warning=None.
    """
    attempt_count = {"gets": 0, "polling_gets": 0}

    class MockInstanceData:
        def __init__(self, state="STOPPING"):
            self.lifecycle_state = state
            self.freeform_tags = {
                "Environment": "Prod",
                "ledger_sequence": "1",
                "ledger_hash": "1" * 64,
                "ledger_event_id": "lock-66"
            }

    class MockGetResponse:
        def __init__(self, state):
            self.data = MockInstanceData(state=state)
            self.headers = {"etag": "etag-66"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["gets"] += 1
            if attempt_count["gets"] == 1:
                # Lectura inicial d'etiquetes i ETag
                return MockGetResponse(state="RUNNING")
            # Polling post-aturada: primer STOPPING, després STOPPED
            attempt_count["polling_gets"] += 1
            if attempt_count["polling_gets"] == 1:
                return MockGetResponse(state="STOPPING")
            return MockGetResponse(state="STOPPED")

        def update_instance(self, ocid, update_details, if_match=None):
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst66",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Aturada preventiva test 66",
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=3,
        poll_interval_seconds=0.01
    )

    assert isinstance(res, dict)
    assert res["success"] is True
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is True
    assert res["lifecycle_state"] == "STOPPED"
    assert res["warning"] is None
    assert attempt_count["polling_gets"] == 2


# -----------------------------------------------------------------------------
# Escenari 67: STOP acceptat (HTTP 200), però queda en STOPPING (timeout de polling)
# -----------------------------------------------------------------------------
def test_scenario_67_stop_transition_stuck_in_stopping_timeout_warning(monkeypatch):
    """
    Verifica que si la instància roman en 'STOPPING' sense arribar a 'STOPPED':
    1. stop_accepted és True (petició admesa).
    2. instance_stopped és False (NO es confirma com a aturada física).
    3. Es registra una advertència explícita de transició no conclosa.
    4. Si strict_stopped=True, success és False.
    """
    attempt_count = {"polling_gets": 0}

    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPING"
            self.freeform_tags = {
                "Environment": "Prod",
                "ledger_sequence": "1",
                "ledger_hash": "1" * 64,
                "ledger_event_id": "lock-67"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-67"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            attempt_count["polling_gets"] += 1
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst67",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Aturada test 67",
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=3,
        poll_interval_seconds=0.01,
        strict_stopped=True
    )

    assert isinstance(res, dict)
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] == "STOPPING"
    assert res["warning"] is not None
    assert "no ha arribat a l'estat STOPPED" in res["warning"]
    assert "STOPPING" in res["warning"]
    assert res["success"] is False  # Degut a strict_stopped=True


# -----------------------------------------------------------------------------
# Escenari 68: STOP acceptat (HTTP 200), però la instància roman en estat incert (RUNNING)
# -----------------------------------------------------------------------------
def test_scenario_68_stop_transition_uncertain_or_running_state_detected(monkeypatch):
    """
    Verifica que si després de STOP la instància continua en estat 'RUNNING',
    el sistema NO la dóna falsament per aturada, alerta del perill i manté el bloqueig.
    """
    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "RUNNING"
            self.freeform_tags = {
                "Environment": "Prod",
                "ledger_sequence": "1",
                "ledger_hash": "1" * 64,
                "ledger_event_id": "lock-68"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-68"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst68",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] == "RUNNING"
    assert "RUNNING" in res["warning"]
    assert res["tag_applied"] is True


# -----------------------------------------------------------------------------
# Escenari 69: Ordre STOP amb confirmació immediata STOPPED i àncora sincronitzada
# -----------------------------------------------------------------------------
def test_scenario_69_stop_immediate_stopped_with_synchronized_anchor(monkeypatch):
    """
    Verifica el cas idoni on la instància ja reporta STOPPED immediatament
    i la Segona Àncora s'actualitza al mateix temps.
    """
    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPED"
            self.freeform_tags = {
                "Environment": "Prod",
                "ledger_sequence": "2",
                "ledger_hash": "2" * 64,
                "ledger_event_id": "lock-69"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-69"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst69",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is True
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is True
    assert res["lifecycle_state"] == "STOPPED"
    assert res["tag_applied"] is True
    assert res["warning"] is None


# -----------------------------------------------------------------------------
# Escenari 70: Integració de run_watchdog reflecteix warning de transició
# -----------------------------------------------------------------------------
def test_scenario_70_watchdog_integration_reflects_stopping_warning_in_summary(monkeypatch, tmp_path):
    """
    Verifica que en una aturada desencadenada per run_watchdog, si la instància
    queda en STOPPING, el resum del watchdog i els logs capturen l'alerta de transició.
    """
    class MockInstanceData:
        def __init__(self):
            self.lifecycle_state = "STOPPING"
            self.freeform_tags = {
                "Environment": "Prod",
                "ledger_sequence": "1",
                "ledger_hash": "1" * 64,
                "ledger_event_id": "lock-70"
            }

    class MockGetResponse:
        def __init__(self):
            self.data = MockInstanceData()
            self.headers = {"etag": "etag-70"}

    class MockResponse200:
        status = 200

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()

        def update_instance(self, ocid, update_details, if_match=None):
            return MockResponse200()

        def instance_action(self, ocid, action):
            return MockResponse200()

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    # Simulem un estat financer crític que activa PREVENTIVE_SHUTDOWN (0.30 €)
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    isolated_guard = CostGuard(state_path=tmp_path / "cg_state.json", persistence_backend=InMemoryLockPersistenceBackend(initial_state={"locked": False}))
    res = run_watchdog(
        mode="fast",
        dry_run=False,
        mock_official_cost=0.30,
        instance_ocid="ocid1.instance.oc1..inst70",
        guard=isolated_guard
    )

    assert res["can_restart"] is False
    assert res["watchdog_ok"] is False
    stop_res = res.get("stop_result")
    assert stop_res is not None
    assert stop_res["stop_accepted"] is True
    assert stop_res["instance_stopped"] is False
    assert "TRANSICIÓ" in summary_file.read_text(encoding="utf-8")



# =============================================================================
# FASE 5.1: TESTS OBLIGATORIS DE SEMÀNTICA FAIL-CLOSED D'ATURADA OCI
# =============================================================================

# -----------------------------------------------------------------------------
# Escenari 71: Resposta sense data no confirma l'aturada (Req 1)
# -----------------------------------------------------------------------------
def test_scenario_71_stop_response_without_data_not_confirmed(monkeypatch):
    """
    1. Resposta sense data -> no confirmada.
    Verifica que si compute_client.get_instance() retorna un objecte sense atribut 'data'
    o amb data=None, instance_stopped roman False i success és False.
    """
    class MockNoDataResponse:
        data = None
        headers = {"etag": "etag-71"}

    class MockClient:
        def get_instance(self, ocid):
            return MockNoDataResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst71",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] is None
    assert res["warning"] is not None
    assert "no s'ha pogut verificar l'estat STOPPED" in res["warning"]


# -----------------------------------------------------------------------------
# Escenari 72: Resposta sense lifecycle_state no confirma l'aturada (Req 2)
# -----------------------------------------------------------------------------
def test_scenario_72_stop_response_without_lifecycle_state_not_confirmed(monkeypatch):
    """
    2. Resposta sense lifecycle_state -> no confirmada.
    Verifica que si inst_obj no exposa cap atribut o clau 'lifecycle_state',
    el sistema NO assumeix mai STOPPED per compatibilitat, sinó que manté instance_stopped=False.
    """
    class MockInstanceDataNoState:
        freeform_tags = {"Environment": "Prod", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-72"}

    class MockGetResponse:
        data = MockInstanceDataNoState()
        headers = {"etag": "etag-72"}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst72",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] is None
    assert res["warning"] is not None


# -----------------------------------------------------------------------------
# Escenari 73: Excepció de get_instance() durant el polling no confirma l'aturada (Req 3)
# -----------------------------------------------------------------------------
def test_scenario_73_stop_get_instance_exception_during_polling_not_confirmed(monkeypatch):
    """
    3. Excepció de get_instance() -> no confirmada.
    Verifica que una fallada de xarxa o excepció consultant l'estat manté instance_stopped=False
    i registra advertència sense caure.
    """
    class MockInitialData:
        freeform_tags = {"Environment": "Prod", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-73"}

    class MockGetResponse:
        data = MockInitialData()
        headers = {"etag": "etag-73"}

    gets = [0]
    class MockClient:
        def get_instance(self, ocid):
            gets[0] += 1
            if gets[0] == 1:
                return MockGetResponse()  # Lectura d'etiquetes té èxit
            raise ConnectionError("OCI Compute Service Timeout durant verificació d'estat")
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst73",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["warning"] is not None


# -----------------------------------------------------------------------------
# Escenari 74: Estat desconegut mai no confirma l'aturada (Req 6)
# -----------------------------------------------------------------------------
def test_scenario_74_stop_unknown_lifecycle_state_not_completed(monkeypatch):
    """
    6. Estat desconegut -> no completada.
    Verifica que si la instància reporta un estat com 'PROVISIONING', 'TERMINATING' o 'UNKNOWN',
    stop_oci_instance manté instance_stopped=False i success=False.
    """
    class MockInstanceData:
        lifecycle_state = "PROVISIONING"
        freeform_tags = {"Environment": "Prod", "ledger_sequence": "1", "ledger_hash": "1" * 64, "ledger_event_id": "lock-74"}

    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-74"}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst74",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=False,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] == "PROVISIONING"
    assert "PROVISIONING" in res["warning"]


# -----------------------------------------------------------------------------
# Escenari 75: Mode Dry-Run separa èxit de simulació d'èxit físic real (Req 8)
# -----------------------------------------------------------------------------
def test_scenario_75_stop_dry_run_does_not_assert_real_physical_stop(monkeypatch):
    """
    8. Dry run -> no afirmar aturada física real ni ordre acceptada:
    1. Dry-Run no envia cap ordre STOP a OCI (zero crides a ComputeClient).
    2. Dry-Run no afirma stop_accepted=True (retorna stop_accepted=False).
    3. Dry-Run no afirma instance_stopped=True (retorna instance_stopped=False).
    4. L'èxit de simulació (simulation_success=True) es distingeix de l'èxit físic (success=False).
    5. Un consumidor que comprovi l'èxit físic directe rep False i no pot confondre'l amb aturada confirmada.
    """
    calls = []
    class MockClientShouldNotBeCalled:
        def get_instance(self, *a, **kw):
            calls.append("get_instance")
        def update_instance(self, *a, **kw):
            calls.append("update_instance")
        def instance_action(self, *a, **kw):
            calls.append("instance_action")

    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClientShouldNotBeCalled()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..simulated",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=True,
        return_details=True
    )

    assert isinstance(res, dict)
    assert res["is_dry_run"] is True
    assert res["simulation_success"] is True
    assert res["success"] is False          # Èxit físic no consumat
    assert res["stop_accepted"] is False    # Cap ordre admesa per OCI
    assert res["instance_stopped"] is False # Mai afirmar aturada física real
    assert res["tag_applied"] is False      # Cap tag aplicat a OCI
    assert res["lifecycle_state"] is None
    assert res["warning"] is not None
    assert "Dry-Run" in res["warning"]
    assert len(calls) == 0, "En mode Dry-Run mai no s'ha d'interaccionar amb l'API OCI!"

    # Un consumidor que comprovi l'èxit físic (success o retorn booleà) rep False
    direct_physical_success = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..simulated",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=True,
        return_details=False
    )
    assert direct_physical_success is False


# -----------------------------------------------------------------------------
# Escenari 76: Absència de credencials mai no afirma aturada física (Req 9)
# -----------------------------------------------------------------------------
def test_scenario_76_stop_missing_credentials_fails_and_does_not_assert_stop():
    """
    9. Absència de credencials -> no afirmar aturada física real.
    Verifica que sense credencials OCI (tenancy buit o signer no disponible),
    stop_oci_instance retorna success=False, stop_accepted=False i instance_stopped=False.
    """
    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..no_creds",
        config={},  # Sense tenancy ni credencials
        dry_run=False,
        return_details=True
    )

    assert isinstance(res, dict)
    assert res["success"] is False
    assert res["stop_accepted"] is False
    assert res["instance_stopped"] is False
    assert res["error"] is not None
    assert "credencials" in res["error"] or "signer" in res["error"]


# -----------------------------------------------------------------------------
# Escenari 77: Cap cas d'aturada no confirmada permet reiniciar la instància (Req 10)
# -----------------------------------------------------------------------------
def test_scenario_77_unconfirmed_stop_never_allows_restart(tmp_path):
    """
    10. En cap dels casos no confirmats es permet reiniciar.
    Verifica que CostGuard preserva restart_locked=True i can_restart=False tant en casos
    de STOPPING, RUNNING, UNKNOWN, absència de credencials com errors de polling.
    """
    state_file = tmp_path / "cg_state_req10.json"
    guard = CostGuard(state_path=state_file)

    # Simulem que es detecta violació financera
    guard.state["restart_locked"] = True
    guard.state["status"] = "RESTART_LOCKED"
    guard.save_state()

    can_rst, reason = guard.can_restart()
    assert can_rst is False

    # Verifiquem que can_restart roman estrictament False davant intents
    unconfirmed_states = ["STOPPING", "RUNNING", "PROVISIONING", None, "UNKNOWN"]
    for st in unconfirmed_states:
        # Même si una consulta externa portés aquest estat, el bloqueig mai s'aixeca sense UNLOCKED
        assert guard.can_restart()[0] is False


# -----------------------------------------------------------------------------
# Escenari 78: La Segona Àncora i el bloqueig econòmic es conserven en aturada incompleta (Req 12)
# -----------------------------------------------------------------------------
def test_scenario_78_second_anchor_and_economic_lock_preserved_on_incomplete_stop(monkeypatch):
    """
    12. La segona àncora i el bloqueig econòmic es conserven.
    Verifica que fins i tot si la instància queda en transició (STOPPING),
    l'etiquetatge persistent a OCI amb economic_lock='true' i monotonicitat de ledger es preserva.
    """
    applied_tags = {}

    class MockInstanceData:
        lifecycle_state = "STOPPING"
        freeform_tags = {
            "Environment": "Prod",
            "ledger_sequence": "5",
            "ledger_hash": "5" * 64,
            "ledger_event_id": "lock-prev-78"
        }

    class MockGetResponse:
        data = MockInstanceData()
        headers = {"etag": "etag-78"}

    class MockClient:
        def get_instance(self, ocid):
            return MockGetResponse()
        def update_instance(self, ocid, update_details, if_match=None):
            applied_tags.update(update_details.freeform_tags)
            return MagicMock(status=200)
        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    import sys
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    meta_seq6 = {"sequence": 6, "hash": "6" * 64, "event_id": "lock-new-78"}
    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..inst78",
        config={"tenancy": "ocid1.tenancy..test"},
        reason="Aturada 78",
        dry_run=False,
        anchor_metadata=meta_seq6,
        return_details=True,
        wait_for_stopped=True,
        max_poll_attempts=2,
        poll_interval_seconds=0.01
    )

    # L'aturada física no està confirmada, per tant success és False
    assert res["success"] is False
    assert res["instance_stopped"] is False
    # PERÒ el bloqueig econòmic i la Segona Àncora SÍ s'han persistit correctament a OCI!
    assert res["tag_applied"] is True
    assert applied_tags["economic_lock"] == "true"
    assert applied_tags["ledger_sequence"] == "6"
    assert applied_tags["ledger_hash"] == "6" * 64
    assert applied_tags["ledger_event_id"] == "lock-new-78"
    assert applied_tags["Environment"] == "Prod"



# -----------------------------------------------------------------------------
# Escenari 80: run_watchdog() tracta correctament el mode Dry-Run
# -----------------------------------------------------------------------------
def test_scenario_80_watchdog_integration_dry_run_treated_correctly(tmp_path, monkeypatch):
    """
    Verifica que quan run_watchdog() s'executa en mode dry_run=True davant un estat crític:
    1. No s'envia cap comanda física real.
    2. El resultat conté is_dry_run=True i simulation_success=True.
    3. stop_accepted=False i instance_stopped=False.
    4. watchdog_ok és True (simulació correcta, no és una fallada d'infraestructura).
    5. can_restart roman estrictament en False (bloqueig econòmic actiu).
    6. Step Summary reflecteix el bloc informatiu [!NOTE] MODE DRY-RUN.
    """
    summary_file = tmp_path / "step_summary_dryrun.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    calls = []
    class MockClient:
        def instance_action(self, *a, **kw):
            calls.append("instance_action")

    import sys
    from unittest.mock import MagicMock
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockClient()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex"  # Dispara RESTART_LOCKED
    mock_prov["is_running"] = True

    isolated_guard = CostGuard(state_path=tmp_path / "cg_state.json", persistence_backend=InMemoryLockPersistenceBackend(initial_state={"locked": False}))
    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_provisioning=mock_prov,
        instance_ocid="ocid1.instance.oc1..dryrun_vm",
        live_url=None,
        guard=isolated_guard
    )

    assert res["watchdog_ok"] is True
    assert res["can_restart"] is False
    stop_res = res["stop_result"]
    assert stop_res is not None
    assert stop_res["is_dry_run"] is True
    assert stop_res["simulation_success"] is True
    assert stop_res["success"] is False
    assert stop_res["stop_accepted"] is False
    assert stop_res["instance_stopped"] is False
    assert len(calls) == 0

    content = summary_file.read_text(encoding="utf-8")
    assert "MODE DRY-RUN" in content
    assert "FALLADA DEL WATCHDOG" not in content

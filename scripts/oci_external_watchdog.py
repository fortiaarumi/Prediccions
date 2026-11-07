"""
scripts/oci_external_watchdog.py
================================
Vigilant extern independent (GitHub Actions) per al monitoratge d'estat,
inspecció de costos i aturada preventiva d'OCI.

Modes d'execució:
- fast (cada 10-15 min): Comprova l'endpoint en viu i l'estat de la instància.
- hourly (cada hora): Mode fast + descobriment complet de recursos provisionats + Usage API.
- daily (un cop al dia): Auditoria integral de despesa, inventari de volums/IPs i conciliació.

Seguretat i aïllament:
S'executa exclusivament des de GitHub Actions. Si la VM està apagada, el vigilant
continua analitzant les dades de facturació i els recursos de la tenancy.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.cost_guard import CostGuard, AUTHORIZED_BASELINE, THRESHOLDS, ALWAYS_FREE_LIMITS, validate_recovery_metadata


def check_live_endpoint(url: str, timeout: int = 5) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Comprova si l'endpoint web en viu respon i retorna el payload sanititzat."""
    if not url:
        return False, None
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PreddicionsLliga-Watchdog/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                return True, json.loads(body)
    except Exception as e:
        print(f"[Watchdog] L'endpoint en viu ({url}) no respon: {e}")
    return False, None


def validate_oci_credentials(
    config: Dict[str, Any],
    require_instance: bool = False,
    instance_ocid: Optional[str] = None,
    require_compartment: bool = False,
    compartment_ocid: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Valida de forma estricta (fail-closed) la presència dels identificadors i credencials OCI obligatoris.
    Retorna (valid: bool, error_msg: Optional[str]).
    Regles de seguretat:
    - Mai imprimeix ni revela el contingut de cap secret ni clau privada.
    - Identifica exhaustivament tots els paràmetres absents o buits.
    - Rebutja qualsevol valor simulat o fictici en operacions reals.
    """
    missing = []

    # 1. Credencials fonamentals d'autenticació OCI
    tenancy = config.get("tenancy")
    if not tenancy or not isinstance(tenancy, str) or not tenancy.strip() or tenancy.strip() == "ocid1.tenancy..simulated":
        missing.append("OCI_TENANCY_OCID")

    user = config.get("user")
    if not user or not isinstance(user, str) or not user.strip() or user.strip() == "ocid1.user..simulated":
        missing.append("OCI_USER_OCID")

    fingerprint = config.get("fingerprint")
    if not fingerprint or not isinstance(fingerprint, str) or not fingerprint.strip():
        missing.append("OCI_FINGERPRINT")

    key_content = config.get("key_content")
    if not key_content or not isinstance(key_content, str) or not key_content.strip():
        missing.append("OCI_PRIVATE_KEY")

    # 2. Regió OCI
    region = config.get("region")
    if region is None and "OCI_REGION" in os.environ:
        region = os.environ.get("OCI_REGION")
    if region is None:
        region = "eu-madrid-1"
    if not region or not isinstance(region, str) or not region.strip() or "simulated" in region.lower() or " " in region:
        missing.append("OCI_REGION")

    # 3. Identificador d'instància (si és requerit per a l'operació)
    if require_instance:
        inst = instance_ocid or config.get("instance_ocid")
        if not inst or not isinstance(inst, str) or not inst.strip() or "simulated" in inst.lower():
            missing.append("OCI_INSTANCE_OCID")

    # 4. Identificador de compartiment (si és requerit per al descobriment)
    if require_compartment:
        comp = compartment_ocid or config.get("compartment_ocid")
        if not comp or not isinstance(comp, str) or not comp.strip() or "simulated" in comp.lower():
            missing.append("OCI_COMPARTMENT_OCID")

    if missing:
        return False, f"Falten paràmetres OCI obligatoris ({', '.join(missing)}) per a l'operació real (no es permeten valors buits o simulats)."
    return True, None


def get_oci_signer(config: Dict[str, Any]):
    """Construeix el signer d'autenticació OCI si les credencials són presents."""
    if not config.get("tenancy") or not config.get("user") or not config.get("fingerprint") or not config.get("key_content"):
        return None
    try:
        import oci
        return oci.signer.Signer(
            tenancy=config["tenancy"],
            user=config["user"],
            fingerprint=config["fingerprint"],
            private_key_file_location=None,
            private_key_content=config.get("key_content")
        )
    except Exception as e:
        print(f"[Watchdog] No s'ha pogut inicialitzar el signer OCI: {e}")
        return None


def discover_oci_resources(
    config: Dict[str, Any],
    compartment_ocid: Optional[str]
) -> Dict[str, Any]:
    """
    Descobreix els recursos reals provisionats al compartiment i tenancy mitjançant OCI SDK.
    Si no hi ha credencials OCI, retorna la baseline per defecte.
    """
    comp_id = compartment_ocid or config.get("compartment_ocid") or config.get("tenancy")
    signer = get_oci_signer(config)

    if not signer or not comp_id:
        print("[Watchdog] Credencials OCI no disponibles per al descobriment.")
        failed_baseline = dict(AUTHORIZED_BASELINE)
        failed_baseline["discovery_failed"] = True
        failed_baseline["error_msg"] = "Credencials OCI o Compartment OCID absents"
        return failed_baseline

    try:
        import oci
        region = config.get("region", "eu-madrid-1")
        compute_client = oci.core.ComputeClient({"region": region}, signer=signer)
        block_client = oci.core.BlockstorageClient({"region": region}, signer=signer)
        net_client = oci.core.VirtualNetworkClient({"region": region}, signer=signer)

        discovered = {
            "shape": "VM.Standard.A1.Flex",
            "ocpus": 0.0,
            "ram_gb": 0.0,
            "is_running": False,
            "boot_volume_gb": 0.0,
            "extra_volumes_gb": 0.0,
            "attached_public_ips": 0,
            "unattached_public_ips": 0,
            "nat_gateways": 0,
            "load_balancers": 0,
            "anomalies_detected": []
        }

        # 1. Instàncies de computació
        instances = compute_client.list_instances(compartment_id=comp_id).data
        for inst in instances:
            if inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                shape = inst.shape
                discovered["shape"] = shape
                if shape != "VM.Standard.A1.Flex":
                    discovered["anomalies_detected"].append(f"Forma no autoritzada detectada: {shape}")

                # Shape config
                cfg = inst.shape_config
                if cfg:
                    discovered["ocpus"] += (cfg.ocpus or 0.0)
                    discovered["ram_gb"] += (cfg.memory_in_gbs or 0.0)

                if inst.lifecycle_state in ("RUNNING", "STARTING"):
                    discovered["is_running"] = True

                # Detecció de tag persistent de bloqueig econòmic i segona àncora a OCI
                tags = inst.freeform_tags or {}
                raw_seq = tags.get("ledger_sequence")
                seq_val = None
                if raw_seq is not None:
                    if isinstance(raw_seq, int) and not isinstance(raw_seq, bool):
                        seq_val = raw_seq
                    elif str(raw_seq).isdigit():
                        seq_val = int(raw_seq)
                    else:
                        seq_val = raw_seq

                discovered["oci_anchor"] = {
                    "economic_lock": tags.get("economic_lock") == "true",
                    "ledger_sequence": seq_val,
                    "ledger_hash": tags.get("ledger_hash"),
                    "ledger_event_id": tags.get("ledger_event_id"),
                    "lock_reason": tags.get("lock_reason"),
                    "locked_at": tags.get("locked_at"),
                    "raw_tags": dict(tags)
                }
                if tags.get("economic_lock") == "true":
                    discovered["economic_lock_tag"] = True
                    discovered["lock_reason_tag"] = tags.get(
                        "lock_reason", "Bloqueig econòmic persistent detectat a tags OCI"
                    )
                if tags.get("ledger_sequence"):
                    discovered["ledger_sequence_tag"] = tags.get("ledger_sequence")
                if tags.get("ledger_hash"):
                    discovered["ledger_hash_tag"] = tags.get("ledger_hash")
                if tags.get("ledger_event_id"):
                    discovered["ledger_event_id_tag"] = tags.get("ledger_event_id")

        # 2. Boot Volumes
        identity_client = oci.identity.IdentityClient({"region": region}, signer=signer)
        ads = identity_client.list_availability_domains(compartment_id=config["tenancy"]).data

        for ad in ads:
            bv_list = block_client.list_boot_volumes(availability_domain=ad.name, compartment_id=comp_id).data
            for bv in bv_list:
                if bv.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                    discovered["boot_volume_gb"] += (bv.size_in_gbs or 0.0)

            # Block Volumes addicionals
            vol_list = block_client.list_volumes(availability_domain=ad.name, compartment_id=comp_id).data
            for v in vol_list:
                if v.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                    discovered["extra_volumes_gb"] += (v.size_in_gbs or 0.0)

        # 3. Adreces IP Públiques (Reservades)
        public_ips = net_client.list_public_ips(scope="REGION", compartment_id=comp_id).data
        for ip in public_ips:
            if ip.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                if ip.assigned_entity_id:
                    discovered["attached_public_ips"] += 1
                else:
                    discovered["unattached_public_ips"] += 1
                    discovered["anomalies_detected"].append(f"IP pública reservada sense associar detectada: {ip.ip_address}")

        # 4. NAT Gateways
        nat_gws = net_client.list_nat_gateways(compartment_id=comp_id).data
        for gw in nat_gws:
            if gw.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                discovered["nat_gateways"] += 1
                discovered["anomalies_detected"].append(f"NAT Gateway no autoritzat detectat: {gw.display_name}")

        print(f"[Watchdog] Recursos reals descoberts a OCI: {discovered['shape']} | "
              f"OCPUs={discovered['ocpus']} | RAM={discovered['ram_gb']}GB | "
              f"Disc Total={discovered['boot_volume_gb'] + discovered['extra_volumes_gb']}GB | "
              f"IPs Orfes={discovered['unattached_public_ips']}")
        return discovered

    except Exception as e:
        print(f"[!] Error descobrint recursos via OCI SDK: {e}")
        failed_baseline = dict(AUTHORIZED_BASELINE)
        failed_baseline["discovery_failed"] = True
        failed_baseline["error_msg"] = str(e)
        return failed_baseline


def query_oci_usage_api(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Consulta l'OCI Usage API per obtenir el cost agregat del cicle actual."""
    signer = get_oci_signer(config)
    if not signer or not config.get("tenancy"):
        return None

    try:
        import oci
        region = config.get("region", "eu-madrid-1")
        usage_client = oci.usage_api.UsageapiClient({"region": region}, signer=signer)

        now = datetime.now(timezone.utc)
        # Consultar des del principi del mes en curs
        start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        details = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=config["tenancy"],
            time_usage_started=start_time,
            time_usage_ended=now,
            granularity="DAILY",
            query_type="COST"
        )
        response = usage_client.request_summarized_usages(details).data
        total_amount = 0.0
        for item in response.items:
            total_amount += (item.computed_amount or 0.0)

        print(f"[Watchdog] OCI Usage API consultada: {total_amount:.4f} USD/EUR acumulats.")
        return {
            "amount_eur": round(total_amount, 4),
            "timestamp_utc": now.isoformat()
        }
    except Exception as e:
        print(f"[Watchdog] OCI Usage API no disponible o denegada: {e}")
        return None


def stop_oci_instance(
    instance_ocid: str,
    config: Dict[str, Any],
    reason: str = "Aturada preventiva per motius econòmics",
    dry_run: bool = False,
    max_retries: int = 3,
    anchor_metadata: Optional[Dict[str, Any]] = None,
    return_details: bool = False,
    wait_for_stopped: bool = True,
    max_poll_attempts: int = 3,
    poll_interval_seconds: float = 0.5,
    strict_stopped: bool = True
) -> Union[bool, Dict[str, Any]]:
    """
    Envia una ordre d'aturada (STOP) a la instància per motius econòmics i aplica l'etiqueta de bloqueig
    i la Segona Àncora del ledger amb control de concurrència optimista (ETag / if_match).
    Preserva les etiquetes lliures existents, gestiona conflictes HTTP 412 amb reintents,
    i verifica mitjançant sondeig controlat la transició física cap a l'estat STOPPED.
    """
    print(f"[EMERGÈNCIA ECONÒMICA] Sol·licitant ATURADA de la instància {instance_ocid}...")
    tag_applied = False
    tag_error: Optional[str] = None
    stop_accepted = False
    stop_error: Optional[str] = None
    instance_stopped = False
    final_lifecycle_state: Optional[str] = None
    stop_warning: Optional[str] = None
    exception_captured: Optional[str] = None

    if dry_run:
        print("[Watchdog] Mode Dry-Run: comanda d'aturada simulada (sense acció física real).")
        stop_warning = "Mode Dry-Run: no s'ha executat ni verificat cap aturada física real a OCI."
        details = {
            "success": False,  # Representa l'èxit de l'operació física (no executada en dry-run)
            "simulation_success": True,  # La simulació s'ha completat satisfactòriament
            "stop_accepted": False,  # OCI no ha rebut ni acceptat cap comanda física real
            "instance_stopped": False,  # Mai afirmar aturada física real en mode dry-run!
            "lifecycle_state": None,
            "tag_applied": False,  # No s'ha modificat cap tag real a OCI
            "error": None,
            "warning": stop_warning,
            "tag_error": None,
            "stop_error": None,
            "exception": None,
            "is_dry_run": True
        }
        return details if return_details else False

    # Validació d'identificador d'instància en operació real
    if not instance_ocid or not isinstance(instance_ocid, str) or not instance_ocid.strip() or "simulated" in instance_ocid.lower():
        err_msg = "Falta el paràmetre obligatori 'OCI_INSTANCE_OCID' amb un identificador real vàlid per a l'operació real (no es permeten valors buits o simulats)."
        print(f"[!] ERROR CRÍTIC: {err_msg}")
        details = {
            "success": False,
            "stop_accepted": False,
            "instance_stopped": False,
            "lifecycle_state": None,
            "tag_applied": False,
            "error": err_msg,
            "warning": err_msg,
            "tag_error": err_msg,
            "stop_error": err_msg,
            "exception": None,
            "is_dry_run": False
        }
        return details if return_details else False

    signer = get_oci_signer(config)
    if not signer or not config.get("tenancy"):
        ok_creds, err_creds = validate_oci_credentials(config)
        details_err = err_creds or "absència de credencials o tenancy no configurat"
        tag_error = f"No s'ha pogut obtenir el signer d'autenticació OCI ({details_err})"
        print(f"[!] ERROR CRÍTIC: {tag_error}")
        details = {
            "success": False,
            "stop_accepted": False,
            "instance_stopped": False,
            "lifecycle_state": None,
            "tag_applied": False,
            "error": tag_error,
            "warning": "Absència de credencials OCI: no s'ha pogut enviar ni verificar l'aturada física.",
            "tag_error": tag_error,
            "stop_error": tag_error,
            "exception": None,
            "is_dry_run": False
        }
        return details if return_details else False

    try:
        import oci
        compute_client = oci.core.ComputeClient({"region": config.get("region", "eu-madrid-1")}, signer=signer)

        # 1. Aplicar etiqueta amb Concurrència Optimista (ETag + if_match)
        for attempt in range(1, max_retries + 1):
            try:
                get_resp = compute_client.get_instance(instance_ocid)
                current_inst = get_resp.data
                etag = get_resp.headers.get("etag") if hasattr(get_resp, "headers") and get_resp.headers else None

                # Validació estricta d'ETag: mai actualitzar sense control de concurrència
                if not etag or not isinstance(etag, str) or not etag.strip():
                    tag_error = f"ETag invàlid o absent de la instància {instance_ocid} (ETag='{etag}')"
                    print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització d'etiquetes avortada.")
                    break

                current_tags = dict(current_inst.freeform_tags or {})

                # Comprovació de monotonicitat respecte a les etiquetes actuals rellegides
                raw_existing_seq = current_tags.get("ledger_sequence")
                existing_seq: Optional[int] = None
                if raw_existing_seq is not None:
                    if isinstance(raw_existing_seq, bool):
                        tag_error = "Seqüència existent a l'àncora OCI no pot ser booleana"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break
                    elif isinstance(raw_existing_seq, int):
                        if raw_existing_seq <= 0:
                            tag_error = "Seqüència existent a l'àncora OCI ha de ser > 0"
                            print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                            break
                        existing_seq = raw_existing_seq
                    elif isinstance(raw_existing_seq, str) and raw_existing_seq.isdigit() and int(raw_existing_seq) > 0:
                        existing_seq = int(raw_existing_seq)
                    else:
                        tag_error = f"Seqüència existent a l'àncora OCI no és vàlida ({raw_existing_seq})"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break

                existing_hash = current_tags.get("ledger_hash")
                existing_event_id = current_tags.get("ledger_event_id")

                # REQUISIT FASE 2.3: Eliminar el bootstrap implícit en operació ordinària.
                # Si no existeix ledger_sequence o falten tags necessaris de l'àncora, rebutja l'actualització.
                if existing_seq is None:
                    tag_error = f"Àncora OCI absent a la instància {instance_ocid} (ledger_sequence absent)"
                    print(f"[!] ERROR CRÍTIC: {tag_error}. L'operació ordinària no permet bootstrap automàtic. Actualització avortada.")
                    break

                if not existing_hash or not isinstance(existing_hash, str) or len(existing_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in existing_hash):
                    tag_error = f"Àncora OCI parcial o invàlida (ledger_hash invàlid o absent: '{existing_hash}')"
                    print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                    break

                if not existing_event_id or not isinstance(existing_event_id, str) or not existing_event_id.strip():
                    tag_error = f"Àncora OCI parcial o invàlida (ledger_event_id invàlid o absent: '{existing_event_id}')"
                    print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                    break

                merged_tags = dict(current_tags)
                merged_tags["economic_lock"] = "true"
                merged_tags["lock_reason"] = reason
                merged_tags["locked_at"] = datetime.now(timezone.utc).isoformat()

                if anchor_metadata:
                    in_seq = anchor_metadata.get("sequence")
                    if isinstance(in_seq, bool) or not isinstance(in_seq, int) or in_seq <= 0:
                        tag_error = f"Seqüència entrant invàlida ({in_seq})"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break
                    in_hash = anchor_metadata.get("hash")
                    if not isinstance(in_hash, str) or len(in_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in in_hash):
                        tag_error = f"Hash entrant invàlid ({in_hash})"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break
                    in_event_id = anchor_metadata.get("event_id")
                    if not isinstance(in_event_id, str) or not in_event_id.strip():
                        tag_error = f"Event ID entrant invàlid ({in_event_id})"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break

                    if anchor_metadata.get("unverified"):
                        tag_error = "Metadades no provenen d'un ledger verificat"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break

                    if in_seq < existing_seq:
                        tag_error = f"Violació de monotonicitat! Seqüència entrant ({in_seq}) < seqüència existent ({existing_seq})"
                        print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                        break
                    elif in_seq == existing_seq:
                        if in_hash != existing_hash:
                            tag_error = f"Discrepància de hash a la mateixa seqüència {in_seq}: existent={existing_hash} vs entrant={in_hash}"
                            print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                            break
                        if in_event_id != existing_event_id:
                            tag_error = f"Discrepància d'event_id a la mateixa seqüència {in_seq}: existent={existing_event_id} vs entrant={in_event_id}"
                            print(f"[!] ERROR CRÍTIC: {tag_error}. Actualització avortada.")
                            break

                    merged_tags["ledger_sequence"] = str(in_seq)
                    merged_tags["ledger_hash"] = str(in_hash)
                    merged_tags["ledger_event_id"] = str(in_event_id)

                update_details = oci.core.models.UpdateInstanceDetails(freeform_tags=merged_tags)
                update_resp = compute_client.update_instance(instance_ocid, update_details, if_match=etag.strip())

                if update_resp.status == 200:
                    tag_applied = True
                    tag_error = None
                    print(f"[Watchdog] Etiqueta de bloqueig persistent aplicada (intent {attempt}, ETag={etag.strip()}).")
                    break
                else:
                    tag_error = f"update_instance ha retornat codi HTTP {update_resp.status}"
                    print(f"[!] Advertència: {tag_error}")
            except Exception as e:
                is_conflict = (hasattr(e, "status") and e.status == 412) or "412" in str(e)
                if is_conflict and attempt < max_retries:
                    print(f"[!] Conflicte de concurrència (HTTP 412, ETag desfasat) a l'intent {attempt}/{max_retries}. Reintentant...")
                    time.sleep(0.5)
                    continue
                else:
                    tag_error = f"Error aplicant etiqueta persistent a OCI (intent {attempt}): {e}"
                    print(f"[!] ERROR CRÍTIC: {tag_error}")
                    break

        # 2. Aturar la instància (Emergency Stop)
        # S'executa SEMPRE per protegir el pressupost, fins i tot si l'etiquetatge ha fallat
        action_resp = compute_client.instance_action(instance_ocid, "STOP")
        if action_resp.status == 200:
            stop_accepted = True
            print("[Watchdog] Ordre STOP enviada amb èxit a OCI (petició asíncrona acceptada).")

            # 3. Verificació de l'estat físic final (Polling fins a STOPPED)
            if wait_for_stopped:
                for poll_attempt in range(1, max_poll_attempts + 1):
                    try:
                        get_state_resp = compute_client.get_instance(instance_ocid)
                        inst_obj = get_state_resp.data if hasattr(get_state_resp, "data") else get_state_resp
                        raw_state = getattr(inst_obj, "lifecycle_state", None)
                        if raw_state is None and isinstance(inst_obj, dict):
                            raw_state = inst_obj.get("lifecycle_state")

                        final_lifecycle_state = raw_state

                        if final_lifecycle_state == "STOPPED":
                            instance_stopped = True
                            print(f"[Watchdog] Confirmació d'aturada física: instància {instance_ocid} és STOPPED (intent {poll_attempt}).")
                            break
                        elif final_lifecycle_state == "STOPPING":
                            print(f"[Watchdog] Instància {instance_ocid} en procés d'aturada: estat STOPPING (intent {poll_attempt}/{max_poll_attempts}).")
                        else:
                            print(f"[Watchdog] Estat de la instància {instance_ocid}: '{final_lifecycle_state}' (intent {poll_attempt}/{max_poll_attempts}).")

                        if poll_attempt < max_poll_attempts and poll_interval_seconds > 0:
                            time.sleep(poll_interval_seconds)
                    except Exception as poll_e:
                        print(f"[!] Error consultant estat de la instància {instance_ocid} (intent {poll_attempt}): {poll_e}")
                        if poll_attempt < max_poll_attempts and poll_interval_seconds > 0:
                            time.sleep(poll_interval_seconds)

                if not instance_stopped:
                    if final_lifecycle_state is not None:
                        stop_warning = (
                            f"Ordre STOP acceptada per OCI, però la instància {instance_ocid} no ha arribat a l'estat "
                            f"STOPPED dins del límit d'espera (estat final detectat: '{final_lifecycle_state}')."
                        )
                    else:
                        stop_warning = (
                            f"Ordre STOP acceptada per OCI, però no s'ha pogut verificar l'estat "
                            f"STOPPED de la instància {instance_ocid} (resposta sense estat, sense dades o excepció de consulta)."
                        )
                    print(f"[Watchdog] ADVERTÈNCIA CRÍTICA: {stop_warning}")
        else:
            stop_error = f"instance_action(STOP) ha retornat codi HTTP {action_resp.status}"
            print(f"[!] ERROR: {stop_error}")
    except Exception as e:
        exception_captured = str(e)
        stop_error = f"Excepció durant la comanda STOP o connexió a OCI: {e}"
        print(f"[!] {stop_error}")

    if strict_stopped:
        overall_success = tag_applied and stop_accepted and instance_stopped
    else:
        overall_success = tag_applied and stop_accepted

    err_summary = stop_error or tag_error or exception_captured

    if return_details:
        return {
            "success": overall_success,
            "stop_accepted": stop_accepted,
            "instance_stopped": instance_stopped,
            "lifecycle_state": final_lifecycle_state,
            "tag_applied": tag_applied,
            "error": err_summary,
            "warning": stop_warning,
            "tag_error": tag_error,
            "stop_error": stop_error,
            "exception": exception_captured
        }
    return overall_success


def start_oci_instance(instance_ocid: str, config: Dict[str, Any], dry_run: bool = False) -> bool:
    """Envia una ordre d'arrencada (START) a la instància si no hi ha bloqueig econòmic."""
    print(f"[Watchdog] Sol·licitant ARRENCADA de la instància {instance_ocid}...")
    if dry_run:
        print("[Watchdog] Mode Dry-Run: ordre d'arrencada simulada.")
        return True
    if not instance_ocid or not isinstance(instance_ocid, str) or not instance_ocid.strip() or "simulated" in instance_ocid.lower():
        print("[!] ERROR CRÍTIC: Falta el paràmetre obligatori 'OCI_INSTANCE_OCID' amb un identificador real vàlid per a l'operació real (no es permeten valors buits o simulats).")
        return False
    signer = get_oci_signer(config)
    if not signer:
        ok_creds, err_creds = validate_oci_credentials(config)
        print(f"[!] ERROR CRÍTIC: No s'ha pogut obtenir el signer d'autenticació OCI ({err_creds})")
        return False
    try:
        import oci
        compute_client = oci.core.ComputeClient({"region": config.get("region", "eu-madrid-1")}, signer=signer)
        compute_client.instance_action(instance_ocid, "START")
        print("[Watchdog] Ordre START enviada amb èxit a OCI.")
        return True
    except Exception as e:
        print(f"[!] Error enviant comanda START a OCI: {e}")
        return False


def execute_administrative_recovery(
    config: Optional[Dict[str, Any]] = None,
    instance_ocid: Optional[str] = None,
    compartment_ocid: Optional[str] = None,
    actor: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    unlock_reason: Optional[str] = None,
    dry_run: bool = False,
    max_retries: int = 3,
    mock_provisioning: Optional[Dict[str, Any]] = None,
    guard: Optional[CostGuard] = None,
    compute_client: Any = None
) -> Tuple[bool, str]:
    """
    Executa el protocol complet de recuperació administrativa i desbloqueig econòmic:
    0. Valida estrictament les metadades d'auditoria (actor, unlock_reason, workflow_run_id).
    1. Valida l'inventari OCI (descobriment o mock): rebutja qualsevol recurs de pagament o anomalia.
    2. Obté les metadades del bloqueig actiu del GitHub Issue Ledger (event_id de l'últim LOCKED).
    3. Escriu l'esdeveniment criptogràfic UNLOCKED al Ledger vinculant-lo a resolves_lock_id.
    4. Actualitza la Segona Àncora a OCI (economic_lock='false', sequence, hash, event_id)
       amb control de concurrència optimista (ETag) i gestió de reintents HTTP 412.
    5. Reconcilia CostGuard per restablir l'estat operatiu a OK / can_restart=True.
    """
    # 0. Validació estricta de metadades d'auditoria (Fail-Closed)
    ok_meta, meta_err, clean_meta = validate_recovery_metadata(
        actor=actor,
        unlock_reason=unlock_reason,
        workflow_run_id=workflow_run_id
    )
    if not ok_meta:
        return False, meta_err

    if config is None:
        config = {}
    else:
        config = dict(config)

    config.setdefault("tenancy", os.environ.get("OCI_TENANCY_OCID"))
    config.setdefault("user", os.environ.get("OCI_USER_OCID"))
    config.setdefault("fingerprint", os.environ.get("OCI_FINGERPRINT"))
    config.setdefault("key_content", os.environ.get("OCI_PRIVATE_KEY"))
    config.setdefault("region", os.environ.get("OCI_REGION", "eu-madrid-1"))
    config.setdefault("compartment_ocid", compartment_ocid or os.environ.get("OCI_COMPARTMENT_OCID"))

    if guard is None:
        try:
            guard = CostGuard(dry_run=dry_run)
        except TypeError:
            guard = CostGuard()
    if dry_run and hasattr(guard, "dry_run"):
        guard.dry_run = True

    target_instance = instance_ocid or os.environ.get("OCI_INSTANCE_OCID")
    if not dry_run:
        # En operació real (dry_run=False), validació estricta d'identificador d'instància
        if not target_instance or not isinstance(target_instance, str) or not target_instance.strip() or "simulated" in target_instance.lower():
            err_msg = "Recuperació denegada: falta el paràmetre obligatori 'OCI_INSTANCE_OCID' amb un identificador real vàlid per a l'operació real (no es permeten valors simulats o buits en operació real)."
            print(f"[!] ERROR CRÍTIC: {err_msg}")
            return False, err_msg

        # En operació real (dry_run=False), validació estricta de credencials OCI.
        # Mai es permet saltar la validació per haver rebut un mock_provisioning o compute_client!
        ok_creds, err_creds = validate_oci_credentials(
            config,
            require_instance=True,
            instance_ocid=target_instance,
            require_compartment=True,
            compartment_ocid=compartment_ocid
        )
        if not ok_creds:
            print(f"[!] ERROR CRÍTIC: {err_creds}")
            return False, f"Recuperació denegada: {err_creds}"
    else:
        if not target_instance or not target_instance.strip():
            target_instance = "ocid1.instance.oc1..simulated"

    # 1. Validació de l'inventari OCI actual
    if mock_provisioning is not None:
        prov = mock_provisioning
    elif config.get("tenancy"):
        prov = discover_oci_resources(config, compartment_ocid)
    else:
        prov = guard.state.get("active_provisioning") or AUTHORIZED_BASELINE

    if prov.get("discovery_failed"):
        return False, f"Recuperació denegada: error descobrint recursos OCI ({prov.get('error_msg')})."

    est_cost, anomalies = guard.compute_estimated_cost(prov, uptime_hours=744.0)
    critical_or_paid = [
        a for a in anomalies
        if "Forma no autoritzada" in a or "NAT Gateway" in a or "IP pública reservada sense associar" in a or "Disc supera 200GB" in a
    ]
    if len(critical_or_paid) > 0 or len(prov.get("anomalies_detected", [])) > 0 or est_cost > 0.0:
        return False, "Recuperació denegada: l'inventari OCI conté recursos no autoritzats."

    # 2. Obtenir metadades de l'últim bloqueig actiu del ledger
    p_data, err = guard.persistence_backend.get_state()
    if err:
        return False, f"Recuperació denegada: error llegint el ledger persistent ({err})."

    if not p_data or p_data.get("locked") is not True:
        return False, "Recuperació denegada: el ledger no es troba en estat LOCKED actiu."

    resolves_lock_id = p_data.get("event_id")
    if not resolves_lock_id or not isinstance(resolves_lock_id, str) or not resolves_lock_id.strip():
        return False, "Recuperació denegada: l'esdeveniment LOCKED previ no té event_id vàlid."

    # Validació d'identificador d'instància i credencials OCI reals abans de modificar estat persistent
    if not dry_run and compute_client is None:
        ok_creds, err_creds = validate_oci_credentials(
            config,
            require_instance=True,
            instance_ocid=target_instance
        )
        if not ok_creds:
            print(f"[!] ERROR CRÍTIC: {err_creds}")
            return False, f"Recuperació denegada: {err_creds}"

    # 3. Escriure l'esdeveniment UNLOCKED al Ledger
    unlock_payload = {
        "locked": False,
        "resolves_lock_id": resolves_lock_id.strip(),
        "actor": clean_meta["actor"],
        "workflow_run_id": clean_meta["workflow_run_id"],
        "unlock_reason": clean_meta["unlock_reason"],
    }

    if dry_run:
        print("[Watchdog] Mode Dry-Run: escriptura d'UNLOCKED simulada al ledger.")
        new_event = {
            "sequence": (p_data.get("sequence") or 1) + 1,
            "hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "event_id": f"unlock-simulated-{resolves_lock_id.strip()}",
            "event_type": "UNLOCKED",
            "resolves_lock_id": resolves_lock_id.strip(),
            "actor": unlock_payload["actor"],
            "workflow_run_id": unlock_payload["workflow_run_id"],
            "unlock_reason": unlock_payload["unlock_reason"]
        }
    else:
        success, write_err = guard.persistence_backend.set_state(unlock_payload)
        if not success:
            return False, f"Recuperació denegada: error escrivint esdeveniment UNLOCKED al ledger ({write_err})."

        new_event = getattr(guard.persistence_backend, "last_persisted_event", None)
        if not new_event:
            latest_state, _ = guard.persistence_backend.get_state()
            if latest_state and latest_state.get("event_id"):
                new_event = latest_state
            else:
                new_event = {
                    "sequence": (p_data.get("sequence") or 1) + 1,
                    "hash": p_data.get("hash") or "0000000000000000000000000000000000000000000000000000000000000000",
                    "event_id": f"unlock-{resolves_lock_id.strip()}",
                    "event_type": "UNLOCKED"
                }

    new_seq = new_event.get("sequence")
    new_hash = new_event.get("hash")
    new_event_id = new_event.get("event_id")

    # 4. Actualització de la Segona Àncora a OCI
    if dry_run or (not config.get("tenancy") and compute_client is None):
        print("[Watchdog] Mode Dry-Run / Simulat: actualització d'àncora OCI simulada.")
        oci_anchor_payload = {
            "economic_lock": False,
            "ledger_sequence": new_seq,
            "ledger_hash": new_hash,
            "ledger_event_id": new_event_id,
            "lock_reason": None,
            "unlocked_at": datetime.now(timezone.utc).isoformat()
        }
        guard.reconcile_with_oci_anchor(oci_anchor_payload)
        return True, "Recuperació administrativa completada amb èxit."

    if compute_client is None:
        signer = get_oci_signer(config)
        if not signer:
            return False, "Recuperació denegada: no s'ha pogut obtenir el signer OCI per actualitzar l'àncora."
        import oci
        compute_client = oci.core.ComputeClient({"region": config.get("region", "eu-madrid-1")}, signer=signer)

    anchor_updated = False
    last_tag_error = None

    for attempt in range(1, max_retries + 1):
        try:
            get_resp = compute_client.get_instance(target_instance)
            current_inst = get_resp.data
            etag = get_resp.headers.get("etag") if hasattr(get_resp, "headers") and get_resp.headers else None

            if not etag or not isinstance(etag, str) or not etag.strip():
                last_tag_error = f"ETag invàlid o absent de la instància {target_instance} (ETag='{etag}')"
                print(f"[!] ERROR CRÍTIC: {last_tag_error}. Actualització d'etiquetes avortada.")
                break

            current_tags = dict(current_inst.freeform_tags or {})
            merged_tags = dict(current_tags)
            merged_tags["economic_lock"] = "false"
            merged_tags["ledger_sequence"] = str(new_seq)
            merged_tags["ledger_hash"] = str(new_hash)
            merged_tags["ledger_event_id"] = str(new_event_id)
            merged_tags["unlocked_at"] = datetime.now(timezone.utc).isoformat()
            if "lock_reason" in merged_tags:
                del merged_tags["lock_reason"]

            try:
                import oci
                update_details = oci.core.models.UpdateInstanceDetails(freeform_tags=merged_tags)
            except Exception:
                class SimpleUpdateDetails:
                    def __init__(self, freeform_tags):
                        self.freeform_tags = freeform_tags
                update_details = SimpleUpdateDetails(freeform_tags=merged_tags)

            update_resp = compute_client.update_instance(target_instance, update_details, if_match=etag.strip())

            if update_resp.status == 200:
                anchor_updated = True
                print(f"[Watchdog] Àncora OCI desbloquejada amb èxit (intent {attempt}, ETag={etag.strip()}).")
                break
            else:
                last_tag_error = f"update_instance ha retornat codi HTTP {update_resp.status}"
                print(f"[!] Advertència: {last_tag_error}")
        except Exception as e:
            is_conflict = (hasattr(e, "status") and e.status == 412) or "412" in str(e)
            if is_conflict and attempt < max_retries:
                print(f"[!] Conflicte de concurrència (HTTP 412, ETag desfasat) actualitzant àncora OCI a l'intent {attempt}/{max_retries}. Reintentant...")
                time.sleep(0.5)
                continue
            else:
                last_tag_error = f"Error actualitzant àncora persistent a OCI (intent {attempt}): {e}"
                print(f"[!] ERROR CRÍTIC: {last_tag_error}")
                break

    if not anchor_updated:
        return False, f"Recuperació denegada: error actualitzant Segona Àncora a OCI ({last_tag_error})."

    # 5. Reconciliació final de CostGuard
    oci_anchor_payload = {
        "economic_lock": False,
        "ledger_sequence": new_seq,
        "ledger_hash": new_hash,
        "ledger_event_id": new_event_id,
        "lock_reason": None,
        "unlocked_at": datetime.now(timezone.utc).isoformat()
    }
    coherent, rec_err = guard.reconcile_with_oci_anchor(oci_anchor_payload)
    if not coherent:
        return False, f"Recuperació fallida en reconciliar amb Segona Àncora: {rec_err}"

    return True, "Recuperació administrativa completada amb èxit."


def run_watchdog(
    mode: str = "fast",
    live_url: Optional[str] = None,
    instance_ocid: Optional[str] = None,
    compartment_ocid: Optional[str] = None,
    dry_run: bool = False,
    mock_official_cost: Optional[float] = None,
    mock_provisioning: Optional[Dict[str, Any]] = None,
    guard: Optional[CostGuard] = None
) -> Dict[str, Any]:
    """Execució principal del vigilant segons el mode indicat."""
    if guard is None:
        try:
            guard = CostGuard(dry_run=dry_run)
        except TypeError:
            guard = CostGuard()
    if dry_run and hasattr(guard, "dry_run"):
        guard.dry_run = True

    config = {
        "tenancy": os.environ.get("OCI_TENANCY_OCID"),
        "user": os.environ.get("OCI_USER_OCID"),
        "fingerprint": os.environ.get("OCI_FINGERPRINT"),
        "key_content": os.environ.get("OCI_PRIVATE_KEY"),
        "region": os.environ.get("OCI_REGION", "eu-madrid-1"),
        "compartment_ocid": compartment_ocid or os.environ.get("OCI_COMPARTMENT_OCID")
    }
    target_instance = instance_ocid or os.environ.get("OCI_INSTANCE_OCID")
    if not dry_run:
        ok_creds, err_creds = validate_oci_credentials(
            config,
            require_instance=True,
            instance_ocid=target_instance,
            require_compartment=(mode in ("hourly", "daily")),
            compartment_ocid=compartment_ocid
        )
        if not ok_creds:
            print(f"[!] ERROR CRÍTIC: {err_creds}", file=sys.stderr)
            return {
                "is_live": False,
                "cost_guard_summary": {},
                "can_restart": False,
                "watchdog_ok": False,
                "stop_result": None,
                "error": err_creds
            }
    else:
        if not target_instance or not target_instance.strip():
            target_instance = "ocid1.instance.oc1..simulated"
    target_url = live_url or os.environ.get("LIVE_API_URL")

    print(f"[Watchdog] Iniciant execució en mode: {mode.upper()} ({datetime.now(timezone.utc).isoformat()})")

    # 1. Comprovació d'estat de l'endpoint web (Fast)
    is_live, live_data = check_live_endpoint(target_url) if target_url else (False, None)

    # 2. Descobriment de recursos i dades de facturació
    if mock_provisioning:
        prov = mock_provisioning
    elif mode in ("hourly", "daily"):
        if dry_run and not config.get("tenancy"):
            print("[Watchdog] Mode Dry-Run: credencials OCI absents; utilitzant baseline per defecte per a simulació.")
            prov = dict(AUTHORIZED_BASELINE)
        else:
            prov = discover_oci_resources(config, compartment_ocid)
    else:
        # En mode fast
        has_oci_creds = bool(
            config.get("tenancy")
            and target_instance
            and "simulated" not in target_instance.lower()
            and get_oci_signer(config)
        )
        if has_oci_creds:
            if compartment_ocid:
                prov = discover_oci_resources(config, compartment_ocid)
            else:
                try:
                    import oci
                    signer = get_oci_signer(config)
                    region = config.get("region", "eu-madrid-1")
                    compute_client = oci.core.ComputeClient({"region": region}, signer=signer)
                    inst_resp = compute_client.get_instance(target_instance)
                    inst = getattr(inst_resp, "data", inst_resp)
                    tags = getattr(inst, "freeform_tags", {}) or {}
                    raw_seq = tags.get("ledger_sequence")
                    seq_val = None
                    if raw_seq is not None:
                        if isinstance(raw_seq, int) and not isinstance(raw_seq, bool):
                            seq_val = raw_seq
                        elif str(raw_seq).isdigit():
                            seq_val = int(raw_seq)
                        else:
                            seq_val = raw_seq

                    cfg = getattr(inst, "shape_config", None)
                    ocpus = getattr(cfg, "ocpus", 1.0) if cfg and getattr(cfg, "ocpus", None) else 1.0
                    ram_gb = getattr(cfg, "memory_in_gbs", 6.0) if cfg and getattr(cfg, "memory_in_gbs", None) else 6.0

                    prov = dict(AUTHORIZED_BASELINE)
                    prov["shape"] = getattr(inst, "shape", "VM.Standard.A1.Flex") or "VM.Standard.A1.Flex"
                    prov["ocpus"] = float(ocpus)
                    prov["ram_gb"] = float(ram_gb)
                    lifecycle = getattr(inst, "lifecycle_state", "RUNNING")
                    prov["is_running"] = lifecycle in ("RUNNING", "STARTING")
                    if "economic_lock" in tags:
                        prov["oci_anchor"] = {
                            "economic_lock": tags.get("economic_lock") == "true",
                            "ledger_sequence": seq_val,
                            "ledger_hash": tags.get("ledger_hash"),
                            "ledger_event_id": tags.get("ledger_event_id"),
                            "lock_reason": tags.get("lock_reason"),
                            "locked_at": tags.get("locked_at"),
                            "raw_tags": dict(tags)
                        }
                    if tags.get("economic_lock") == "true":
                        prov["economic_lock_tag"] = True
                        prov["lock_reason_tag"] = tags.get("lock_reason", "Bloqueig econòmic persistent detectat a tags OCI")
                    if tags.get("ledger_sequence"):
                        prov["ledger_sequence_tag"] = tags.get("ledger_sequence")
                    if tags.get("ledger_hash"):
                        prov["ledger_hash_tag"] = tags.get("ledger_hash")
                    if tags.get("ledger_event_id"):
                        prov["ledger_event_id_tag"] = tags.get("ledger_event_id")
                    print(f"[Watchdog] Consulta ràpida OCI instància: {prov['shape']} | Lifecycle={lifecycle} | oci_anchor={prov.get('oci_anchor')}")
                except Exception as e:
                    print(f"[!] Error consultant instància OCI en mode fast: {e}")
                    prov = dict(AUTHORIZED_BASELINE)
                    prov["discovery_failed"] = True
                    prov["error_msg"] = str(e)
        else:
            prov = guard.state.get("active_provisioning") or dict(AUTHORIZED_BASELINE)

    # Dades de facturació oficial
    if mock_official_cost is not None:
        official_data = {
            "amount_eur": mock_official_cost,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
    elif mode in ("hourly", "daily"):
        official_data = query_oci_usage_api(config)
    else:
        official_data = None

    # 3. Avaluació financera conjunta
    if prov.get("discovery_failed"):
        guard.set_discovery_status(False, reason=prov.get("error_msg"))

    if prov.get("economic_lock_tag"):
        already_locked = bool(
            guard.state.get("restart_locked")
            or (guard.latest_ledger_event and guard.latest_ledger_event.get("event_type") == "LOCKED")
        )
        guard.state["restart_locked"] = True
        guard.state["economic_shutdown_triggered"] = True
        guard.state["status"] = "RESTART_LOCKED"
        guard.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
        guard.state["lock_reason"] = prov.get("lock_reason_tag", "Bloqueig econòmic persistent detectat a tags OCI")
        if not dry_run and not already_locked:
            guard._persist_lock_state(locked=True, reason=guard.state["lock_reason"])
            guard.save_state()

    summary = guard.evaluate(current_provisioning=prov, uptime_hours=744.0, official_data=official_data, dry_run=dry_run)
    status = summary.get("status")
    can_restart, restart_reason = guard.can_restart()

    print(f"[Watchdog] Estat Econòmic: {status} | Can Restart: {can_restart}")
    print(f"[Watchdog] Cost Teòric Estimat: {summary.get('estimated_cost_eur')} EUR | Cost Oficial: {summary.get('official_cost_eur')} EUR")

    # 4. Accions de protecció
    stop_result: Optional[Dict[str, Any]] = None
    watchdog_ok: bool = True
    watchdog_error: Optional[str] = None

    if status in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED"):
        print(f"[Watchdog] ALERTA CRÍTICA: Estat {status}. Procedint a aturar la màquina...")
        anchor_meta = guard.get_latest_ledger_metadata()
        stop_result = stop_oci_instance(
            target_instance,
            config,
            reason=summary.get("lock_reason") or "Aturada preventiva per motius econòmics",
            dry_run=dry_run,
            anchor_metadata=anchor_meta,
            return_details=True
        )

        if not isinstance(stop_result, dict):
            if stop_result is True:
                stop_result = {
                    "stop_accepted": True,
                    "tag_applied": True,
                    "instance_stopped": True,
                    "is_dry_run": False,
                    "simulation_success": False,
                    "success": True,
                    "error": None
                }
            elif dry_run:
                stop_result = {
                    "stop_accepted": False,
                    "tag_applied": False,
                    "instance_stopped": False,
                    "is_dry_run": True,
                    "simulation_success": True,
                    "success": False,
                    "error": None,
                    "warning": "Mode Dry-Run: comanda d'aturada simulada."
                }
            else:
                stop_result = {
                    "stop_accepted": False,
                    "tag_applied": False,
                    "instance_stopped": False,
                    "is_dry_run": False,
                    "simulation_success": False,
                    "success": False,
                    "error": "stop_oci_instance ha retornat False"
                }

        if stop_result.get("is_dry_run"):
            # En mode Dry-Run, la simulació s'ha completat sense executar canvis físics a OCI
            print(f"[Watchdog] Mode Dry-Run: comanda d'aturada simulada (simulation_success={stop_result.get('simulation_success', False)}).")
            if not stop_result.get("simulation_success", False):
                watchdog_ok = False
                watchdog_error = f"Fallada en la simulació Dry-Run: {stop_result.get('error')}"
        elif not stop_result.get("stop_accepted", False):
            watchdog_ok = False
            watchdog_error = f"L'ordre STOP ha fallat o ha estat rebutjada per OCI: {stop_result.get('stop_error') or stop_result.get('error')}"
            print(f"[!] ERROR CRÍTIC WORKFLOW: {watchdog_error}")
        elif not stop_result.get("tag_applied", False):
            # STOP acceptat per OCI (asíncron), però fallada en l'etiquetatge persistent de la Segona Àncora
            watchdog_ok = False
            watchdog_error = (
                f"L'ordre STOP ha estat acceptada per OCI (transició asíncrona iniciada), "
                f"però la persistència de l'àncora a OCI ha fallat: {stop_result.get('tag_error') or stop_result.get('error')}"
            )
            print(f"[!] ALERTA CRÍTICA WORKFLOW: {watchdog_error}")
        elif not stop_result.get("instance_stopped", False):
            watchdog_ok = False
            watchdog_error = (
                f"TRANSICIÓ D'ATURADA NO COMPLETADA: l'ordre STOP ha estat acceptada i l'àncora persistent ha estat aplicada, "
                f"però l'aturada física NO s'ha confirmat (estat detectat: '{stop_result.get('lifecycle_state')}'): "
                f"{stop_result.get('warning')}"
            )
            print(f"[Watchdog] ALERTA CRÍTICA: {watchdog_error}")
        else:
            print("[Watchdog] Aturada STOP, confirmació d'estat STOPPED i actualització de l'àncora completades amb èxit.")
    elif not is_live:
        # Criteri de seguretat obligatori: un timeout HTTP NO és prova que la VM estigui apagada
        # ni justifica una arrencada automàtica a cegues.
        is_vm_running = prov.get("is_running", True)
        if is_vm_running:
            print("[Watchdog] L'endpoint públic no respon però la VM està RUNNING a OCI. "
                  "Degradació d'aplicació o túnel; NO s'envia cap ordre d'arrencada.")
        else:
            # La VM està STOPPED a OCI
            if not can_restart:
                print(f"[Watchdog] La VM està STOPPED i el reinici està bloquejat ({restart_reason}). REINICI REBUTJAT.")
            else:
                # La VM està STOPPED sense bloqueig econòmic, però se'n desconeix la causa (aturada manual, Oracle, etc.)
                print("[Watchdog] La VM està STOPPED a OCI per causa desconeguda. "
                      "Regla de seguretat conservadora: NO s'executa arrencada automàtica a cegues. "
                      "Requereix revisió o autorització de l'administrador.")

    # 5. Resum per a GitHub Step Summary
    summary_md = f"""
### 🛡️ OCI Cost Guard Watchdog ({mode.upper()})
- **Data:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`
- **Estat del Servei:** `{summary.get('service_state')}` (`{status}`)
- **Endpoint en Viu:** `{'🟢 Responent' if is_live else '🔴 Inaccessible'}`
- **Cost Estimat Teòric:** `{summary.get('estimated_cost_eur')} €`
- **Cost Oficial Usage API:** `{str(summary.get('official_cost_eur')) + ' €' if summary.get('official_cost_eur') is not None else 'Pendent de consolidació'}`
- **Reinici Permès:** `{'SÍ' if can_restart else 'BLOQUEJAT'}`
- **Anomalies / Recursos Inesperats:** `{len(summary.get('unexpected_resources', []))}`
"""
    if not watchdog_ok:
        summary_md += f"\n> [!CAUTION]\n> **FALLADA DEL WATCHDOG:** {watchdog_error}\n"
    elif stop_result and stop_result.get("is_dry_run"):
        summary_md += f"\n> [!NOTE]\n> **MODE DRY-RUN:** Simulació d'aturada completada amb èxit (cap ordre física enviada a OCI).\n"
    elif stop_result and stop_result.get("warning"):
        summary_md += f"\n> [!WARNING]\n> **TRANSICIÓ D'ATURADA EN CURS:** {stop_result.get('warning')}\n"

    step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_file:
        try:
            with open(step_summary_file, "a", encoding="utf-8") as f:
                f.write(summary_md)
        except Exception:
            pass

    return {
        "is_live": is_live,
        "cost_guard_summary": summary,
        "can_restart": can_restart,
        "watchdog_ok": watchdog_ok,
        "stop_result": stop_result,
        "error": watchdog_error
    }


def main():
    parser = argparse.ArgumentParser(description="OCI External Cost Watchdog")
    parser.add_argument("--action", choices=["watchdog", "recover"], default="watchdog", help="Acció a realitzar (watchdog o recover)")
    parser.add_argument("--recover", action="action_true" if hasattr(argparse, "action_true") else "store_true", help="Sinònim per executar l'acció de recuperació")
    parser.add_argument("--mode", choices=["fast", "hourly", "daily"], default="fast", help="Mode d'execució del watchdog")
    parser.add_argument("--url", type=str, default=None, help="URL de l'endpoint públic")
    parser.add_argument("--instance-ocid", type=str, default=None, help="OCID de la instància OCI")
    parser.add_argument("--compartment-ocid", type=str, default=None, help="OCID del compartiment OCI")
    parser.add_argument("--dry-run", action="action_true" if hasattr(argparse, "action_true") else "store_true", help="No executa accions reals a OCI")
    parser.add_argument("--mock-cost", type=float, default=None, help="Simula import oficial en euros")
    parser.add_argument("--actor", type=str, default=None, help="Actor que autoritza la recuperació administrativa")
    parser.add_argument("--workflow-run-id", type=str, default=None, help="Identificador del workflow run per a la recuperació")
    parser.add_argument("--unlock-reason", type=str, default=None, help="Motiu de la recuperació administrativa")
    args = parser.parse_args()

    if not args.dry_run:
        cfg_check = {
            "tenancy": os.environ.get("OCI_TENANCY_OCID"),
            "user": os.environ.get("OCI_USER_OCID"),
            "fingerprint": os.environ.get("OCI_FINGERPRINT"),
            "key_content": os.environ.get("OCI_PRIVATE_KEY"),
            "region": os.environ.get("OCI_REGION", "eu-madrid-1"),
            "compartment_ocid": args.compartment_ocid or os.environ.get("OCI_COMPARTMENT_OCID")
        }
        inst_check = args.instance_ocid or os.environ.get("OCI_INSTANCE_OCID")
        is_rec = (args.action == "recover" or args.recover)
        require_comp = is_rec or (args.mode in ("hourly", "daily"))
        ok_cfg, err_cfg = validate_oci_credentials(
            cfg_check,
            require_instance=True,
            instance_ocid=inst_check,
            require_compartment=require_comp,
            compartment_ocid=cfg_check["compartment_ocid"]
        )
        if not ok_cfg:
            print(f"[!] FATAL CONFIGURATION ERROR: {err_cfg}", file=sys.stderr)
            sys.exit(1)

    if args.action == "recover" or args.recover:
        print(f"[Recovery] Iniciant procediment de recuperació administrativa...")
        ok, msg = execute_administrative_recovery(
            instance_ocid=args.instance_ocid,
            compartment_ocid=args.compartment_ocid,
            actor=args.actor,
            workflow_run_id=args.workflow_run_id,
            unlock_reason=args.unlock_reason,
            dry_run=args.dry_run
        )
        print(f"[Recovery] Resultat: {'ÈXIT' if ok else 'FALLADA'} - {msg}")
        if not ok:
            sys.exit(1)
        sys.exit(0)

    res = run_watchdog(
        mode=args.mode,
        live_url=args.url,
        instance_ocid=args.instance_ocid,
        compartment_ocid=args.compartment_ocid,
        dry_run=args.dry_run,
        mock_official_cost=args.mock_cost
    )

    if not res.get("watchdog_ok", True):
        err = res.get("error") or "Fallada crítica en l'execució del vigilant extern OCI."
        print(f"[!] FATAL WATCHDOG FAILURE: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

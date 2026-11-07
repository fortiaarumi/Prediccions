"""
engine/cost_guard.py
====================
Mòdul de vigilància de costos, protecció econòmica i control d'aprovisionament
per a la infraestructura Oracle Cloud Infrastructure (OCI) Always Free / PAYG.

Principis arquitectònics:
1. Cost Zero com a estat normal: La configuració Always Free autoritzada ha de computar 0.00 €.
2. Detecció proactiva: No confia exclusivament en l'API d'ús (que té retard de 24-48h);
   calcula en temps real el cost teòric dels recursos provisionats segons uptime i tarifes oficials.
3. Doble font: Combina el càlcul teòric local amb les dades consolidades de l'OCI Usage API.
4. Llindars escalonats de protecció:
   - 0.01 €: NOTIFICACIÓ (activitat econòmica detectada)
   - 0.10 €: ALERTA (despesa inesperada)
   - 0.25 €: ATURADA PREVENTIVA (ordre de STOP a la instància)
   - 0.50 €: BLOQUEIG DE REINICI (impedeix qualsevol reinici automàtic)
   - 1.00 €: SOSTRE MÀXIM DESITJAT (límit no contractual)
5. Privadesa i desacoblament: Les dades financeres es guarden a data/cost_guard_state.json;
   només s'exporta un estat públic sanititzat sense dades sensibles per a la web.
"""

import os
import sys
import re
import json
import time
import hashlib
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

STATE_FILE = Path(__file__).parent.parent / "data" / "cost_guard_state.json"
DEFAULT_PERSISTENT_LOCK_FILE = Path(__file__).parent.parent / "data" / ".cost_guard_state_persistent_lock.json"


class LockPersistenceBackend:
    """Interfície base per a la persistència externa del bloqueig econòmic."""

    def get_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Retorna (state_dict, error_code).
        Èxit: (dict, None)
        Font no existent: (None, "ABSENT")
        Error de lectura/xarxa: (None, "READ_ERROR: ...")
        Dades malformades/invàlides: (None, "MALFORMED: ...")
        """
        raise NotImplementedError

    def set_state(self, state: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Escriu l'estat persistent.
        Retorna (success, error_msg).
        """
        raise NotImplementedError


class FileLockPersistenceBackend(LockPersistenceBackend):
    """Persistència en fitxer durable amb validació estricta i escriptura atòmica."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)

    def get_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not self.file_path.exists():
            return None, "ABSENT"
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return None, f"MALFORMED: Fitxer JSON no analitzable ({e})"
        except Exception as e:
            return None, f"READ_ERROR: Error d'E/S llegint fitxer persistent ({e})"

        if not isinstance(data, dict):
            return None, "MALFORMED: El contingut persistent ha de ser un diccionari JSON"
        if "locked" not in data or not isinstance(data["locked"], bool):
            return None, "MALFORMED: El camp obligatori 'locked' (bool) és absent o invàlid"

        return data, None

    def set_state(self, state: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.file_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            tmp_path.replace(self.file_path)
            return True, None
        except Exception as e:
            return False, f"Error escrivint fitxer de bloqueig persistent: {e}"


class GitHubVariablePersistenceBackend(LockPersistenceBackend):
    """
    [DEPRECAT - FASE 1] Persistència anterior basada en GitHub Actions Variables.
    Substituïda funcionalment per GitHubIssueLedgerPersistenceBackend.
    Es manté exclusivament per compatibilitat d'imports històrics.
    """

    def __init__(
        self,
        repository: str,
        token: str,
        variable_name: str = "ECONOMIC_LOCK_STATE",
        api_url: str = "https://api.github.com"
    ):
        self.repository = repository
        self.token = token
        self.variable_name = variable_name
        self.api_url = api_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PreddicionsLliga-CostGuard/1.0"
        }

    def get_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not self.repository or not self.token:
            return None, "READ_ERROR: Credencials GitHub (repository o token) absents"

        url = f"{self.api_url}/repos/{self.repository}/actions/variables/{self.variable_name}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read().decode("utf-8"))
                    val_str = body.get("value", "")
                    try:
                        data = json.loads(val_str)
                    except json.JSONDecodeError as e:
                        return None, f"MALFORMED: Valor de la variable no és JSON vàlid ({e})"
                    if not isinstance(data, dict) or "locked" not in data or not isinstance(data["locked"], bool):
                        return None, "MALFORMED: Esquema de variable invàlid (manca 'locked' booleà)"
                    return data, None
                else:
                    return None, f"READ_ERROR: Resposta HTTP inesperada ({resp.status})"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "ABSENT"
            return None, f"READ_ERROR: Error HTTP {e.code}"
        except Exception as e:
            return None, f"READ_ERROR: Error de connexió ({e})"

    def set_state(self, state: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if not self.repository or not self.token:
            return False, "Credencials GitHub no configurades"

        val_str = json.dumps(state, ensure_ascii=False)
        payload = {"name": self.variable_name, "value": val_str}
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        # 1. Intentar actualitzar la variable existent via PATCH
        patch_url = f"{self.api_url}/repos/{self.repository}/actions/variables/{self.variable_name}"
        req_patch = urllib.request.Request(patch_url, data=data_bytes, headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(req_patch, timeout=10) as resp:
                if resp.status in (200, 204):
                    return True, None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 2. Variable inexistent: crear-la via POST
                post_url = f"{self.api_url}/repos/{self.repository}/actions/variables"
                req_post = urllib.request.Request(post_url, data=data_bytes, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req_post, timeout=10) as resp_post:
                        if resp_post.status in (201, 204):
                            return True, None
                        return False, f"Error HTTP {resp_post.status} creant variable GitHub"
                except Exception as post_err:
                    return False, f"Excepció creant variable GitHub: {post_err}"
            return False, f"Error HTTP {e.code} actualitzant variable GitHub"
        except Exception as e:
            return False, f"Excepció actualitzant variable GitHub: {e}"


class GitHubIssueLedgerPersistenceBackend(LockPersistenceBackend):
    """
    Persistència del bloqueig econòmic mitjançant un GitHub Issue com a ledger auditable
    utilitzant el GITHUB_TOKEN nadiu (permisos issues: read / issues: write).

    Principis de disseny, garanties i limitacions conegudes:
    1. Cadena d'integritat criptogràfica: Cada esdeveniment de bloqueig conté un hash SHA-256
       canònic del seu contingut i un enllaç al hash de l'esdeveniment anterior (prev_hash).
       El primer esdeveniment utilitza prev_hash="GENESIS".
    2. Detecció de manipulacions i immutabilitat no nativa: Atès que l'API de GitHub permet
       que usuaris o processos editin o esborrin comentaris, el backend verifica:
       - Que cada comentari sigui un diccionari amb camp 'body' tipus str.
       - Que created_at i updated_at siguin presents, vàlids i idèntics (created_at == updated_at).
       - Que el hash del contingut coincideixi exactament amb el hash registrat.
       - Que cap identificador d'esdeveniment (event_id) estigui duplicat.
       - Que 'sequence' sigui un enter positiu (>0) estrictament creixent.
       - Que 'schema_version' sigui admesa (ex: '1.0').
       - Que no s'hagi eliminat cap comentari intermedi (trencament de prev_hash).
    3. Absència de CAS / Transaccionalitat atòmica: GitHub Issues no disposa d'operacions
       Compare-And-Swap (CAS) ni de locks atòmics. Dues escriptures simultànies generen una
       bifurcació (fork). El sistema NO assumeix garanties CAS inexistents; davant qualsevol
       bifurcació o discordança, aplica estrictament Fail-Closed i denega el reinici.
    4. Limitació de la truncació de cua: Una cadena encadenada cap enrere (prev_hash) no pot
       detectar per si sola l'eliminació del darrer element de la cua. La Fase 2 haurà d'incorporar
       una segona àncora (p. ex. seqüència creixent a etiquetes OCI) per detectar la truncació.
    5. Verificació Read-After-Write: Després de publicar un comentari (POST), es fa una lectura
       immediata (GET) del comentari creat per verificar-ne la persistència, integritat i hash.
    6. Paginació robusta: Llegeix l'historial complet recorrent 'Link' (o incrementant pàgina).
       Si s'arriba a max_pages sense esgotar l'historial, avorta amb error (fail-closed) en comptes
       d'acceptar dades truncades o parcials.
    7. Fase 1: Cap esdeveniment UNLOCKED pot habilitar un desbloqueig; només es processen i
       registren esdeveniments LOCKED.
    """

    SUPPORTED_SCHEMA_VERSIONS = {"1.0"}

    def __init__(
        self,
        repository: str,
        token: str,
        issue_number: int,
        api_url: str = "https://api.github.com"
    ):
        self.repository = repository
        self.token = token
        self.issue_number = int(issue_number) if issue_number is not None else 0
        self.api_url = api_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PreddicionsLliga-CostGuard/1.0"
        }

    @staticmethod
    def _compute_event_hash(payload: Dict[str, Any]) -> str:
        """Calcula el hash SHA-256 canònic d'un esdeveniment excloent el camp 'hash'."""
        data_to_hash = {k: v for k, v in payload.items() if k != "hash"}
        canonical_json = json.dumps(data_to_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _fetch_issue(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Consulta l'estat de l'Issue dedicat."""
        url = f"{self.api_url}/repos/{self.repository}/issues/{self.issue_number}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if not isinstance(data, dict):
                        return None, "MALFORMED: Resposta de l'Issue no és un diccionari"
                    return data, None
                return None, f"READ_ERROR: Resposta HTTP inesperada ({resp.status})"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "ABSENT"
            return None, f"READ_ERROR: Error HTTP {e.code} consultant Issue"
        except Exception as e:
            return None, f"READ_ERROR: Error de connexió consultant Issue ({e})"

    @staticmethod
    def _extract_next_url(link_header: Optional[str]) -> Optional[str]:
        """Extreu la URL amb rel='next' de la capçalera Link de GitHub."""
        if not link_header:
            return None
        parts = link_header.split(",")
        for part in parts:
            section = part.strip().split(";")
            if len(section) >= 2:
                url_part = section[0].strip()
                rel_part = section[1].strip()
                if 'rel="next"' in rel_part:
                    if url_part.startswith("<") and url_part.endswith(">"):
                        return url_part[1:-1]
        return None

    def _fetch_all_comments(self) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Llegeix recursivament tots els comentaris de l'Issue gestionant paginació."""
        all_comments: List[Dict[str, Any]] = []
        next_url: Optional[str] = f"{self.api_url}/repos/{self.repository}/issues/{self.issue_number}/comments?per_page=100&page=1"
        page = 1
        max_pages = 100

        while next_url:
            if page > max_pages:
                return None, f"READ_ERROR: S'ha superat el límit màxim de paginació ({max_pages} pàgines) sense esgotar l'historial"

            req = urllib.request.Request(next_url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status != 200:
                        return None, f"READ_ERROR: Error HTTP {resp.status} obtenint comentaris"
                    body = resp.read().decode("utf-8")
                    try:
                        page_comments = json.loads(body)
                    except Exception as e:
                        return None, f"MALFORMED: JSON de comentaris invàlid ({e})"
                    if not isinstance(page_comments, list):
                        return None, "MALFORMED: La resposta de comentaris no és una llista"

                    all_comments.extend(page_comments)

                    link_header = resp.headers.get("Link") or resp.headers.get("link")
                    extracted_next = self._extract_next_url(link_header)

                    if extracted_next:
                        next_url = extracted_next
                        page += 1
                    elif len(page_comments) == 100:
                        page += 1
                        next_url = f"{self.api_url}/repos/{self.repository}/issues/{self.issue_number}/comments?per_page=100&page={page}"
                    else:
                        break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None, "ABSENT"
                return None, f"READ_ERROR: Error HTTP {e.code} obtenint comentaris"
            except Exception as e:
                return None, f"READ_ERROR: Error de connexió obtenint comentaris ({e})"

        return all_comments, None

    def _parse_comment_event(self, comment: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Extreu i valida estrictament el payload d'un esdeveniment a partir d'un comentari GitHub.
        Retorna (event_dict, error_str). Si no és un comentari d'esdeveniment, retorna (None, None).
        Mai llança excepcions no controlades: qualsevol anomalia genera (None, 'MALFORMED: ...').
        """
        try:
            # 1. Validar que el comentari és un diccionari
            if not isinstance(comment, dict):
                return None, "MALFORMED: El comentari ha de ser un diccionari JSON"

            comment_id = comment.get("id")

            # 2. Validar que body és una cadena de text
            body = comment.get("body")
            if not isinstance(body, str):
                return None, f"MALFORMED: El camp 'body' del comentari #{comment_id} ha de ser una cadena de text"

            if "<!-- COST_GUARD_EVENT -->" not in body:
                return None, None

            # 3. Validació de timestamps created_at i updated_at
            created_at = comment.get("created_at")
            updated_at = comment.get("updated_at")
            if not isinstance(created_at, str) or not created_at.strip():
                return None, f"MALFORMED: Camp 'created_at' absent o buit al comentari #{comment_id}"
            if not isinstance(updated_at, str) or not updated_at.strip():
                return None, f"MALFORMED: Camp 'updated_at' absent o buit al comentari #{comment_id}"

            if CostGuard._parse_dt(created_at) is None:
                return None, f"MALFORMED: Format de timestamp 'created_at' invàlid al comentari #{comment_id}"
            if CostGuard._parse_dt(updated_at) is None:
                return None, f"MALFORMED: Format de timestamp 'updated_at' invàlid al comentari #{comment_id}"

            # Detecció de modificacions a GitHub (created_at != updated_at)
            if created_at != updated_at:
                return None, f"MALFORMED: Comentari #{comment_id} ha estat modificat a GitHub (created_at={created_at} != updated_at={updated_at})"

            # 4. Extracció del bloc JSON
            marker_idx = body.find("<!-- COST_GUARD_EVENT -->")
            payload_str = body[marker_idx + len("<!-- COST_GUARD_EVENT -->"):].strip()
            if payload_str.startswith("```json"):
                payload_str = payload_str[len("```json"):].strip()
            elif payload_str.startswith("```"):
                payload_str = payload_str[len("```"):].strip()
            if payload_str.endswith("```"):
                payload_str = payload_str[:-3].strip()

            try:
                event_data = json.loads(payload_str)
            except Exception as e:
                return None, f"MALFORMED: Payload JSON no vàlid al comentari #{comment_id} ({e})"

            if not isinstance(event_data, dict):
                return None, f"MALFORMED: L'esdeveniment al comentari #{comment_id} ha de ser un diccionari JSON"

            # 5. Validació de camps obligatoris
            required_fields = ["schema_version", "event_type", "event_id", "sequence", "timestamp_utc", "hash", "prev_hash"]
            for rf in required_fields:
                if rf not in event_data:
                    return None, f"MALFORMED: Camp obligatori '{rf}' absent al comentari #{comment_id}"

            # 6. Validació de schema_version
            schema_ver = event_data.get("schema_version")
            if not isinstance(schema_ver, str) or schema_ver not in self.SUPPORTED_SCHEMA_VERSIONS:
                return None, f"MALFORMED: Versió d'esquema '{schema_ver}' no admesa al comentari #{comment_id}"

            # 7. Validació de event_id
            event_id = event_data.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                return None, f"MALFORMED: Camp 'event_id' invàlid o buit al comentari #{comment_id}"

            # 8. Validació de event_type
            event_type = event_data.get("event_type")
            if not isinstance(event_type, str):
                return None, f"MALFORMED: Camp 'event_type' invàlid al comentari #{comment_id}"
            if event_type not in ("LOCKED", "UNLOCKED"):
                return None, f"MALFORMED: Tipus d'esdeveniment desconegut '{event_type}' al comentari #{comment_id}"

            if event_type == "UNLOCKED":
                audit_fields = ["resolves_lock_id", "actor", "workflow_run_id", "unlock_reason"]
                for af in audit_fields:
                    val = event_data.get(af)
                    if not isinstance(val, str) or not val.strip():
                        return None, f"MALFORMED: Camp obligatori d'auditoria '{af}' absent o invàlid per a UNLOCKED al comentari #{comment_id}"

            # 9. Validació de sequence (enter positiu)
            sequence = event_data.get("sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
                return None, f"MALFORMED: Camp 'sequence' ha de ser un enter positiu (>0) al comentari #{comment_id}"

            # 10. Validació de timestamp_utc
            ts_utc = event_data.get("timestamp_utc")
            if not isinstance(ts_utc, str) or CostGuard._parse_dt(ts_utc) is None:
                return None, f"MALFORMED: Camp 'timestamp_utc' invàlid o no analitzable al comentari #{comment_id}"

            # 11. Validació de prev_hash format
            prev_hash = event_data.get("prev_hash")
            if not isinstance(prev_hash, str) or not prev_hash.strip():
                return None, f"MALFORMED: Camp 'prev_hash' absent o buit al comentari #{comment_id}"
            if prev_hash != "GENESIS":
                if len(prev_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in prev_hash):
                    return None, f"MALFORMED: Format de 'prev_hash' invàlid al comentari #{comment_id}"

            # 12. Validació de hash format
            event_hash = event_data.get("hash")
            if not isinstance(event_hash, str) or len(event_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in event_hash):
                return None, f"MALFORMED: Format de 'hash' invàlid al comentari #{comment_id}"

            # 13. Verificació d'integritat del hash canònic
            expected_hash = self._compute_event_hash(event_data)
            if event_hash.lower() != expected_hash.lower():
                return None, f"MALFORMED: Hash invàlid al comentari #{comment_id} (esperat {expected_hash}, obtingut {event_hash})"

            return event_data, None
        except Exception as e:
            return None, f"MALFORMED: Excepció inesperada analitzant comentari: {e}"

    def _validate_ledger(self, comments: List[Any]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        Valida la seqüència completa del ledger:
        - Detecta comentaris malformats o no diccionaris.
        - Detecta identificadors duplicats (event_id).
        - Verifica que sequence sigui estrictament creixent.
        - Verifica la continuïtat de la cadena de hashes (prev_hash -> hash).
        - Detecta esborrats de comentaris intermedis.
        """
        try:
            if not isinstance(comments, list):
                return None, "MALFORMED: La llista de comentaris no és vàlida"

            seen_ids = set()
            validated_events: List[Dict[str, Any]] = []
            expected_prev_hash: Optional[str] = None
            last_sequence: Optional[int] = None

            for comment in comments:
                event, err = self._parse_comment_event(comment)
                if err:
                    return None, err
                if event is None:
                    continue

                event_id = event["event_id"]
                if event_id in seen_ids:
                    return None, f"MALFORMED: Identificador duplicat detectat: '{event_id}'"
                seen_ids.add(event_id)

                if expected_prev_hash is None:
                    if event.get("prev_hash") != "GENESIS":
                        return None, f"MALFORMED: El primer esdeveniment ha de tenir prev_hash='GENESIS', obtingut '{event.get('prev_hash')}'"
                else:
                    if event.get("prev_hash") != expected_prev_hash:
                        return None, f"MALFORMED: Cadena de hash trencada al comentari #{comment.get('id')} (esperat {expected_prev_hash}, obtingut {event.get('prev_hash')})"

                expected_prev_hash = event.get("hash")

                sequence = event["sequence"]
                if last_sequence is None:
                    if sequence != 1:
                        return None, f"MALFORMED: El primer esdeveniment ha de tenir sequence=1, obtingut {sequence}"
                    if event.get("event_type") == "UNLOCKED":
                        return None, "MALFORMED: El primer esdeveniment del ledger (sequence=1) no pot ser UNLOCKED"
                    last_sequence = 1
                else:
                    expected_sequence = last_sequence + 1
                    if sequence != expected_sequence:
                        return None, f"MALFORMED: 'sequence' ha de ser estrictament consecutiva (esperat {expected_sequence}, obtingut {sequence})"
                    last_sequence = sequence

                if event.get("event_type") == "UNLOCKED":
                    if not validated_events or validated_events[-1].get("event_type") != "LOCKED":
                        return None, f"MALFORMED: Esdeveniment UNLOCKED invàlid a sequence={sequence}: l'estat previ no és LOCKED"
                    prev_event = validated_events[-1]
                    if event.get("resolves_lock_id") != prev_event.get("event_id"):
                        return None, f"MALFORMED: 'resolves_lock_id' ('{event.get('resolves_lock_id')}') no coincideix amb l'event_id del bloqueig actiu ('{prev_event.get('event_id')}')"

                validated_events.append(event)

            return validated_events, None
        except Exception as e:
            return None, f"MALFORMED: Excepció no controlada validant ledger: {e}"

    def get_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Llegeix l'estat persistent des del ledger d'esdeveniments de l'Issue.
        Aplica fail-closed davant qualsevol anomalia.
        """
        if not self.repository or not self.token or self.issue_number <= 0:
            return None, "READ_ERROR: Credencials o configuració GitHub (repository, token o issue_number) incompletes"

        # 1. Comprovar que l'Issue existeix i està obert
        issue_data, err = self._fetch_issue()
        if err:
            return None, err
        if issue_data.get("state") != "open":
            return None, f"MALFORMED: L'Issue #{self.issue_number} està tancat (estat '{issue_data.get('state')}', no operatiu)"

        # 2. Obtenir i validar tots els comentaris
        comments, err = self._fetch_all_comments()
        if err:
            return None, err

        # 3. Validar integritat del ledger
        events, err = self._validate_ledger(comments)
        if err:
            return None, err

        if not events:
            return None, "ABSENT"

        latest_event = events[-1]
        self.last_persisted_event = latest_event
        is_locked = (latest_event.get("event_type") == "LOCKED")
        return {
            "locked": is_locked,
            "lock_reason": latest_event.get("lock_reason", "Bloqueig econòmic persistent actiu") if is_locked else None,
            "unlock_reason": latest_event.get("unlock_reason") if not is_locked else None,
            "resolves_lock_id": latest_event.get("resolves_lock_id") if not is_locked else None,
            "actor": latest_event.get("actor") if not is_locked else None,
            "workflow_run_id": latest_event.get("workflow_run_id") if not is_locked else None,
            "event_type": latest_event.get("event_type"),
            "event_id": latest_event.get("event_id"),
            "sequence": latest_event.get("sequence"),
            "hash": latest_event.get("hash"),
            "timestamp_utc": latest_event.get("timestamp_utc"),
            "updated_at_utc": latest_event.get("timestamp_utc"),
            "authorized_manual_override": False,
            "events": events,
        }, None

    def _verify_read_after_write(
        self,
        comment_id: int,
        expected_event: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Verifica immediatament el comentari acabat de crear per garantir la persistència efectiva."""
        url = f"{self.api_url}/repos/{self.repository}/issues/comments/{comment_id}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status} consultant comentari verificat"
                comment_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return False, f"Error de xarxa consultant comentari per a verificació: {e}"

        if not isinstance(comment_data, dict):
            return False, "Dades del comentari retornades no són un diccionari"

        parsed_event, err = self._parse_comment_event(comment_data)
        if err:
            return False, f"Comentari acabat d'escriure no supera la validació: {err}"
        if parsed_event is None:
            return False, "El comentari no conté el marcador d'esdeveniment"

        if parsed_event.get("event_id") != expected_event.get("event_id"):
            return False, f"ID d'esdeveniment no coincideix (esperat {expected_event.get('event_id')}, obtingut {parsed_event.get('event_id')})"

        if parsed_event.get("hash") != expected_event.get("hash"):
            return False, f"Hash no coincideix (esperat {expected_event.get('hash')}, obtingut {parsed_event.get('hash')})"

        return True, None

    def set_state(self, state: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Afegeix un nou esdeveniment (LOCKED o UNLOCKED) al ledger de l'Issue dedicat.
        Aplica validació estricta de metadades d'auditoria i invariants de transició.
        """
        if not self.repository or not self.token or self.issue_number <= 0:
            return False, "Credencials o configuració GitHub (repository, token o issue_number) incompletes"

        is_locked_request = state.get("locked") is True

        if not is_locked_request:
            audit_fields = ["resolves_lock_id", "actor", "workflow_run_id", "unlock_reason"]
            for af in audit_fields:
                val = state.get(af)
                if not isinstance(val, str) or not val.strip():
                    return False, "Escriptura d'UNLOCKED rebutjada: falten metadades d'auditoria administrativa obligatòries (resolves_lock_id, actor, workflow_run_id, unlock_reason)"

        # 1. Comprovar l'estat de l'Issue
        issue_data, err = self._fetch_issue()
        if err:
            return False, f"No es pot escriure: error consultant Issue ({err})"
        if issue_data.get("state") != "open":
            return False, f"No es pot escriure: l'Issue #{self.issue_number} està tancat"

        # 2. Obtenir comentaris previs per verificar la cadena i determinar prev_hash
        comments, err = self._fetch_all_comments()
        if err and err != "ABSENT":
            return False, f"No es pot escriure: error llegint historial ({err})"

        events: List[Dict[str, Any]] = []
        if comments:
            parsed_events, val_err = self._validate_ledger(comments)
            if val_err:
                return False, f"No es pot escriure: ledger previ invàlid o manipulat ({val_err})"
            events = parsed_events or []

        now_iso = datetime.now(timezone.utc).isoformat()

        # 3. Construir el nou esdeveniment
        if not is_locked_request:
            if not events or events[-1].get("event_type") != "LOCKED":
                return False, "No es pot escriure UNLOCKED: el ledger no es troba en estat LOCKED actiu"

            last_lock = events[-1]
            if state["resolves_lock_id"].strip() != last_lock["event_id"]:
                return False, f"No es pot escriure UNLOCKED: resolves_lock_id ('{state['resolves_lock_id']}') no coincideix amb l'últim bloqueig del ledger ('{last_lock['event_id']}')"

            new_event = {
                "schema_version": "1.0",
                "event_id": state.get("event_id") or f"unlock-{uuid.uuid4().hex[:12]}",
                "event_type": "UNLOCKED",
                "sequence": last_lock["sequence"] + 1,
                "timestamp_utc": now_iso,
                "resolves_lock_id": state["resolves_lock_id"].strip(),
                "actor": state["actor"].strip(),
                "workflow_run_id": state["workflow_run_id"].strip(),
                "unlock_reason": state["unlock_reason"].strip(),
                "prev_hash": last_lock["hash"],
            }
            new_event["hash"] = self._compute_event_hash(new_event)
        else:
            prev_hash = "GENESIS"
            sequence = 1
            if events:
                prev_hash = events[-1]["hash"]
                sequence = events[-1]["sequence"] + 1

            event_id = state.get("event_id") or f"lock-{uuid.uuid4().hex[:12]}"
            reason = state.get("lock_reason") or "Bloqueig econòmic persistent actiu"
            new_event = {
                "schema_version": "1.0",
                "event_id": event_id,
                "event_type": "LOCKED",
                "sequence": sequence,
                "timestamp_utc": now_iso,
                "lock_reason": reason,
                "prev_hash": prev_hash,
            }
            new_event["hash"] = self._compute_event_hash(new_event)

        # 4. Format del comentari a GitHub
        comment_body = (
            f"<!-- COST_GUARD_EVENT -->\n"
            f"```json\n"
            f"{json.dumps(new_event, indent=2, ensure_ascii=False)}\n"
            f"```\n"
        )

        # 5. Publicar el comentari via POST
        post_url = f"{self.api_url}/repos/{self.repository}/issues/{self.issue_number}/comments"
        payload_bytes = json.dumps({"body": comment_body}).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req_post = urllib.request.Request(post_url, data=payload_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req_post, timeout=10) as resp_post:
                if resp_post.status != 201:
                    return False, f"Error HTTP {resp_post.status} publicant comentari"
                created_data = json.loads(resp_post.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return False, f"Error HTTP {e.code} publicant comentari: {e}"
        except Exception as e:
            return False, f"Excepció publicant comentari: {e}"

        comment_id = created_data.get("id")
        if not comment_id:
            return False, "Error de verificació: resposta de creació sense 'id' de comentari"

        # 6. Verificació Read-After-Write
        raw_ok, raw_err = self._verify_read_after_write(comment_id, new_event)
        if not raw_ok:
            return False, f"Error de verificació read-after-write: {raw_err}"

        self.last_persisted_event = new_event
        return True, None


class InMemoryLockPersistenceBackend(LockPersistenceBackend):
    """Backend en memòria per a tests deterministes i simulacions d'errors."""

    def __init__(
        self,
        initial_state: Optional[Dict[str, Any]] = None,
        failure_mode: Optional[str] = None
    ):
        self.state = initial_state
        self.failure_mode = failure_mode  # None, "ABSENT", "READ_ERROR", "MALFORMED"

    def get_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if self.failure_mode == "ABSENT":
            return None, "ABSENT"
        if self.failure_mode == "READ_ERROR":
            return None, "READ_ERROR: Error de xarxa o timeout simulat"
        if self.failure_mode == "MALFORMED":
            return None, "MALFORMED: JSON invàlid o esquema incomplet simulat"
        if self.state is None:
            return None, "ABSENT"
        if not isinstance(self.state, dict) or "locked" not in self.state or not isinstance(self.state["locked"], bool):
            return None, "MALFORMED: Esquema de dades invàlid a memòria"
        return dict(self.state), None

    def set_state(self, state: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if self.failure_mode == "WRITE_ERROR":
            return False, "Error d'escriptura simulat"
        self.state = dict(state)
        return True, None

# Tarifes oficials OCI 2026 (en EUR, convertit a tipus oficial mitjà 1 USD = 0.92 EUR)
# Preus per recurs que superi l'Always Free:
RATES_EUR = {
    # Ampere A1 (per hora per OCPU i per GB RAM)
    "ampere_ocpu_hour": 0.0092,      # 0.0100 USD/h
    "ampere_ram_gb_hour": 0.00138,   # 0.0015 USD/GB-h
    # AMD E4 / Intel (formes de pagament si es creen per error)
    "standard_e4_ocpu_hour": 0.0368, # 0.0400 USD/h
    "standard_e4_ram_gb_hour": 0.00184,
    # Block Storage / Boot volume (per GB i mes)
    "block_storage_gb_month": 0.0235, # 0.0255 USD/GB-mes
    "vpu_performance_gb_month": 0.00156,
    # IP Pública Reservada sense associar (unattached) per hora
    "unattached_public_ip_hour": 0.0046, # 0.0050 USD/h
    # Trànsit sortint més enllà de 10 TB
    "outbound_data_gb": 0.0078,      # 0.0085 USD/GB
    # NAT Gateway / Load Balancer
    "nat_gateway_hour": 0.0138,      # 0.0150 USD/h
}

# Llindars Always Free oficials mensuals (Actualitzats segons política vigent OCI)
ALWAYS_FREE_LIMITS = {
    "ampere_ocpu_hours": 1500.0,     # Equivalent a 2 OCPUs 24/7 (norma oficial Always Free OCI)
    "ampere_ram_gb_hours": 9000.0,   # Equivalent a 12 GB RAM 24/7
    "block_storage_gb": 200.0,       # 200 GB d'emmagatzematge en blocs total (Boot + Block)
    "attached_public_ips": 1,        # 1 IP pública reservada associada gratuïta
    "outbound_data_tb": 10.0,        # 10 TB de sortida al mes
}

# Configuració autoritzada per al projecte PreddicionsLliga
AUTHORIZED_BASELINE = {
    "shape": "VM.Standard.A1.Flex",
    "ocpus": 1,
    "ram_gb": 4,
    "boot_volume_gb": 50,
    "attached_public_ips": 1,
    "unattached_public_ips": 0,
    "nat_gateways": 0,
    "load_balancers": 0,
}

# Llindars econòmics d'acció (en EUR)
THRESHOLDS = {
    "NOTIFICATION": 0.01,
    "WARNING": 0.10,
    "PREVENTIVE_SHUTDOWN": 0.25,
    "RESTART_LOCK": 0.50,
    "MAX_BUDGET_CEILING": 1.00,
}

# Antiguitat màxima permesa per a les dades de facturació oficials (hores)
MAX_OFFICIAL_BILLING_AGE_HOURS = 48.0


def validate_recovery_metadata(
    actor: Any,
    unlock_reason: Any,
    workflow_run_id: Any = None,
    is_github_actions: Optional[bool] = None
) -> Tuple[bool, Optional[str], Optional[Dict[str, str]]]:
    """
    Valida de forma estricta (fail-closed) les metadades d'auditoria per a la recuperació administrativa:
    - actor: ha de ser str no buit ni pur whitespace.
    - unlock_reason: ha de ser str no buit ni pur whitespace.
    - workflow_run_id:
        - Si s'executa a GitHub Actions: ha de ser un enter o cadena numèrica/text no buit i no 'manual'.
        - Si és entorn local CLI: si no s'ha especificat o és buit/'manual', es genera un id únic 'cli-local-<ts>-<uuid>'.
    Retorna (ok, error_msg, sanitized_metadata_dict).
    """
    if actor is None or not isinstance(actor, str) or not actor.strip():
        return False, "Recuperació denegada: el camp 'actor' és obligatori, ha de ser de tipus text i no pot ser buit.", None

    if unlock_reason is None or not isinstance(unlock_reason, str) or not unlock_reason.strip():
        return False, "Recuperació denegada: el camp 'unlock_reason' és obligatori, ha de ser de tipus text i no pot ser buit.", None

    if is_github_actions is None:
        is_github_actions = os.environ.get("GITHUB_ACTIONS") == "true"

    clean_actor = actor.strip()
    clean_reason = unlock_reason.strip()

    if is_github_actions:
        if workflow_run_id is None or not isinstance(workflow_run_id, (str, int)):
            return False, "Recuperació denegada: a GitHub Actions cal especificar un 'workflow_run_id' real i vàlid (no s'accepta 'manual').", None
        w_str = str(workflow_run_id).strip()
        if not w_str or w_str.lower() == "manual" or re.fullmatch(r"[0-9]+", w_str) is None or int(w_str) <= 0:
            return False, "Recuperació denegada: a GitHub Actions cal especificar un 'workflow_run_id' real, numèric (dígits ASCII 0-9) i estrictament positiu.", None
        clean_run_id = w_str
    else:
        if workflow_run_id is not None and isinstance(workflow_run_id, (str, int)) and str(workflow_run_id).strip() and str(workflow_run_id).strip().lower() != "manual":
            clean_run_id = str(workflow_run_id).strip()
        else:
            ts = int(time.time())
            u = uuid.uuid4().hex[:8]
            clean_run_id = f"cli-local-{ts}-{u}"

    return True, None, {
        "actor": clean_actor,
        "unlock_reason": clean_reason,
        "workflow_run_id": clean_run_id
    }


class CostGuard:
    """
    Controlador de vigilància de costos i protecció econòmica per a OCI.
    """

    def __init__(
        self,
        state_path: Optional[Path] = None,
        persistence_backend: Optional[LockPersistenceBackend] = None,
        oci_anchor: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ):
        self.dry_run = dry_run
        self.state_path = state_path or STATE_FILE
        self.persistence_backend = self._resolve_persistence_backend(persistence_backend)
        self.persistence_status = "OK"
        self.persistence_error: Optional[str] = None
        self.oci_anchor: Optional[Dict[str, Any]] = oci_anchor
        self.latest_ledger_event: Optional[Dict[str, Any]] = None
        self.state: Dict[str, Any] = self._load_state()
        self._reconcile_with_persistent_source()

    @staticmethod
    def verify_anchor_coherence(
        ledger_state: Optional[Dict[str, Any]],
        ledger_err: Optional[str],
        oci_anchor: Optional[Dict[str, Any]]
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifica la coherència estricta entre el GitHub Issue Ledger i la Segona Àncora d'OCI.
        Retorna (coherent, error_message).
        Davant qualsevol anomalia, truncació, absència o discordança, retorna (False, motiu).
        Aplica principi fail-closed: cap incertesa permet considerar l'àncora vàlida.
        """
        # 1. Validació d'absència d'àncora OCI
        if not oci_anchor or not isinstance(oci_anchor, dict):
            if ledger_state is not None:
                return False, "MALFORMED: Absència d'àncora OCI amb ledger existent (desaparició o no inicialitzada)"
            if ledger_err == "ABSENT":
                return False, "ABSENT: Àncora OCI i GitHub Ledger no inicialitzats (requereix bootstrap administratiu autoritzat)"
            return False, f"MALFORMED: Àncora OCI absent o no disponible davant del ledger ({ledger_err or 'ledger buit'})"

        # 2. Validació de tipus i camps obligatoris de l'àncora OCI
        if "economic_lock" not in oci_anchor:
            return False, "MALFORMED: Camp obligatori 'economic_lock' absent a l'àncora OCI"

        lock_raw = oci_anchor.get("economic_lock")
        if isinstance(lock_raw, bool):
            lock_val = lock_raw
        elif isinstance(lock_raw, str) and lock_raw.lower() in ("true", "false"):
            lock_val = (lock_raw.lower() == "true")
        else:
            return False, f"MALFORMED: Camp 'economic_lock' ha de ser un booleà o cadena 'true'/'false' (obtingut {lock_raw})"

        raw_seq = oci_anchor.get("ledger_sequence")
        oci_seq: Optional[int] = None
        if raw_seq is not None:
            if isinstance(raw_seq, bool):
                return False, "MALFORMED: Camp 'ledger_sequence' no pot ser de tipus booleà"
            elif isinstance(raw_seq, int):
                if raw_seq <= 0:
                    return False, f"MALFORMED: Camp 'ledger_sequence' ha de ser un enter positiu (>0), obtingut {raw_seq}"
                oci_seq = raw_seq
            elif isinstance(raw_seq, str) and raw_seq.isdigit() and int(raw_seq) > 0:
                oci_seq = int(raw_seq)
            else:
                return False, f"MALFORMED: Camp 'ledger_sequence' a l'àncora OCI invàlid ({raw_seq})"

        oci_hash = oci_anchor.get("ledger_hash")
        if oci_hash is not None:
            if not isinstance(oci_hash, str) or len(oci_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in oci_hash):
                return False, f"MALFORMED: Format de 'ledger_hash' a l'àncora OCI invàlid ({oci_hash})"

        oci_event_id = oci_anchor.get("ledger_event_id")
        if oci_event_id is not None:
            if not isinstance(oci_event_id, str) or not oci_event_id.strip():
                return False, "MALFORMED: Camp 'ledger_event_id' a l'àncora OCI invàlid"

        gh_seq = ledger_state.get("sequence") if ledger_state else None

        # 3. Validació de completesa i coherència interna de l'àncora OCI
        if lock_val is False and (oci_seq is None or gh_seq == oci_seq) and ledger_state and ledger_state.get("locked") is True:
            return False, "MALFORMED: Incoherència: Àncora OCI indica economic_lock=False però GitHub Ledger està LOCKED"

        if oci_seq is None or oci_hash is None or oci_event_id is None:
            return False, "MALFORMED: L'àncora OCI és incompleta: manca ledger_sequence, ledger_hash o ledger_event_id"

        # 4. Validació creuada contra el GitHub Issue Ledger
        if ledger_err == "ABSENT":
            return False, f"MALFORMED: Truncació total detectada: Àncora OCI activa (seq={oci_seq}) però GitHub Ledger absent"

        if ledger_err:
            return False, f"MALFORMED: GitHub Ledger invàlid davant d'àncora OCI ({ledger_err})"

        if not ledger_state:
            return False, "MALFORMED: GitHub Ledger no conté dades d'estat vàlides"

        gh_seq = ledger_state.get("sequence")
        gh_hash = ledger_state.get("hash")
        gh_event_id = ledger_state.get("event_id")

        if gh_seq is None or isinstance(gh_seq, bool) or not isinstance(gh_seq, int):
            return False, "MALFORMED: GitHub Ledger no conté un número de seqüència vàlid"

        # 5. Comprovació de truncació de cua (GitHub ha retrocedit)
        if gh_seq < oci_seq:
            return False, (
                f"MALFORMED: Truncació de cua detectada: La seqüència de GitHub ({gh_seq}) "
                f"és inferior a la darrera seqüència confirmada a OCI ({oci_seq})"
            )

        # 6. Comprovació a la mateixa seqüència: hash i event_id han de coincidir exactament
        if gh_seq == oci_seq:
            if gh_hash != oci_hash:
                return False, (
                    f"MALFORMED: Discrepància de hash a seqüència {gh_seq}: "
                    f"GitHub={gh_hash} vs OCI={oci_hash}"
                )
            if gh_event_id != oci_event_id:
                return False, (
                    f"MALFORMED: Discrepància d'event_id a seqüència {gh_seq}: "
                    f"GitHub={gh_event_id} vs OCI={oci_event_id}"
                )
            gh_locked = bool(ledger_state.get("locked"))
            if lock_val is False and gh_locked is True:
                return False, "MALFORMED: Incoherència: Àncora OCI indica economic_lock=False però GitHub Ledger està LOCKED"
            if lock_val is True and gh_locked is False:
                return False, "MALFORMED: Incoherència a la mateixa seqüència: Àncora OCI indica economic_lock=True però el GitHub Ledger és UNLOCKED"

        # 7. Comprovació si GitHub ha avançat (gh_seq > oci_seq)
        if gh_seq > oci_seq:
            events = ledger_state.get("events") or []
            ancestor = next((e for e in events if e.get("sequence") == oci_seq), None)
            if not ancestor:
                return False, (
                    f"MALFORMED: Historial bifurcat: GitHub (seq={gh_seq}) no conté l'esdeveniment ancestre "
                    f"de la seqüència OCI ({oci_seq})"
                )
            if ancestor.get("hash") != oci_hash:
                return False, (
                    f"MALFORMED: Historial bifurcat: L'esdeveniment ancestre a seqüència {oci_seq} té hash "
                    f"{ancestor.get('hash')} que no coincideix amb l'àncora OCI ({oci_hash})"
                )
            if ancestor.get("event_id") != oci_event_id:
                return False, (
                    f"MALFORMED: Historial bifurcat: L'esdeveniment ancestre a seqüència {oci_seq} té event_id "
                    f"'{ancestor.get('event_id')}' que no coincideix amb l'àncora OCI ('{oci_event_id}')"
                )
            ancestor_locked = (ancestor.get("event_type") == "LOCKED")
            if lock_val != ancestor_locked:
                return False, (
                    f"MALFORMED: Incoherència d'ancestre a seqüència {oci_seq}: "
                    f"economic_lock={lock_val} no correspon a event_type={ancestor.get('event_type')}"
                )

        return True, None

    def _resolve_persistence_backend(
        self,
        backend: Optional[LockPersistenceBackend]
    ) -> LockPersistenceBackend:
        if backend is not None:
            return backend
        if (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_TOKEN")
            and os.environ.get("GITHUB_REPOSITORY")
        ):
            issue_str = os.environ.get("COST_GUARD_ISSUE_NUMBER", "0")
            try:
                issue_num = int(issue_str)
            except ValueError:
                issue_num = 0
            return GitHubIssueLedgerPersistenceBackend(
                repository=os.environ["GITHUB_REPOSITORY"],
                token=os.environ["GITHUB_TOKEN"],
                issue_number=issue_num
            )
        if self.state_path == STATE_FILE:
            if os.environ.get("COST_GUARD_PERSISTENCE_FILE"):
                return FileLockPersistenceBackend(Path(os.environ["COST_GUARD_PERSISTENCE_FILE"]))
            return FileLockPersistenceBackend(DEFAULT_PERSISTENT_LOCK_FILE)
        return FileLockPersistenceBackend(self.state_path.parent / f".{self.state_path.stem}_persistent_lock.json")

    def _reconcile_with_persistent_source(self) -> None:
        """
        Reconcilia l'estat local amb la font persistent de seguretat.
        Aplica principi Fail-Closed: si la font persistent no està disponible,
        és absent o conté dades malformades, bloqueja qualsevol reinici.
        """
        p_data, err = self.persistence_backend.get_state()
        if p_data and p_data.get("sequence") is not None:
            self.latest_ledger_event = {
                "sequence": p_data.get("sequence"),
                "hash": p_data.get("hash"),
                "event_id": p_data.get("event_id"),
                "lock_reason": p_data.get("lock_reason"),
            }
        else:
            self.latest_ledger_event = None

        # Validar coherència amb Segona Àncora OCI si està configurada
        if self.oci_anchor is not None:
            coherent, err_anchor = self.verify_anchor_coherence(p_data, err, self.oci_anchor)
            if not coherent:
                self.persistence_status = "MALFORMED"
                self.persistence_error = f"Discrepància amb Segona Àncora OCI: {err_anchor}"
                self.state["persistence_unverified"] = True
                self.state["status"] = "PERSISTENCE_CORRUPT"
                self.state["service_state"] = "STOPPED_UNKNOWN"
                self.state["lock_reason"] = self.persistence_error
                self.save_state()
                return

        if err == "ABSENT":
            self.persistence_status = "ABSENT"
            self.persistence_error = "Font persistent de bloqueig absent"
            self.state["persistence_unverified"] = True
            self.state["status"] = "PERSISTENCE_ABSENT"
            self.state["service_state"] = "STOPPED_UNKNOWN"
            self.state["lock_reason"] = "Absència de font persistent de bloqueig (fallada conservadora)"
        elif err and err.startswith("READ_ERROR"):
            self.persistence_status = "ERROR"
            self.persistence_error = err
            self.state["persistence_unverified"] = True
            self.state["status"] = "PERSISTENCE_ERROR"
            self.state["service_state"] = "STOPPED_UNKNOWN"
            self.state["lock_reason"] = f"Error consultant font persistent: {err}"
        elif err and err.startswith("MALFORMED"):
            self.persistence_status = "MALFORMED"
            self.persistence_error = err
            self.state["persistence_unverified"] = True
            self.state["status"] = "PERSISTENCE_CORRUPT"
            self.state["service_state"] = "STOPPED_UNKNOWN"
            self.state["lock_reason"] = f"Font persistent amb dades malformades: {err}"
        else:
            self.persistence_status = "OK"
            self.persistence_error = None
            self.state["persistence_unverified"] = False

            if p_data and p_data.get("locked") is True:
                self.state["restart_locked"] = True
                self.state["economic_shutdown_triggered"] = True
                self.state["status"] = "RESTART_LOCKED"
                self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
                self.state["lock_reason"] = p_data.get("lock_reason") or "Bloqueig econòmic persistent actiu"
                self.save_state()
            elif p_data and p_data.get("locked") is False and self.oci_anchor is None:
                self.state["restart_locked"] = False
                self.state["economic_shutdown_triggered"] = False
                self.state["lock_reason"] = None
                self.state["status"] = "OK"
                self.state["service_state"] = "ACTIVE"
                self.save_state()

        # Avaluació de l'àncora OCI sincronitzada
        if self.oci_anchor is not None and self.persistence_status == "OK":
            gh_seq = p_data.get("sequence") if p_data else None
            raw_oci_seq = self.oci_anchor.get("ledger_sequence")
            oci_seq = int(raw_oci_seq) if (raw_oci_seq is not None and str(raw_oci_seq).isdigit()) else raw_oci_seq
            gh_locked = bool(p_data.get("locked")) if p_data else True
            lock_raw = self.oci_anchor.get("economic_lock")
            oci_locked = (lock_raw is True or (isinstance(lock_raw, str) and lock_raw.lower() == "true"))

            if gh_seq is not None and oci_seq is not None and gh_seq > oci_seq and not gh_locked:
                self.state["restart_locked"] = True
                self.state["economic_shutdown_triggered"] = True
                self.state["status"] = "RESTART_LOCKED"
                self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
                self.state["lock_reason"] = "Desbloqueig pendent de sincronització a l'àncora OCI (gh_seq > oci_seq)"
                self.save_state()
            elif gh_seq is not None and oci_seq is not None and gh_seq == oci_seq and not gh_locked and not oci_locked:
                self.state["restart_locked"] = False
                self.state["economic_shutdown_triggered"] = False
                self.state["lock_reason"] = None
                self.state["status"] = "OK"
                self.state["service_state"] = "ACTIVE"
                self.state["authorized_manual_override"] = False
                self.save_state()
            elif oci_locked or gh_locked:
                self.state["restart_locked"] = True
                self.state["economic_shutdown_triggered"] = True
                self.state["status"] = "RESTART_LOCKED"
                self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
                if not self.state.get("lock_reason"):
                    self.state["lock_reason"] = self.oci_anchor.get("lock_reason") or (p_data.get("lock_reason") if p_data else None) or "Bloqueig econòmic persistent confirmat per àncora OCI"
                self.save_state()

    def reconcile_with_oci_anchor(self, oci_anchor: Dict[str, Any], dry_run: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Reconcilia i verifica la coherència del ledger persistent amb una àncora OCI rebuda.
        Retorna (coherent, error_o_motiu).
        """
        if dry_run:
            self.dry_run = True
        self.oci_anchor = oci_anchor
        p_data, err = self.persistence_backend.get_state()
        if p_data and p_data.get("sequence") is not None:
            self.latest_ledger_event = {
                "sequence": p_data.get("sequence"),
                "hash": p_data.get("hash"),
                "event_id": p_data.get("event_id"),
                "lock_reason": p_data.get("lock_reason"),
            }
        else:
            self.latest_ledger_event = None
        coherent, err_anchor = self.verify_anchor_coherence(p_data, err, oci_anchor)
        if not coherent:
            self.persistence_status = "MALFORMED"
            self.persistence_error = f"Discrepància amb Segona Àncora OCI: {err_anchor}"
            self.state["persistence_unverified"] = True
            self.state["status"] = "PERSISTENCE_CORRUPT"
            self.state["service_state"] = "STOPPED_UNKNOWN"
            self.state["lock_reason"] = self.persistence_error
            if not dry_run:
                self.save_state()
            return False, self.persistence_error

        self.persistence_status = "OK"
        self.persistence_error = None
        self.state["persistence_unverified"] = False

        gh_seq = p_data.get("sequence") if p_data else None
        raw_oci_seq = oci_anchor.get("ledger_sequence")
        oci_seq = int(raw_oci_seq) if (raw_oci_seq is not None and str(raw_oci_seq).isdigit()) else raw_oci_seq
        gh_locked = bool(p_data.get("locked")) if p_data else True
        lock_raw = oci_anchor.get("economic_lock")
        oci_locked = (lock_raw is True or (isinstance(lock_raw, str) and lock_raw.lower() == "true"))

        if gh_seq is not None and oci_seq is not None and gh_seq > oci_seq and not gh_locked:
            self.state["restart_locked"] = True
            self.state["economic_shutdown_triggered"] = True
            self.state["status"] = "RESTART_LOCKED"
            self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
            self.state["lock_reason"] = "Desbloqueig pendent de sincronització a l'àncora OCI (gh_seq > oci_seq)"
            if not dry_run:
                self.save_state()
        elif gh_seq is not None and oci_seq is not None and gh_seq == oci_seq and not gh_locked and not oci_locked:
            self.state["restart_locked"] = False
            self.state["economic_shutdown_triggered"] = False
            self.state["lock_reason"] = None
            self.state["status"] = "OK"
            self.state["service_state"] = "ACTIVE"
            self.state["authorized_manual_override"] = False
            if not dry_run:
                self.save_state()
        elif oci_locked or gh_locked:
            self.state["restart_locked"] = True
            self.state["economic_shutdown_triggered"] = True
            self.state["status"] = "RESTART_LOCKED"
            self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
            if not self.state.get("lock_reason"):
                self.state["lock_reason"] = oci_anchor.get("lock_reason") or (p_data.get("lock_reason") if p_data else None) or "Bloqueig econòmic persistent confirmat per àncora OCI"
            if not dry_run:
                self.save_state()

        return True, None

    def get_latest_ledger_metadata(self) -> Optional[Dict[str, Any]]:
        """Retorna les metadades del darrer esdeveniment vàlid per sincronitzar l'àncora OCI."""
        if self.persistence_status != "OK" or self.state.get("persistence_unverified"):
            return None
        if self.latest_ledger_event and self.latest_ledger_event.get("sequence") is not None:
            return self.latest_ledger_event
        if hasattr(self.persistence_backend, "last_persisted_event") and self.persistence_backend.last_persisted_event:
            return self.persistence_backend.last_persisted_event
        p_data, err = self.persistence_backend.get_state()
        if p_data and not err and p_data.get("sequence") is not None:
            return {
                "sequence": p_data.get("sequence"),
                "hash": p_data.get("hash"),
                "event_id": p_data.get("event_id"),
                "lock_reason": p_data.get("lock_reason"),
            }
        return None

    def _persist_lock_state(self, locked: bool, reason: Optional[str] = None) -> bool:
        """Escriu de manera síncrona l'estat de bloqueig a la font persistent externa."""
        payload = {
            "locked": locked,
            "lock_reason": reason,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "authorized_manual_override": bool(self.state.get("authorized_manual_override", False)),
        }
        success, err = self.persistence_backend.set_state(payload)
        if success:
            if hasattr(self.persistence_backend, "last_persisted_event") and self.persistence_backend.last_persisted_event:
                self.latest_ledger_event = self.persistence_backend.last_persisted_event
        else:
            print(f"[!] ALERTA CRÍTICA: Error persistint bloqueig extern: {err}")
        return success

    def initialize_persistent_lock(self, locked: bool = False, reason: Optional[str] = None) -> bool:
        """Inicialitza explícitament la font persistent amb un estat conegut."""
        success = self._persist_lock_state(locked=locked, reason=reason)
        if success:
            self.persistence_status = "OK"
            self.persistence_error = None
            self.state["persistence_unverified"] = False
            self.state["restart_locked"] = locked
            self.state["economic_shutdown_triggered"] = locked
            if locked:
                self.state["status"] = "RESTART_LOCKED"
                self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
                self.state["lock_reason"] = reason
            else:
                self.state["status"] = "OK"
                self.state["service_state"] = "ACTIVE"
                self.state["lock_reason"] = None
            self.save_state()
        return success

    def _load_state(self) -> Dict[str, Any]:
        """Carrega l'estat des de disc o inicialitza els valors per defecte."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "status": "OK",
            "service_state": "ACTIVE",
            "last_check_utc": datetime.now(timezone.utc).isoformat(),
            "uptime_hours_total": 0.0,
            "estimated_cost_eur": 0.0,
            "official_cost_eur": 0.0,
            "last_official_update_utc": None,
            "official_data_age_hours": None,
            "stale_billing_data": False,
            "discovery_failed": False,
            "shutdown_cause_known": True,
            "unexpected_resources": [],
            "economic_shutdown_triggered": False,
            "restart_locked": False,
            "lock_reason": None,
            "authorized_manual_override": False,
            "active_provisioning": dict(AUTHORIZED_BASELINE),
        }

    def save_state(self) -> None:
        """Escriu atòmicament l'estat a disc."""
        if getattr(self, "dry_run", False):
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        tmp_path.replace(self.state_path)

    # -------------------------------------------------------------------------
    # A. Càlcul de Cost Teòric Local (Sense dependre de l'Usage API)
    # -------------------------------------------------------------------------
    def compute_estimated_cost(
        self,
        provisioning: Dict[str, Any],
        uptime_hours: float,
        monthly_days: int = 30
    ) -> Tuple[float, List[str]]:
        """
        Calcula el cost teòric dels recursos provisionats per a les hores indicades.
        Retorna (cost_total_eur, llista_d_anomalies_o_recursos_facturables).
        """
        anomalies: List[str] = []
        cost_eur = 0.0

        shape = provisioning.get("shape", "")
        ocpus = float(provisioning.get("ocpus", 0))
        ram_gb = float(provisioning.get("ram_gb", 0))
        is_running = provisioning.get("is_running", True)
        boot_volume_gb = float(provisioning.get("boot_volume_gb", 0))
        extra_volumes_gb = float(provisioning.get("extra_volumes_gb", 0))
        total_storage_gb = boot_volume_gb + extra_volumes_gb
        unattached_ips = int(provisioning.get("unattached_public_ips", 0))
        nat_gateways = int(provisioning.get("nat_gateways", 0))
        load_balancers = int(provisioning.get("load_balancers", 0))
        outbound_data_gb = float(provisioning.get("outbound_data_gb", 0))

        # 1. Validació de forma (Shape) i còmput actiu
        # Nota crítica: Si la instància està STOPPED, no consumeix OCPU/RAM.
        effective_compute_hours = uptime_hours if is_running else 0.0

        if shape == "VM.Standard.A1.Flex":
            # Consum d'hores Ampere
            ocpu_hours = ocpus * effective_compute_hours
            ram_hours = ram_gb * effective_compute_hours

            # Dins dels límits Always Free (1500 OCPU-h / 9000 RAM-GB-h al mes)
            max_ocpu_free = ALWAYS_FREE_LIMITS["ampere_ocpu_hours"]
            max_ram_free = ALWAYS_FREE_LIMITS["ampere_ram_gb_hours"]

            billable_ocpu_h = max(0.0, ocpu_hours - max_ocpu_free)
            billable_ram_h = max(0.0, ram_hours - max_ram_free)

            if billable_ocpu_h > 0:
                c = billable_ocpu_h * RATES_EUR["ampere_ocpu_hour"]
                cost_eur += c
                anomalies.append(f"Ampere OCPU supera Always Free: {billable_ocpu_h:.1f}h (+{c:.4f}€)")
            if billable_ram_h > 0:
                c = billable_ram_h * RATES_EUR["ampere_ram_gb_hour"]
                cost_eur += c
                anomalies.append(f"Ampere RAM supera Always Free: {billable_ram_h:.1f}GB-h (+{c:.4f}€)")
        else:
            # Forma no autoritzada (p. ex. E4, Intel, Bare Metal)
            anomalies.append(f"Forma no autoritzada de pagament: {shape}")
            # Factura des del primer minut mentre està engegada
            if is_running:
                ocpu_cost = ocpus * effective_compute_hours * RATES_EUR["standard_e4_ocpu_hour"]
                ram_cost = ram_gb * effective_compute_hours * RATES_EUR["standard_e4_ram_gb_hour"]
                cost_eur += (ocpu_cost + ram_cost)

        # 2. Emmagatzematge en disc (Boot + Block Volumes)
        # ALERTA CRÍTICA: El disc PROVISIONAT factura encara que la màquina estigui STOPPED!
        # Always Free cobreix 200 GB
        if total_storage_gb > ALWAYS_FREE_LIMITS["block_storage_gb"]:
            excess_gb = total_storage_gb - ALWAYS_FREE_LIMITS["block_storage_gb"]
            # Prorratejat pel mes
            hours_in_month = monthly_days * 24.0
            month_fraction = uptime_hours / hours_in_month
            storage_cost = excess_gb * RATES_EUR["block_storage_gb_month"] * month_fraction
            cost_eur += storage_cost
            anomalies.append(f"Disc supera 200GB Always Free ({total_storage_gb:.0f}GB): +{storage_cost:.4f}€")

        # 3. Adreces IP Públiques No Associades (Unattached Reserved IPs)
        if unattached_ips > 0:
            ip_cost = unattached_ips * uptime_hours * RATES_EUR["unattached_public_ip_hour"]
            cost_eur += ip_cost
            anomalies.append(f"IP pública reservada sense associar detectada ({unattached_ips}): +{ip_cost:.4f}€")

        # 4. NAT Gateways (Servei de pagament)
        if nat_gateways > 0:
            nat_cost = nat_gateways * uptime_hours * RATES_EUR["nat_gateway_hour"]
            cost_eur += nat_cost
            anomalies.append(f"NAT Gateway no autoritzat detectat ({nat_gateways}): +{nat_cost:.4f}€")

        # 5. Load Balancers adicionals
        if load_balancers > 1: # 1 Always Free permissible si és 10Mbps
            lb_cost = (load_balancers - 1) * uptime_hours * 0.025
            cost_eur += lb_cost
            anomalies.append(f"Load Balancer excedentari detectat: +{lb_cost:.4f}€")

        # 6. Trànsit sortint (més enllà de 10 TB = 10,240 GB)
        if outbound_data_gb > (ALWAYS_FREE_LIMITS["outbound_data_tb"] * 1024.0):
            excess_traffic = outbound_data_gb - (ALWAYS_FREE_LIMITS["outbound_data_tb"] * 1024.0)
            traffic_cost = excess_traffic * RATES_EUR["outbound_data_gb"]
            cost_eur += traffic_cost
            anomalies.append(f"Trànsit sortint superior a 10TB: +{traffic_cost:.4f}€")

        return round(cost_eur, 4), anomalies

    # -------------------------------------------------------------------------
    # B. Ingestió de Cost Oficial (OCI Usage API / Cost Reports)
    # -------------------------------------------------------------------------
    def ingest_official_billing(
        self,
        official_data: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[float], bool]:
        """
        Processa les dades oficials rebudes d'OCI Usage API o Cost Reports.
        Retorna (cost_oficial_eur, is_stale).
        """
        now = datetime.now(timezone.utc)

        if not official_data or "amount_eur" not in official_data:
            # Dades absents: calcular antiguitat respecte a l'última dada guardada
            last_dt = self._parse_dt(self.state.get("last_official_update_utc"))
            if last_dt:
                age_h = (now - last_dt).total_seconds() / 3600.0
                self.state["official_data_age_hours"] = round(age_h, 1)
                self.state["stale_billing_data"] = age_h > MAX_OFFICIAL_BILLING_AGE_HOURS
            else:
                self.state["official_data_age_hours"] = None
                self.state["stale_billing_data"] = True
            return None, self.state["stale_billing_data"]

        amount = float(official_data["amount_eur"])
        report_dt = self._parse_dt(official_data.get("timestamp_utc")) or now
        age_h = max(0.0, (now - report_dt).total_seconds() / 3600.0)

        self.state["official_cost_eur"] = round(amount, 4)
        self.state["last_official_update_utc"] = report_dt.isoformat()
        self.state["official_data_age_hours"] = round(age_h, 1)
        self.state["stale_billing_data"] = age_h > MAX_OFFICIAL_BILLING_AGE_HOURS

        return amount, self.state["stale_billing_data"]

    # -------------------------------------------------------------------------
    # C. Motor d'Avaluació de Llindars i Protecció
    # -------------------------------------------------------------------------
    def evaluate(
        self,
        current_provisioning: Optional[Dict[str, Any]] = None,
        uptime_hours: float = 744.0,
        official_data: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Avalua conjuntament el cost teòric i el cost oficial, comprova llindars
        i determina l'acció necessària.
        """
        if dry_run:
            self.dry_run = True
        prov = current_provisioning or self.state.get("active_provisioning", AUTHORIZED_BASELINE)
        self.state["active_provisioning"] = prov
        self.state["uptime_hours_total"] = uptime_hours
        self.state["last_check_utc"] = datetime.now(timezone.utc).isoformat()

        # 0. Verificació d'àncora OCI si es troba a la provisió actual
        if prov and "oci_anchor" in prov:
            ok, anchor_err = self.reconcile_with_oci_anchor(prov["oci_anchor"], dry_run=dry_run)
            if not ok:
                if not dry_run:
                    self.save_state()
                return self.get_summary()

        # 1. Càlcul estimat
        est_cost, anomalies = self.compute_estimated_cost(prov, uptime_hours)
        self.state["estimated_cost_eur"] = est_cost
        self.state["unexpected_resources"] = anomalies

        # 2. Ingestió de dades oficials
        off_cost, is_stale = self.ingest_official_billing(official_data)

        # 3. Cost de referència (el màxim entre el teòric estimat i l'oficial conegut)
        effective_cost = max(est_cost, self.state.get("official_cost_eur", 0.0))

        # 4. Determinació d'estat
        # Si la font persistent no s'ha pogut verificar o està en error, fallar de manera conservadora
        if self.persistence_status != "OK" or self.state.get("persistence_unverified"):
            if self.persistence_status == "MALFORMED":
                self.state["status"] = "PERSISTENCE_CORRUPT"
            else:
                self.state["status"] = f"PERSISTENCE_{self.persistence_status}"
            self.state["service_state"] = "STOPPED_UNKNOWN"
            if not dry_run:
                self.save_state()
            return self.get_summary()

        # Si hi ha bloqueig previ per motius econòmics i no hi ha autorització manual
        if self.state.get("restart_locked") and not self.state.get("authorized_manual_override"):
            self.state["status"] = "RESTART_LOCKED"
            self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
            if not dry_run:
                self.save_state()
            return self.get_summary()

        # Prioritat màxima: Detecció de recurs de pagament no autoritzat
        critical_anomalies = [a for a in anomalies if "Forma no autoritzada" in a or "NAT Gateway" in a]

        if effective_cost >= THRESHOLDS["RESTART_LOCK"]:
            self.state["status"] = "RESTART_LOCKED"
            self.state["service_state"] = "STOPPED_ECONOMIC_LOCK"
            self.state["economic_shutdown_triggered"] = True
            self.state["restart_locked"] = True
            self.state["authorized_manual_override"] = False
            self.state["lock_reason"] = f"Despesa acumulada ({effective_cost:.2f}€) ha superat el llindar de bloqueig ({THRESHOLDS['RESTART_LOCK']}€)"
            if not dry_run:
                self._persist_lock_state(locked=True, reason=self.state["lock_reason"])
        elif len(critical_anomalies) > 0:
            # Recurs de pagament greu detectat: aturada preventiva immediata
            self.state["status"] = "PREVENTIVE_SHUTDOWN"
            self.state["service_state"] = "STOPPED_PREVENTIVE"
            self.state["economic_shutdown_triggered"] = True
            self.state["authorized_manual_override"] = False
            self.state["lock_reason"] = f"Detectat recurs de pagament no autoritzat: {', '.join(critical_anomalies)}"
            if not dry_run:
                self._persist_lock_state(locked=True, reason=self.state["lock_reason"])
        elif effective_cost >= THRESHOLDS["PREVENTIVE_SHUTDOWN"]:
            self.state["status"] = "PREVENTIVE_SHUTDOWN"
            self.state["service_state"] = "STOPPED_PREVENTIVE"
            self.state["economic_shutdown_triggered"] = True
            self.state["authorized_manual_override"] = False
            self.state["lock_reason"] = f"Cost ({effective_cost:.2f}€) ha superat el límit d'aturada preventiva ({THRESHOLDS['PREVENTIVE_SHUTDOWN']}€)"
            if not dry_run:
                self._persist_lock_state(locked=True, reason=self.state["lock_reason"])
        elif effective_cost >= THRESHOLDS["WARNING"] or is_stale:
            self.state["status"] = "WARNING"
            self.state["service_state"] = "ACTIVE"
        elif effective_cost >= THRESHOLDS["NOTIFICATION"] or len(anomalies) > 0:
            self.state["status"] = "NOTIFICATION"
            self.state["service_state"] = "ACTIVE"
        else:
            self.state["status"] = "OK"
            self.state["service_state"] = "ACTIVE"
            self.state["economic_shutdown_triggered"] = False
            self.state["lock_reason"] = None

        if not dry_run:
            self.save_state()
        return self.get_summary()

    def can_restart(self) -> Tuple[bool, Optional[str]]:
        """
        Determina si el sistema pot reiniciar-se automàticament.
        Regla obligatòria: Davant qualsevol incertesa o risc econòmic, NO reiniciar.
        Retorna (autoritzat, motiu_si_denegat).
        """
        if self.persistence_status != "OK" or self.state.get("persistence_unverified"):
            return False, f"Reinici denegat: Estat de persistència no verificat ({self.persistence_status}: {self.persistence_error}). Cal autorització o inicialització manual."

        if self.state.get("restart_locked"):
            if not self.state.get("authorized_manual_override"):
                return False, f"Reinici denegat: Bloqueig econòmic actiu ({self.state.get('lock_reason')}). Cal autorització manual."

        if self.state.get("economic_shutdown_triggered"):
            if not self.state.get("authorized_manual_override"):
                return False, f"Reinici denegat: Aturada preventiva activada ({self.state.get('lock_reason')}). Cal autorització manual."

        if self.state.get("discovery_failed"):
            return False, "Reinici denegat: El descobriment de recursos OCI ha fallat (incertesa d'inventari)."

        if self.state.get("stale_billing_data"):
            return False, "Reinici denegat: Dades de facturació desconegudes o amb antiguitat superior a 48 hores."

        if not self.state.get("shutdown_cause_known", True):
            return False, "Reinici denegat: Causa de l'aturada de la VM desconeguda. Requereix comprovació manual."

        unexpected = self.state.get("unexpected_resources", [])
        if unexpected and not self.state.get("authorized_manual_override"):
            return False, f"Reinici denegat: Recursos inesperats o facturables detectats ({', '.join(unexpected)})."

        return True, None

    def set_discovery_status(self, success: bool, reason: Optional[str] = None) -> None:
        """Indica si el descobriment de recursos OCI s'ha completat o ha fallat."""
        self.state["discovery_failed"] = not success
        if not success and reason:
            self.state["lock_reason"] = f"Descobriment fallit: {reason}"
        self.save_state()

    def set_shutdown_cause_known(self, known: bool, reason: Optional[str] = None) -> None:
        """Registra si la causa de l'aturada de la instància és coneguda o incerta."""
        self.state["shutdown_cause_known"] = known
        if not known and reason:
            self.state["lock_reason"] = f"Causa d'aturada desconeguda: {reason}"
        self.save_state()

    def authorize_manual_restart(
        self,
        token: str,
        current_provisioning: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Autoritza manualment el reinici després d'una aturada econòmica.
        Requereix passar la paraula clau de confirmació 'AUTORITZO_REINICI_SENSE_DESPESA'
        i verificar que l'inventari OCI no contingui recursos inesperats.
        """
        if token != "AUTORITZO_REINICI_SENSE_DESPESA":
            return False

        # Verificació conservadora d'inventari: rebutjar si hi ha recursos inesperats
        prov = current_provisioning or self.state.get("active_provisioning", AUTHORIZED_BASELINE)
        _, anomalies = self.compute_estimated_cost(prov, uptime_hours=744.0)
        critical_or_paid = [
            a for a in anomalies
            if "Forma no autoritzada" in a or "NAT Gateway" in a or "IP pública reservada sense associar" in a or "Disc supera 200GB" in a
        ]
        if len(critical_or_paid) > 0:
            return False

        self.state["authorized_manual_override"] = True
        self.state["economic_shutdown_triggered"] = False
        self.state["restart_locked"] = False
        self.state["discovery_failed"] = False
        self.state["shutdown_cause_known"] = True
        self.state["unexpected_resources"] = []
        self.state["service_state"] = "ACTIVE"
        self.state["status"] = "OK"
        self.state["lock_reason"] = "Reinici manual autoritzat per l'administrador"
        self.state["persistence_unverified"] = False
        self.persistence_status = "OK"
        self.persistence_error = None

        self._persist_lock_state(locked=False, reason="Reinici manual autoritzat per l'administrador")
        self.save_state()
        return True

    def execute_administrative_recovery(
        self,
        actor: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
        unlock_reason: Optional[str] = None,
        current_provisioning: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ) -> Tuple[bool, str]:
        """
        Executa la recuperació administrativa autenticada a nivell de CostGuard:
        0. Valida estrictament les metadades d'auditoria (actor, unlock_reason, workflow_run_id).
        1. Valida que l'inventari no contingui recursos no autoritzats ni despesa > 0.
        2. Obté l'event_id del bloqueig actiu del ledger persistent.
        3. Escriu l'esdeveniment UNLOCKED al ledger.
        4. Si oci_anchor és present, actualitza la coherència o prepara les metadades.
        5. Retorna (success, missatge).
        """
        # 0. Validació estricta de metadades d'auditoria (Fail-Closed)
        ok_meta, meta_err, clean_meta = validate_recovery_metadata(
            actor=actor,
            unlock_reason=unlock_reason,
            workflow_run_id=workflow_run_id
        )
        if not ok_meta:
            return False, meta_err

        # 1. Validació conservadora d'inventari
        prov = current_provisioning or self.state.get("active_provisioning", AUTHORIZED_BASELINE)
        est_cost, anomalies = self.compute_estimated_cost(prov, uptime_hours=744.0)
        critical_or_paid = [
            a for a in anomalies
            if "Forma no autoritzada" in a or "NAT Gateway" in a or "IP pública reservada sense associar" in a or "Disc supera 200GB" in a
        ]
        if len(critical_or_paid) > 0 or len(prov.get("anomalies_detected", [])) > 0 or est_cost > 0.0:
            return False, "Recuperació denegada: l'inventari OCI conté recursos no autoritzats."

        # 2. Obtenir metadades de l'últim bloqueig actiu del ledger
        p_data, err = self.persistence_backend.get_state()
        if err:
            return False, f"Recuperació denegada: error llegint el ledger persistent ({err})."

        if not p_data or p_data.get("locked") is not True:
            return False, "Recuperació denegada: el ledger no es troba en estat LOCKED actiu."

        resolves_lock_id = p_data.get("event_id")
        if not resolves_lock_id or not isinstance(resolves_lock_id, str) or not resolves_lock_id.strip():
            return False, "Recuperació denegada: l'esdeveniment LOCKED previ no té event_id vàlid."

        # 3. Escriure l'esdeveniment UNLOCKED
        unlock_payload = {
            "locked": False,
            "resolves_lock_id": resolves_lock_id.strip(),
            "actor": clean_meta["actor"],
            "workflow_run_id": clean_meta["workflow_run_id"],
            "unlock_reason": clean_meta["unlock_reason"],
        }

        if dry_run:
            return True, "Recuperació administrativa completada amb èxit (dry-run)."

        success, write_err = self.persistence_backend.set_state(unlock_payload)
        if not success:
            return False, f"Recuperació denegada: error escrivint esdeveniment UNLOCKED al ledger ({write_err})."

        new_event = getattr(self.persistence_backend, "last_persisted_event", None)
        if not new_event:
            latest_state, _ = self.persistence_backend.get_state()
            if latest_state and latest_state.get("event_id"):
                new_event = latest_state
            else:
                new_event = {
                    "sequence": (p_data.get("sequence") or 1) + 1,
                    "hash": p_data.get("hash") or "0000000000000000000000000000000000000000000000000000000000000000",
                    "event_id": f"unlock-{resolves_lock_id.strip()}",
                    "event_type": "UNLOCKED"
                }

        self.latest_ledger_event = new_event

        # 4. Si tenim àncora OCI a la instància local, actualitzar-la per mantenir coherència
        if self.oci_anchor is not None:
            oci_anchor_payload = {
                "economic_lock": False,
                "ledger_sequence": new_event.get("sequence"),
                "ledger_hash": new_event.get("hash"),
                "ledger_event_id": new_event.get("event_id"),
                "lock_reason": None,
                "unlocked_at": datetime.now(timezone.utc).isoformat()
            }
            self.reconcile_with_oci_anchor(oci_anchor_payload, dry_run=dry_run)
        else:
            self.state["restart_locked"] = False
            self.state["economic_shutdown_triggered"] = False
            self.state["lock_reason"] = None
            self.state["status"] = "OK"
            self.state["service_state"] = "ACTIVE"
            self.state["authorized_manual_override"] = False
            self.save_state()

        return True, "Recuperació administrativa completada amb èxit."

    def get_summary(self) -> Dict[str, Any]:
        """Retorna el resum complet per a diagnòstics interns o logs privats."""
        return {
            "status": self.state.get("status"),
            "service_state": self.state.get("service_state"),
            "estimated_cost_eur": self.state.get("estimated_cost_eur"),
            "official_cost_eur": self.state.get("official_cost_eur"),
            "official_data_age_hours": self.state.get("official_data_age_hours"),
            "stale_billing_data": self.state.get("stale_billing_data"),
            "unexpected_resources": self.state.get("unexpected_resources", []),
            "can_restart": self.can_restart()[0],
            "lock_reason": self.state.get("lock_reason"),
            "last_check_utc": self.state.get("last_check_utc"),
            "persistence_status": self.persistence_status,
        }

    def get_public_web_status(self) -> Dict[str, Any]:
        """
        Retorna EXCLUSIVAMENT informació no sensible per publicar a la web pública.
        NO conté OCIDs, ni dades financeres en euros, ni detalls interns del compte.
        """
        st = self.state.get("status", "OK")
        srv = self.state.get("service_state", "ACTIVE")

        web_status = "OPERATIONAL"
        if st in ("PREVENTIVE_SHUTDOWN", "RESTART_LOCKED"):
            web_status = "ECONOMIC_SHUTDOWN"
        elif st == "WARNING":
            web_status = "DEGRADED"

        return {
            "service_status": web_status,
            "is_active": srv == "ACTIVE",
            "last_verified_utc": self.state.get("last_check_utc"),
            "mode": "FALLBACK" if srv != "ACTIVE" else "LIVE",
        }

    @staticmethod
    def _parse_dt(dt_str: Any) -> Optional[datetime]:
        if not dt_str or not isinstance(dt_str, str):
            return None
        try:
            s = dt_str.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

"""
agent/protocol.py
=================
Contractes de dades, identificadors deterministes i màquina d'estats
per al Surebet Monitor (Agent Windows + Coordinador).
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field, asdict

# Estats de sessió permesos
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_CANCEL_REQUESTED = "CANCEL_REQUESTED"
STATUS_CANCELLED = "CANCELLED"
STATUS_COMPLETED = "COMPLETED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_FAILED = "FAILED"

ALL_SESSION_STATUSES: Set[str] = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_TIMEOUT,
    STATUS_FAILED,
}

# Transicions d'estat vàlides i atòmiques (aplicades al servidor/coordinador)
VALID_TRANSITIONS: Dict[str, List[str]] = {
    STATUS_PENDING: [STATUS_RUNNING, STATUS_CANCELLED],
    STATUS_RUNNING: [STATUS_CANCEL_REQUESTED, STATUS_COMPLETED, STATUS_TIMEOUT, STATUS_FAILED],
    STATUS_CANCEL_REQUESTED: [STATUS_CANCELLED, STATUS_FAILED],
    STATUS_CANCELLED: [],
    STATUS_COMPLETED: [],
    STATUS_TIMEOUT: [],
    STATUS_FAILED: []
}


def validate_transition(current_status: str, new_status: str) -> bool:
    """Valida si una transició d'estat de sessió és permesa per les regles del sistema."""
    if current_status == new_status:
        return True
    allowed = VALID_TRANSITIONS.get(current_status, [])
    return new_status in allowed


def generate_opportunity_id(match_id: str, market_name: str, legs: List[Dict[str, Any]]) -> str:
    """
    Genera un identificador canònic i determinista per a l'estructura d'una oportunitat.
    Independent de petites variacions de quota, identifica l'enfrontament i mercat.
    """
    sorted_legs = sorted(
        legs,
        key=lambda l: (str(l.get("bookmaker", "")).lower(), str(l.get("selection", "")).lower())
    )
    legs_str = "|".join(
        f"{str(l.get('bookmaker', '')).lower()}:{str(l.get('selection', '')).lower()}"
        for l in sorted_legs
    )
    raw = f"{match_id}::{market_name.strip().lower()}::{legs_str}"
    return f"opp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def generate_snapshot_id(opportunity_id: str, legs: List[Dict[str, Any]]) -> str:
    """
    Genera un identificador únic per a una observació discreta d'una oportunitat
    amb les seves quotes concretes i els timestamps de cada pota.
    """
    sorted_legs = sorted(
        legs,
        key=lambda l: (str(l.get("bookmaker", "")).lower(), str(l.get("selection", "")).lower())
    )
    legs_str = "|".join(
        f"{str(l.get('bookmaker', '')).lower()}@{float(l.get('odd', 0.0)):.2f}@{str(l.get('captured_at', ''))}"
        for l in sorted_legs
    )
    raw = f"{opportunity_id}::{legs_str}"
    return f"snap_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


@dataclass
class SearchOrder:
    session_id: str
    user_id: str
    status: str = STATUS_PENDING
    requested_duration_minutes: int = 15
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    last_heartbeat: Optional[str] = None
    ended_at: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentPresence:
    agent_id: str
    status: str = "ONLINE"  # ONLINE, BUSY, OFFLINE
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "2.1.0"
    host_name: str = "Windows-PC"
    active_session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SnapshotRecord:
    snapshot_id: str
    opportunity_id: str
    session_id: str
    cycle_index: int
    matchup: str
    market_name: str
    profit_pct: float
    freshness_tier: str
    legs: List[Dict[str, Any]]
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    age_max_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


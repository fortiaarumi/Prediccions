"""
agent
=====
Mòdul de l'Agent Local Windows i Coordinador Segur per a Prediccions Lliga.
"""
from agent.protocol import (
    SearchOrder,
    AgentPresence,
    SnapshotRecord,
    generate_opportunity_id,
    generate_snapshot_id,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_TIMEOUT,
    STATUS_FAILED
)
from agent.coordinator import MockCoordinator
from agent.snapshot_store import SnapshotStore
from agent.windows_agent import WindowsSurebetAgent

__all__ = [
    "SearchOrder",
    "AgentPresence",
    "SnapshotRecord",
    "generate_opportunity_id",
    "generate_snapshot_id",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_CANCEL_REQUESTED",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_TIMEOUT",
    "STATUS_FAILED",
    "MockCoordinator",
    "SnapshotStore",
    "WindowsSurebetAgent"
]


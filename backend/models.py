"""Pydantic and dataclass models for TrustDecay data structures."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TrustStatus(str, Enum):
    """Status for canonical trust relationships."""
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class NodeTrustStatus(str, Enum):
    """Cached trust status on an individual node."""
    TRUSTED = "TRUSTED"
    BLOCKED = "BLOCKED"


class ConnectivityState(str, Enum):
    """Node network connectivity state."""
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class LifecycleState(str, Enum):
    """Node operational lifecycle state."""
    READY = "READY"
    RECONCILING = "RECONCILING"


class TrustRelationship(BaseModel):
    """Canonical trust relationship entry."""
    id: str
    status: TrustStatus
    epoch: int


class AuthorityState(BaseModel):
    """Global authority state counter."""
    id: int = 1
    global_epoch: int


class NodeTrust(BaseModel):
    """Node cached trust observation."""
    node_id: str
    relationship_id: str
    status: NodeTrustStatus
    epoch: int
    source_node: str


class PropagationEdge(BaseModel):
    """Recorded propagation edge representing the trust graph."""
    id: Optional[int] = None
    source_node: str
    destination_node: str
    relationship_id: str
    epoch: int
    ts: str


class NodeState(BaseModel):
    """Node connectivity and lifecycle state."""
    node_id: str
    connectivity: ConnectivityState
    lifecycle: LifecycleState
    last_reconciled_epoch: int = 0


class EventLog(BaseModel):
    """Append-only audit log entry."""
    id: Optional[int] = None
    ts: str
    message: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    database: str = "connected"


class CreateTrustRequest(BaseModel):
    """Request payload for POST /trust."""
    relationship_id: str


class GrantTrustRequest(BaseModel):
    """Request payload for POST /trust/{id}/grant."""
    node_id: str


class PropagateTrustRequest(BaseModel):
    """Request payload for POST /nodes/{node_id}/propagate."""
    relationship_id: str
    to_node: str


class DemoResetResponse(BaseModel):
    """Response payload for POST /demo/reset."""
    status: str = "reset complete"


class FootprintResponse(BaseModel):
    """Response payload for GET /footprint/{relationship_id}."""
    relationship_id: str
    direct: list[str]
    indirect: list[str]


class RevokeResponse(BaseModel):
    """Response payload for POST /trust/{id}/revoke."""
    relationship_id: str
    status: str
    epoch: int
    footprint_direct: list[str] = Field(default_factory=list)
    footprint_indirect: list[str] = Field(default_factory=list)
    blocked_nodes: list[str] = Field(default_factory=list)
    skipped_offline_nodes: list[str] = Field(default_factory=list)


class AuthorizeRequest(BaseModel):
    """Request payload for POST /nodes/{node_id}/authorize."""
    relationship_id: str


class AuthorizeResponse(BaseModel):
    """Response payload for POST /nodes/{node_id}/authorize."""
    decision: str  # "ALLOW" or "DENY"
    node_id: str
    relationship_id: str
    reason: str = ""


# ---------------------------------------------------------------------------
# Phase 5 — Disconnect / Reconnect responses
# ---------------------------------------------------------------------------


class DisconnectResponse(BaseModel):
    """Response payload for POST /nodes/{node_id}/disconnect."""
    node_id: str
    connectivity: str  # "OFFLINE"


class ReconnectResponse(BaseModel):
    """Response payload for POST /nodes/{node_id}/reconnect."""
    node_id: str
    connectivity: str  # "ONLINE"
    lifecycle: str     # "READY" (after reconciliation)
    reconciled_relationships: int
    last_reconciled_epoch: int


# ---------------------------------------------------------------------------
# Phase 6 — Graph / Events / Convergence responses
# ---------------------------------------------------------------------------


class GraphNodeTrust(BaseModel):
    """One node_trust entry embedded in a graph node."""
    relationship_id: str
    status: str
    epoch: int
    source_node: str


class GraphNodeEntry(BaseModel):
    """One node's full state in the GET /graph response."""
    node_id: str
    connectivity: str
    lifecycle: str
    last_reconciled_epoch: int
    trust: list[GraphNodeTrust] = Field(default_factory=list)


class GraphResponse(BaseModel):
    """Response payload for GET /graph."""
    nodes: list[GraphNodeEntry]


class EventLogEntry(BaseModel):
    """One event_log row in the GET /events response."""
    id: int
    ts: str
    message: str


class EventsResponse(BaseModel):
    """Response payload for GET /events."""
    events: list[EventLogEntry]


class ConvergenceNodeEntry(BaseModel):
    """Per-node convergence comparison for GET /convergence."""
    node_id: str
    local_epoch: Optional[int]
    local_status: Optional[str]
    authority_epoch: int
    authority_status: str
    converged: bool


class ConvergenceResponse(BaseModel):
    """Response payload for GET /convergence."""
    relationship_id: str
    authority_epoch: int
    authority_status: str
    all_converged: bool
    nodes: list[ConvergenceNodeEntry]

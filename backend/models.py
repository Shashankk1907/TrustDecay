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

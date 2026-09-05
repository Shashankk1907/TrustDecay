"""Node management API routes."""

from fastapi import APIRouter, Response
from backend.db import get_db
from backend.models import PropagateTrustRequest, AuthorizeRequest, AuthorizeResponse
from backend.node import propagate_trust, authorize, disconnect_node, reconnect_node

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.post("/{node_id}/propagate")
def propagate(node_id: str, req: PropagateTrustRequest) -> dict:
    """Propagate a cached trust relationship from node_id to to_node."""
    with get_db() as conn:
        return propagate_trust(
            conn=conn,
            source_node=node_id,
            destination_node=req.to_node,
            relationship_id=req.relationship_id,
        )


@router.post("/{node_id}/authorize", response_model=AuthorizeResponse)
def authorize_node(node_id: str, req: AuthorizeRequest, response: Response) -> AuthorizeResponse:
    """Check whether a node is authorized for a given trust relationship.

    Returns 200 ALLOW or 403 DENY with a reason.
    Decision is deterministic and fail-safe per architecture.md §10.
    """
    with get_db() as conn:
        result = authorize(conn, node_id, req.relationship_id)

    if result["decision"] == "DENY":
        response.status_code = 403

    return AuthorizeResponse(**result)


@router.post("/{node_id}/disconnect")
def disconnect(node_id: str) -> dict:
    """Disconnect a node — sets connectivity=OFFLINE. node_trust cache is frozen (untouched).

    Per architecture.md §9 and §7:
        disconnect() → connectivity = OFFLINE (node_trust untouched)
    """
    with get_db() as conn:
        return disconnect_node(conn, node_id)


@router.post("/{node_id}/reconnect")
def reconnect(node_id: str) -> dict:
    """Reconnect an offline node via the exact fail-closed path:
    OFFLINE → ONLINE+RECONCILING → reconcile() → READY.

    Per architecture.md §7 hard rule:
        Never OFFLINE → READY → reconcile.
        Always OFFLINE → RECONCILING → READY.
    """
    with get_db() as conn:
        return reconnect_node(conn, node_id)

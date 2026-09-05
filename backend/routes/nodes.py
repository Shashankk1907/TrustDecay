"""Node management API routes."""

from fastapi import APIRouter
from backend.db import get_db
from backend.models import PropagateTrustRequest
from backend.node import propagate_trust

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

"""Trust management API routes."""

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import CreateTrustRequest, GrantTrustRequest, TrustRelationship, RevokeResponse
from backend.authority import create_trust_relationship, grant_trust, revoke_trust

router = APIRouter(prefix="/trust", tags=["trust"])


@router.post("", response_model=TrustRelationship)
def create_trust(req: CreateTrustRequest) -> TrustRelationship:
    """Create a new canonical trust relationship at the authority level."""
    with get_db() as conn:
        return create_trust_relationship(conn, req.relationship_id)


@router.post("/{relationship_id}/grant")
def grant_node_trust(relationship_id: str, req: GrantTrustRequest) -> dict:
    """Authority grants a trust relationship directly to a node."""
    with get_db() as conn:
        return grant_trust(conn, relationship_id, req.node_id)


@router.post("/{relationship_id}/revoke", response_model=RevokeResponse)
def revoke(relationship_id: str) -> RevokeResponse:
    """Revoke a trust relationship, push-blocking all reachable ONLINE nodes."""
    with get_db() as conn:
        result = revoke_trust(conn, relationship_id)
        return RevokeResponse(**result)

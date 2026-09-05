"""Views and demo reset API routes."""

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import DemoResetResponse, FootprintResponse
from backend.seed import seed_database
from backend.footprint import compute_footprint
from backend.authority import get_trust_relationship

router = APIRouter(tags=["views"])


@router.post("/demo/reset", response_model=DemoResetResponse)
def demo_reset() -> DemoResetResponse:
    """Reset the database to the canonical seed graph per architecture.md §8."""
    with get_db() as conn:
        seed_database(conn)
    return DemoResetResponse(status="reset complete")


@router.get("/footprint/{relationship_id}", response_model=FootprintResponse)
def get_footprint(relationship_id: str) -> FootprintResponse:
    """Compute the stale-trust footprint for a relationship via BFS."""
    with get_db() as conn:
        rel = get_trust_relationship(conn, relationship_id)
        if rel is None:
            raise HTTPException(
                status_code=404,
                detail=f"Trust relationship '{relationship_id}' not found",
            )
        footprint = compute_footprint(conn, relationship_id)
        return FootprintResponse(
            relationship_id=relationship_id,
            direct=footprint["direct"],
            indirect=footprint["indirect"],
        )

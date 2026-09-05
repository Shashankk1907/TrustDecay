"""Views and demo reset API routes."""

from fastapi import APIRouter
from backend.db import get_db
from backend.models import DemoResetResponse
from backend.seed import seed_database

router = APIRouter(tags=["views"])


@router.post("/demo/reset", response_model=DemoResetResponse)
def demo_reset() -> DemoResetResponse:
    """Reset the database to the canonical seed graph per architecture.md §8."""
    with get_db() as conn:
        seed_database(conn)
    return DemoResetResponse(status="reset complete")

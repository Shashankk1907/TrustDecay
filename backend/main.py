"""FastAPI main entrypoint for TrustDecay."""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.db import init_db, get_db
from backend.models import HealthResponse
from backend.routes.trust import router as trust_router
from backend.routes.nodes import router as nodes_router
from backend.routes.views import router as views_router


def startup_reconcile_all() -> None:
    """Reconcile every persisted node on process startup.

    Per architecture.md §7 and phase 5 spec:
    'On application startup: query all rows from node_state, for each node
    call reconcile(node_id) — same function as reconnect. This ensures no
    persisted node becomes READY without reconciliation after restart.'
    """
    from backend.node import reconcile

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT node_id FROM node_state;")
        rows = cursor.fetchall()

    # Each reconcile() opens its own connection via the context manager above —
    # we run them one at a time in a single transaction per node.
    for row in rows:
        with get_db() as conn:
            reconcile(conn, row["node_id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle startup and shutdown."""
    init_db()
    # Phase 5 — startup reconciliation: every persisted node must reconcile before READY
    startup_reconcile_all()
    yield


app = FastAPI(
    title="TrustDecay",
    description="Minimal fail-closed, partition-tolerant trust network",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware allowing all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(trust_router)
app.include_router(nodes_router)
app.include_router(views_router)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check() -> HealthResponse:
    """Health-check endpoint verifying database connectivity."""
    with get_db() as conn:
        conn.execute("SELECT 1;").fetchone()
    return HealthResponse(status="ok", database="connected")


# Mount frontend directory for static UI serving
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

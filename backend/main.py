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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle startup and shutdown."""
    init_db()
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

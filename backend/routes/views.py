"""Views and demo reset API routes — Phase 3 footprint + Phase 6 graph/events/convergence."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from backend.db import get_db
from backend.models import (
    DemoResetResponse,
    FootprintResponse,
    GraphResponse,
    GraphNodeEntry,
    GraphNodeTrust,
    EventsResponse,
    EventLogEntry,
    ConvergenceResponse,
    ConvergenceNodeEntry,
)
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


@router.get("/graph", response_model=GraphResponse)
def get_graph() -> GraphResponse:
    """Return all nodes with their current node_trust rows, connectivity, and lifecycle state.

    The frontend renders this directly. Per architecture.md §9:
        GET /graph → returns all nodes + their current node_trust rows
                     + connectivity + lifecycle
    """
    with get_db() as conn:
        # Fetch all node states
        node_rows = conn.execute(
            "SELECT node_id, connectivity, lifecycle, last_reconciled_epoch FROM node_state ORDER BY node_id;"
        ).fetchall()

        nodes: list[GraphNodeEntry] = []
        for nr in node_rows:
            # Fetch all node_trust rows for this node (I7 — entity scoped)
            trust_rows = conn.execute(
                """
                SELECT relationship_id, status, epoch, source_node
                FROM node_trust
                WHERE node_id = ?
                ORDER BY relationship_id;
                """,
                (nr["node_id"],),
            ).fetchall()

            trust_entries = [
                GraphNodeTrust(
                    relationship_id=tr["relationship_id"],
                    status=tr["status"],
                    epoch=tr["epoch"],
                    source_node=tr["source_node"],
                )
                for tr in trust_rows
            ]

            nodes.append(
                GraphNodeEntry(
                    node_id=nr["node_id"],
                    connectivity=nr["connectivity"],
                    lifecycle=nr["lifecycle"],
                    last_reconciled_epoch=nr["last_reconciled_epoch"],
                    trust=trust_entries,
                )
            )

    return GraphResponse(nodes=nodes)


@router.get("/events", response_model=EventsResponse)
def get_events() -> EventsResponse:
    """Return event log entries in insertion order.

    Per architecture.md §9 and phase 6 spec:
        GET /events → event log in insertion order
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, ts, message FROM event_log ORDER BY id ASC;"
        ).fetchall()

    events = [
        EventLogEntry(id=row["id"], ts=row["ts"], message=row["message"])
        for row in rows
    ]
    return EventsResponse(events=events)


@router.get("/convergence", response_model=ConvergenceResponse)
def get_convergence(
    relationship_id: Optional[str] = Query(
        default=None,
        description="Relationship to compare. Defaults to most-recently-revoked (highest epoch, status=REVOKED).",
    )
) -> ConvergenceResponse:
    """Return per-node convergence comparison against authority for a relationship.

    Per architecture.md §9 and phase 6 spec:
        GET /convergence → per-node { local_epoch, local_status, authority_epoch, authority_status, converged }
        The response must clearly indicate whether all nodes have converged.

    Optional query param: ?relationship_id=alice
    Defaults to the most-recently-revoked relationship (highest epoch where status=REVOKED).
    """
    with get_db() as conn:
        # Determine which relationship to compare
        if relationship_id is not None:
            rel = get_trust_relationship(conn, relationship_id)
            if rel is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Trust relationship '{relationship_id}' not found",
                )
        else:
            # Default: most-recently-revoked relationship (highest epoch, status=REVOKED)
            row = conn.execute(
                """
                SELECT id, status, epoch FROM trust_relationship
                WHERE status = 'REVOKED'
                ORDER BY epoch DESC
                LIMIT 1;
                """
            ).fetchone()
            if row is None:
                # No revoked relationship — fall back to most recent by epoch
                row = conn.execute(
                    "SELECT id, status, epoch FROM trust_relationship ORDER BY epoch DESC LIMIT 1;"
                ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="No trust relationships found in the system",
                )
            rel = get_trust_relationship(conn, row["id"])

        # Gather all nodes that ever touched this relationship (footprint approach)
        # Also include nodes that currently hold node_trust for this relationship
        node_ids_rows = conn.execute(
            "SELECT DISTINCT node_id FROM node_trust WHERE relationship_id = ? ORDER BY node_id;",
            (rel.id,),
        ).fetchall()

        node_entries: list[ConvergenceNodeEntry] = []
        all_converged = True

        for nr in node_ids_rows:
            nid = nr["node_id"]
            nt_row = conn.execute(
                "SELECT status, epoch FROM node_trust WHERE node_id = ? AND relationship_id = ?;",
                (nid, rel.id),
            ).fetchone()

            if nt_row is not None:
                local_epoch: Optional[int] = nt_row["epoch"]
                local_status: Optional[str] = nt_row["status"]
                # Converged means: epoch matches authority AND status implies same decision
                # A node is converged when its epoch == authority epoch, or when it's BLOCKED
                # for a REVOKED relationship (the status matches authority intent)
                if rel.status.value == "REVOKED":
                    converged = local_status == "BLOCKED" and local_epoch == rel.epoch
                else:
                    converged = local_epoch == rel.epoch
            else:
                local_epoch = None
                local_status = None
                converged = False  # Node holds no trust at all — not converged

            if not converged:
                all_converged = False

            node_entries.append(
                ConvergenceNodeEntry(
                    node_id=nid,
                    local_epoch=local_epoch,
                    local_status=local_status,
                    authority_epoch=rel.epoch,
                    authority_status=rel.status.value,
                    converged=converged,
                )
            )

    return ConvergenceResponse(
        relationship_id=rel.id,
        authority_epoch=rel.epoch,
        authority_status=rel.status.value,
        all_converged=all_converged,
        nodes=node_entries,
    )

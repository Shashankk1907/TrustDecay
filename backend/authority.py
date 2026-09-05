"""Authority trust management logic."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from backend.db import log_event
from backend.models import TrustRelationship, TrustStatus, NodeTrustStatus, ConnectivityState, LifecycleState


def get_or_init_authority_epoch(conn: sqlite3.Connection) -> int:
    """Read the authority global_epoch, initializing to 1 if not present."""
    cursor = conn.cursor()
    cursor.execute("SELECT global_epoch FROM authority_state WHERE id = 1;")
    row = cursor.fetchone()
    if row is not None:
        return int(row["global_epoch"])
    
    conn.execute("INSERT OR REPLACE INTO authority_state (id, global_epoch) VALUES (1, 1);")
    return 1


def get_trust_relationship(conn: sqlite3.Connection, relationship_id: str) -> Optional[TrustRelationship]:
    """Fetch trust relationship by ID."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, status, epoch FROM trust_relationship WHERE id = ?;",
        (relationship_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return TrustRelationship(
        id=row["id"],
        status=TrustStatus(row["status"]),
        epoch=row["epoch"],
    )


def create_trust_relationship(
    conn: sqlite3.Connection,
    relationship_id: str,
    ts: Optional[str] = None,
) -> TrustRelationship:
    """Create or retrieve a canonical trust relationship at the current global epoch."""
    current_epoch = get_or_init_authority_epoch(conn)
    existing = get_trust_relationship(conn, relationship_id)
    if existing is not None:
        return existing

    conn.execute(
        "INSERT INTO trust_relationship (id, status, epoch) VALUES (?, ?, ?);",
        (relationship_id, TrustStatus.ACTIVE.value, current_epoch),
    )
    log_event(conn, f"Trust relationship '{relationship_id}' created at epoch {current_epoch}", ts=ts)
    return TrustRelationship(
        id=relationship_id,
        status=TrustStatus.ACTIVE,
        epoch=current_epoch,
    )


def grant_trust(
    conn: sqlite3.Connection,
    relationship_id: str,
    node_id: str,
    ts: Optional[str] = None,
) -> dict:
    """Grant trust relationship directly from AUTHORITY to a node."""
    rel = get_trust_relationship(conn, relationship_id)
    if rel is None:
        rel = create_trust_relationship(conn, relationship_id, ts=ts)

    if rel.status == TrustStatus.REVOKED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot grant revoked trust relationship '{relationship_id}'",
        )

    # Check existing node_trust for monotonicity and idempotency
    cursor = conn.cursor()
    cursor.execute(
        "SELECT epoch, status, source_node FROM node_trust WHERE node_id = ? AND relationship_id = ?;",
        (node_id, relationship_id),
    )
    existing_trust = cursor.fetchone()

    # I1 Monotonicity: a node/authority never accepts incoming_epoch < local_epoch
    if existing_trust is not None and rel.epoch < existing_trust["epoch"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot grant trust at epoch {rel.epoch} because node '{node_id}' "
                f"already observed newer epoch {existing_trust['epoch']}"
            ),
        )  # I1

    # Idempotency: if already TRUSTED at current epoch from AUTHORITY, do not create duplicate edge
    if (
        existing_trust is not None
        and existing_trust["status"] == NodeTrustStatus.TRUSTED.value
        and existing_trust["epoch"] == rel.epoch
        and existing_trust["source_node"] == "AUTHORITY"
    ):
        return {
            "status": "granted",
            "relationship_id": relationship_id,
            "node_id": node_id,
            "epoch": rel.epoch,
            "source": "AUTHORITY",
        }

    # Ensure node state row exists (default ONLINE / READY)
    cursor.execute("SELECT node_id FROM node_state WHERE node_id = ?;", (node_id,))
    if cursor.fetchone() is None:
        conn.execute(
            "INSERT INTO node_state (node_id, connectivity, lifecycle, last_reconciled_epoch) VALUES (?, ?, ?, ?);",
            (node_id, ConnectivityState.ONLINE.value, LifecycleState.READY.value, 0),
        )

    # Write node_trust row
    conn.execute(
        """
        INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(node_id, relationship_id) DO UPDATE SET
            status = excluded.status,
            epoch = excluded.epoch,
            source_node = excluded.source_node;
        """,
        (node_id, relationship_id, NodeTrustStatus.TRUSTED.value, rel.epoch, "AUTHORITY"),
    )

    # Append propagation edge
    edge_ts = ts if ts is not None else datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO propagation_edge (source_node, destination_node, relationship_id, epoch, ts)
        VALUES (?, ?, ?, ?, ?);
        """,
        ("AUTHORITY", node_id, relationship_id, rel.epoch, edge_ts),
    )

    log_event(conn, f"Authority granted '{relationship_id}' (epoch {rel.epoch}) to node '{node_id}'", ts=ts)
    return {
        "status": "granted",
        "relationship_id": relationship_id,
        "node_id": node_id,
        "epoch": rel.epoch,
        "source": "AUTHORITY",
    }

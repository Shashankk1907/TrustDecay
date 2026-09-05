"""Node trust and propagation logic."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from backend.db import log_event
from backend.models import NodeTrust, NodeTrustStatus, NodeState, ConnectivityState, LifecycleState
from backend.authority import get_trust_relationship


def get_node_trust(conn: sqlite3.Connection, node_id: str, relationship_id: str) -> Optional[NodeTrust]:
    """Retrieve the cached trust observation for a specific node and relationship (# I7)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT node_id, relationship_id, status, epoch, source_node
        FROM node_trust
        WHERE node_id = ? AND relationship_id = ?;
        """,
        (node_id, relationship_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return NodeTrust(
        node_id=row["node_id"],
        relationship_id=row["relationship_id"],
        status=NodeTrustStatus(row["status"]),
        epoch=row["epoch"],
        source_node=row["source_node"],
    )


def get_node_state(conn: sqlite3.Connection, node_id: str) -> Optional[NodeState]:
    """Retrieve the connectivity and lifecycle state of a node."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT node_id, connectivity, lifecycle, last_reconciled_epoch
        FROM node_state
        WHERE node_id = ?;
        """,
        (node_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return NodeState(
        node_id=row["node_id"],
        connectivity=ConnectivityState(row["connectivity"]),
        lifecycle=LifecycleState(row["lifecycle"]),
        last_reconciled_epoch=row["last_reconciled_epoch"],
    )


def ensure_node_state(
    conn: sqlite3.Connection,
    node_id: str,
    connectivity: ConnectivityState = ConnectivityState.ONLINE,
    lifecycle: LifecycleState = LifecycleState.READY,
) -> None:
    """Ensure a node exists in node_state."""
    cursor = conn.cursor()
    cursor.execute("SELECT node_id FROM node_state WHERE node_id = ?;", (node_id,))
    if cursor.fetchone() is None:
        conn.execute(
            """
            INSERT INTO node_state (node_id, connectivity, lifecycle, last_reconciled_epoch)
            VALUES (?, ?, ?, 0);
            """,
            (node_id, connectivity.value, lifecycle.value),
        )


def propagate_trust(
    conn: sqlite3.Connection,
    source_node: str,
    destination_node: str,
    relationship_id: str,
    ts: Optional[str] = None,
) -> dict:
    """Propagate a cached trust relationship from source_node to destination_node."""
    # I7 Entity isolation: fetch source trust strictly scoped by relationship_id
    source_trust = get_node_trust(conn, source_node, relationship_id)
    if source_trust is None:
        raise HTTPException(
            status_code=404,
            detail=f"Node '{source_node}' does not hold trust relationship '{relationship_id}'",
        )

    # I3 No stale propagate: a node whose local status is BLOCKED must reject any attempt to propagate
    if source_trust.status == NodeTrustStatus.BLOCKED:
        raise HTTPException(
            status_code=409,
            detail=f"Node '{source_node}' is BLOCKED for '{relationship_id}' and cannot propagate",
        )

    # I1 Monotonicity: a node never accepts incoming_epoch < local_epoch
    dest_trust = get_node_trust(conn, destination_node, relationship_id)
    if dest_trust is not None and source_trust.epoch < dest_trust.epoch:
        raise HTTPException(
            status_code=400,
            detail=f"Incoming epoch {source_trust.epoch} is older than local epoch {dest_trust.epoch}",
        )

    # Idempotency: if destination already has TRUSTED at same epoch from same source, do not duplicate edge
    if (
        dest_trust is not None
        and dest_trust.status == NodeTrustStatus.TRUSTED
        and dest_trust.epoch == source_trust.epoch
        and dest_trust.source_node == source_node
    ):
        return {
            "status": "propagated",
            "relationship_id": relationship_id,
            "from_node": source_node,
            "to_node": destination_node,
            "epoch": source_trust.epoch,
        }

    # Ensure destination node state exists
    ensure_node_state(conn, destination_node)

    # Write node_trust row for destination_node
    conn.execute(
        """
        INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(node_id, relationship_id) DO UPDATE SET
            status = excluded.status,
            epoch = excluded.epoch,
            source_node = excluded.source_node;
        """,
        (
            destination_node,
            relationship_id,
            NodeTrustStatus.TRUSTED.value,
            source_trust.epoch,
            source_node,
        ),
    )

    # Record propagation edge
    edge_ts = ts if ts is not None else datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO propagation_edge (source_node, destination_node, relationship_id, epoch, ts)
        VALUES (?, ?, ?, ?, ?);
        """,
        (source_node, destination_node, relationship_id, source_trust.epoch, edge_ts),
    )

    log_event(
        conn,
        f"Node '{source_node}' propagated '{relationship_id}' (epoch {source_trust.epoch}) to node '{destination_node}'",
        ts=ts,
    )

    return {
        "status": "propagated",
        "relationship_id": relationship_id,
        "from_node": source_node,
        "to_node": destination_node,
        "epoch": source_trust.epoch,
    }


def authorize(conn: sqlite3.Connection, node_id: str, relationship_id: str) -> dict:
    """Authorize a node for a given relationship per architecture.md §10 pseudocode.

    Decision chain (fail-safe — every step defaults to DENY):
    1. Load node state — 404 if node unknown.
    2. I6: If lifecycle == RECONCILING, DENY (fail-closed gate).
    3. Load node_trust — DENY if node doesn't hold this relationship.
    4. Load authoritative trust_relationship — DENY if relationship unknown.
    5. If local status != TRUSTED, DENY (locally blocked).
    6. I2: If local epoch < authoritative epoch, DENY (stale epoch).
    7. Otherwise ALLOW.

    All queries scoped by relationship_id (I7 — entity isolation).
    """
    # Step 1: Load node state
    node = get_node_state(conn, node_id)
    if node is None:
        raise HTTPException(
            status_code=404,
            detail=f"Node '{node_id}' not found",
        )

    # I6: Fail-closed gate — RECONCILING node denies ALL authorization
    if node.lifecycle == LifecycleState.RECONCILING:  # I6
        log_event(conn, f"Authorize DENY: node '{node_id}' is RECONCILING for '{relationship_id}'")
        return {
            "decision": "DENY",
            "node_id": node_id,
            "relationship_id": relationship_id,
            "reason": "node is reconciling",
        }

    # Step 3: Load node_trust (I7 — scoped by relationship_id)
    nt = get_node_trust(conn, node_id, relationship_id)
    if nt is None:
        log_event(conn, f"Authorize DENY: node '{node_id}' has no trust for '{relationship_id}'")
        return {
            "decision": "DENY",
            "node_id": node_id,
            "relationship_id": relationship_id,
            "reason": "no trust relationship held",
        }

    # Step 4: Load authoritative relationship
    rel = get_trust_relationship(conn, relationship_id)
    if rel is None:
        log_event(conn, f"Authorize DENY: trust relationship '{relationship_id}' not found")
        return {
            "decision": "DENY",
            "node_id": node_id,
            "relationship_id": relationship_id,
            "reason": "trust relationship not found at authority",
        }

    # Step 5: If local status is not TRUSTED, deny
    if nt.status != NodeTrustStatus.TRUSTED:
        log_event(
            conn,
            f"Authorize DENY: node '{node_id}' is locally {nt.status.value} for '{relationship_id}'",
        )
        return {
            "decision": "DENY",
            "node_id": node_id,
            "relationship_id": relationship_id,
            "reason": "locally blocked",
        }

    # I2: Revocation dominance — stale epoch means cached trust is invalid
    if nt.epoch < rel.epoch:  # I2
        log_event(
            conn,
            f"Authorize DENY: node '{node_id}' has stale epoch {nt.epoch} < {rel.epoch} for '{relationship_id}'",
        )
        return {
            "decision": "DENY",
            "node_id": node_id,
            "relationship_id": relationship_id,
            "reason": "stale epoch",
        }

    # Step 7: All checks passed — ALLOW
    log_event(conn, f"Authorize ALLOW: node '{node_id}' for '{relationship_id}'")
    return {
        "decision": "ALLOW",
        "node_id": node_id,
        "relationship_id": relationship_id,
        "reason": "",
    }

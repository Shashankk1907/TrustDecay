"""Authority trust management logic."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from backend.db import log_event
from backend.models import TrustRelationship, TrustStatus, NodeTrustStatus, ConnectivityState, LifecycleState
from backend.footprint import compute_footprint


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


def revoke_trust(conn: sqlite3.Connection, relationship_id: str) -> dict:
    """Revoke a trust relationship per architecture.md pseudocode.

    1. Read relationship — 404 if not found.
    2. I5: if already REVOKED, return as idempotent no-op.
    3. Increment authority global_epoch.
    4. Mark relationship REVOKED at new epoch.
    5. Compute footprint (direct + indirect).
    6. Push BLOCKED to reachable ONLINE nodes only.
    7. Never modify an OFFLINE node's cache.
    8. Log event.
    """
    rel = get_trust_relationship(conn, relationship_id)
    if rel is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trust relationship '{relationship_id}' not found",
        )

    # I5: Idempotent revoke — revoking an already-REVOKED relationship is a no-op
    if rel.status == TrustStatus.REVOKED:  # I5
        footprint = compute_footprint(conn, relationship_id)
        return {
            "relationship_id": relationship_id,
            "status": rel.status.value,
            "epoch": rel.epoch,
            "footprint_direct": footprint["direct"],
            "footprint_indirect": footprint["indirect"],
            "blocked_nodes": [],
            "skipped_offline_nodes": [],
        }

    # Step 3: Increment authority global epoch
    current_epoch = get_or_init_authority_epoch(conn)
    new_epoch = current_epoch + 1
    conn.execute(
        "UPDATE authority_state SET global_epoch = ? WHERE id = 1;",
        (new_epoch,),
    )

    # Step 4: Mark relationship REVOKED at new epoch
    conn.execute(
        "UPDATE trust_relationship SET status = ?, epoch = ? WHERE id = ?;",
        (TrustStatus.REVOKED.value, new_epoch, relationship_id),
    )

    # Step 5: Compute footprint via BFS
    footprint = compute_footprint(conn, relationship_id)
    all_nodes = footprint["direct"] + footprint["indirect"]

    # Step 6-7: Push BLOCKED to ONLINE nodes, skip OFFLINE nodes
    blocked_nodes: list[str] = []
    skipped_offline_nodes: list[str] = []

    for node_id in all_nodes:
        # Fetch node connectivity state
        cursor = conn.cursor()
        cursor.execute(
            "SELECT connectivity FROM node_state WHERE node_id = ?;",
            (node_id,),
        )
        node_row = cursor.fetchone()
        if node_row is None:
            # Node state missing — skip gracefully
            continue

        connectivity = node_row["connectivity"]

        # Never modify an OFFLINE node's cache
        if connectivity == ConnectivityState.OFFLINE.value:
            skipped_offline_nodes.append(node_id)
            continue

        # Fetch node_trust for this relationship (# I7 — scoped by relationship_id)
        cursor.execute(
            "SELECT epoch, status FROM node_trust WHERE node_id = ? AND relationship_id = ?;",
            (node_id, relationship_id),
        )
        nt_row = cursor.fetchone()
        if nt_row is None:
            # No cached trust for this node — skip
            continue

        local_epoch = nt_row["epoch"]
        local_status = nt_row["status"]

        # I1: Normal case — node's epoch < revocation epoch, update both status and epoch
        if local_epoch < new_epoch:  # I1
            conn.execute(
                """
                UPDATE node_trust SET status = ?, epoch = ?
                WHERE node_id = ? AND relationship_id = ?;
                """,
                (NodeTrustStatus.BLOCKED.value, new_epoch, node_id, relationship_id),
            )
            blocked_nodes.append(node_id)
        elif local_status == NodeTrustStatus.TRUSTED.value:
            # Defensive: node has anomalous epoch >= revocation epoch but is still
            # TRUSTED for a relationship the authority has definitively revoked.
            # Force-block to ensure revoked trust cannot be used. Preserve the
            # node's existing epoch to avoid lowering it (respecting I1 on the
            # epoch dimension).
            conn.execute(
                """
                UPDATE node_trust SET status = ?
                WHERE node_id = ? AND relationship_id = ?;
                """,
                (NodeTrustStatus.BLOCKED.value, node_id, relationship_id),
            )
            blocked_nodes.append(node_id)

    # Step 8: Log event
    all_footprint = sorted(set(footprint["direct"] + footprint["indirect"]))
    log_event(
        conn,
        f"{relationship_id} revoked at epoch {new_epoch}; footprint={all_footprint}",
    )

    return {
        "relationship_id": relationship_id,
        "status": TrustStatus.REVOKED.value,
        "epoch": new_epoch,
        "footprint_direct": footprint["direct"],
        "footprint_indirect": footprint["indirect"],
        "blocked_nodes": sorted(blocked_nodes),
        "skipped_offline_nodes": sorted(skipped_offline_nodes),
    }

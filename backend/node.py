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


def disconnect_node(conn: sqlite3.Connection, node_id: str) -> dict:
    """Set node connectivity to OFFLINE. node_trust cache is NOT modified (frozen).

    Per architecture.md §7:
        disconnect() → connectivity = OFFLINE (node_trust untouched, frozen)
    """
    node = get_node_state(conn, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    conn.execute(
        "UPDATE node_state SET connectivity = ? WHERE node_id = ?;",
        (ConnectivityState.OFFLINE.value, node_id),
    )
    log_event(conn, f"Node '{node_id}' disconnected (went OFFLINE)")
    return {"node_id": node_id, "connectivity": ConnectivityState.OFFLINE.value}


def reconcile(conn: sqlite3.Connection, node_id: str) -> dict:
    """Reconcile a node's local trust cache against the authority.

    Per architecture.md §10 and phase 5 spec:

    1. Set lifecycle = RECONCILING immediately (fail-closed gate is now active).
    2. For every relationship held by the node:
           if authority.epoch > local.epoch:  # I4
               overwrite local status and epoch
    3. Set lifecycle = READY only after reconciliation completes.
    4. Update last_reconciled_epoch.
    5. Log the reconciliation event.

    Hard rule: NEVER set READY before reconciliation completes (I4 / §7).
    This function is used both on reconnect AND on process startup.
    """
    node = get_node_state(conn, node_id)
    if node is None:
        # On startup, nodes may not have a row yet — skip gracefully
        return {"node_id": node_id, "reconciled_relationships": 0}

    # Step 1: Set lifecycle = RECONCILING immediately — fail-closed gate active # I6
    conn.execute(
        "UPDATE node_state SET lifecycle = ? WHERE node_id = ?;",
        (LifecycleState.RECONCILING.value, node_id),
    )

    # Step 2: Load all trust rows for this node (I7 — each scoped by relationship_id)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT relationship_id, status, epoch FROM node_trust WHERE node_id = ?;",
        (node_id,),
    )
    trust_rows = cursor.fetchall()

    reconciled_count = 0
    for row in trust_rows:
        rel_id = row["relationship_id"]
        local_epoch = row["epoch"]

        rel = get_trust_relationship(conn, rel_id)
        if rel is None:
            # Relationship no longer exists at authority — skip
            continue

        # I4 Reconcile dominance: overwrite only when authority epoch is newer
        if rel.epoch > local_epoch:  # I4
            new_status = (
                NodeTrustStatus.BLOCKED.value
                if rel.status.value == "REVOKED"
                else NodeTrustStatus.TRUSTED.value
            )
            conn.execute(
                """
                UPDATE node_trust SET status = ?, epoch = ?
                WHERE node_id = ? AND relationship_id = ?;
                """,
                (new_status, rel.epoch, node_id, rel_id),
            )
            reconciled_count += 1

    # Step 3: Read the current authority global epoch for last_reconciled_epoch
    epoch_row = conn.execute(
        "SELECT global_epoch FROM authority_state WHERE id = 1;"
    ).fetchone()
    current_epoch = epoch_row["global_epoch"] if epoch_row is not None else 0

    # Step 4: Set lifecycle = READY and update last_reconciled_epoch
    conn.execute(
        "UPDATE node_state SET lifecycle = ?, last_reconciled_epoch = ? WHERE node_id = ?;",
        (LifecycleState.READY.value, current_epoch, node_id),
    )

    # Step 5: Log event
    log_event(conn, f"Node '{node_id}' reconciled to epoch {current_epoch}")

    return {
        "node_id": node_id,
        "reconciled_relationships": reconciled_count,
        "last_reconciled_epoch": current_epoch,
    }


def reconnect_node(conn: sqlite3.Connection, node_id: str) -> dict:
    """Reconnect a node: OFFLINE → ONLINE+RECONCILING → reconcile() → READY.

    Per architecture.md §7 transition rule:
        reconnect() → connectivity = ONLINE, lifecycle = RECONCILING
                    → [reconcile algorithm]
                    → lifecycle = READY

    Hard rule: Never OFFLINE → READY → reconcile. Always OFFLINE → RECONCILING → READY.
    """
    node = get_node_state(conn, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    # Set connectivity = ONLINE first, lifecycle transition is handled inside reconcile()
    conn.execute(
        "UPDATE node_state SET connectivity = ? WHERE node_id = ?;",
        (ConnectivityState.ONLINE.value, node_id),
    )
    log_event(conn, f"Node '{node_id}' reconnected (back ONLINE, entering RECONCILING)")

    # reconcile() sets RECONCILING → does work → sets READY
    result = reconcile(conn, node_id)

    return {
        "node_id": node_id,
        "connectivity": ConnectivityState.ONLINE.value,
        "lifecycle": LifecycleState.READY.value,
        **result,
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

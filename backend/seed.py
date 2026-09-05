"""Deterministic seed graph initialization for TrustDecay."""

import sqlite3
from backend.db import wipe_db, log_event
from backend.authority import create_trust_relationship, grant_trust
from backend.node import propagate_trust, ensure_node_state
from backend.models import ConnectivityState, LifecycleState

SEED_TIMESTAMP = "2026-01-01T00:00:00Z"


def seed_database(conn: sqlite3.Connection) -> None:
    """Wipe all tables and build the canonical starting graph per architecture.md §8."""
    wipe_db(conn)

    # Monotonic authority epoch starts at 1
    conn.execute("INSERT INTO authority_state (id, global_epoch) VALUES (1, 1);")

    # Initial node states: A, B, C, D are ONLINE and READY
    for node_id in ["A", "B", "C", "D"]:
        ensure_node_state(
            conn,
            node_id=node_id,
            connectivity=ConnectivityState.ONLINE,
            lifecycle=LifecycleState.READY,
        )

    # Entities: "alice" and "bob"
    create_trust_relationship(conn, "alice", ts=SEED_TIMESTAMP)
    create_trust_relationship(conn, "bob", ts=SEED_TIMESTAMP)

    # Authority grants
    # Authority --grant(alice, epoch=1)--> A (direct)
    grant_trust(conn, relationship_id="alice", node_id="A", ts=SEED_TIMESTAMP)

    # A --propagate(alice, epoch=1)--> C (indirect: C.source_node = "A")
    propagate_trust(conn, source_node="A", destination_node="C", relationship_id="alice", ts=SEED_TIMESTAMP)

    # Authority --grant(alice, epoch=1)--> B (direct)
    grant_trust(conn, relationship_id="alice", node_id="B", ts=SEED_TIMESTAMP)

    # Authority --grant(alice, epoch=1)--> D (direct)
    grant_trust(conn, relationship_id="alice", node_id="D", ts=SEED_TIMESTAMP)

    # Authority --grant(bob, epoch=1)--> B (unrelated control relationship)
    grant_trust(conn, relationship_id="bob", node_id="B", ts=SEED_TIMESTAMP)

    log_event(conn, "Demo seed graph initialized successfully", ts=SEED_TIMESTAMP)

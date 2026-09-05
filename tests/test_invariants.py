"""Invariants test suite for TrustDecay."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.db import init_db, get_db_connection


def test_schema_initialization_and_idempotency(tmp_path):
    """Verify that all 6 tables exist and schema init is idempotent."""
    db_file = tmp_path / "test_trustdecay.db"
    
    # Run twice to test idempotency
    init_db(db_file)
    init_db(db_file)
    
    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = {row["name"] for row in cursor.fetchall()}
    conn.close()
    
    expected_tables = {
        "trust_relationship",
        "authority_state",
        "node_trust",
        "propagation_edge",
        "node_state",
        "event_log",
    }
    assert tables == expected_tables, f"Expected tables {expected_tables}, got {tables}"


def test_health_check():
    """Verify the health check endpoint responds with 200 OK."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_trust_creation_grant_and_propagation(tmp_path, monkeypatch):
    """Test Phase 2 focused scenario: create trust, grant to A, propagate A -> C."""
    db_file = tmp_path / "phase2_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)

    # 1. Create trust "alice"
    res = client.post("/trust", json={"relationship_id": "alice"})
    assert res.status_code == 200
    rel = res.json()
    assert rel["id"] == "alice"
    assert rel["status"] == "ACTIVE"
    assert rel["epoch"] == 1

    # 2. Grant "alice" to node A
    res = client.post("/trust/alice/grant", json={"node_id": "A"})
    assert res.status_code == 200
    assert res.json()["status"] == "granted"

    # 3. Propagate A -> C
    res = client.post("/nodes/A/propagate", json={"relationship_id": "alice", "to_node": "C"})
    assert res.status_code == 200
    assert res.json()["status"] == "propagated"
    assert res.json()["to_node"] == "C"

    # 4. Verify database: exactly 2 propagation edges (AUTHORITY -> A, A -> C)
    conn = get_db_connection(db_file)
    edges = conn.execute(
        "SELECT source_node, destination_node, relationship_id, epoch FROM propagation_edge ORDER BY id ASC;"
    ).fetchall()
    assert len(edges) == 2
    assert edges[0]["source_node"] == "AUTHORITY"
    assert edges[0]["destination_node"] == "A"
    assert edges[0]["relationship_id"] == "alice"
    assert edges[1]["source_node"] == "A"
    assert edges[1]["destination_node"] == "C"
    assert edges[1]["relationship_id"] == "alice"

    # Verify node_trust for C points to source_node A
    c_trust = conn.execute(
        "SELECT node_id, relationship_id, status, epoch, source_node FROM node_trust WHERE node_id = 'C';"
    ).fetchone()
    assert c_trust is not None
    assert c_trust["source_node"] == "A"
    assert c_trust["status"] == "TRUSTED"
    conn.close()


def test_invariant_i3_blocked_node_cannot_propagate(tmp_path, monkeypatch):
    """Verify I3: a node whose local status is BLOCKED must reject any attempt to propagate (HTTP 409)."""
    db_file = tmp_path / "i3_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO authority_state (id, global_epoch) VALUES (1, 1);")
    conn.execute("INSERT INTO trust_relationship (id, status, epoch) VALUES ('alice', 'ACTIVE', 1);")
    conn.execute("INSERT INTO node_state (node_id, connectivity, lifecycle) VALUES ('A', 'ONLINE', 'READY');")
    conn.execute(
        "INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node) VALUES ('A', 'alice', 'BLOCKED', 1, 'AUTHORITY');"
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    res = client.post("/nodes/A/propagate", json={"relationship_id": "alice", "to_node": "C"})
    assert res.status_code == 409
    assert "BLOCKED" in res.json()["detail"]


def test_invariant_i1_monotonicity(tmp_path, monkeypatch):
    """Verify I1: a node never accepts incoming_epoch < local_epoch (HTTP 400)."""
    db_file = tmp_path / "i1_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO authority_state (id, global_epoch) VALUES (1, 1);")
    conn.execute("INSERT INTO trust_relationship (id, status, epoch) VALUES ('alice', 'ACTIVE', 1);")
    # A has epoch 1
    conn.execute(
        "INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node) VALUES ('A', 'alice', 'TRUSTED', 1, 'AUTHORITY');"
    )
    # C already observed epoch 2
    conn.execute(
        "INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node) VALUES ('C', 'alice', 'TRUSTED', 2, 'AUTHORITY');"
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    res = client.post("/nodes/A/propagate", json={"relationship_id": "alice", "to_node": "C"})
    assert res.status_code == 400
    assert "older than local epoch" in res.json()["detail"]


def test_demo_reset_reproducible_seed_graph(tmp_path, monkeypatch):
    """Verify POST /demo/reset builds the exact canonical seed graph deterministically and byte-for-byte."""
    db_file = tmp_path / "reset_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)

    snapshots = []
    # Call reset twice to ensure identical snapshot
    for _ in range(2):
        res = client.post("/demo/reset")
        assert res.status_code == 200
        assert res.json()["status"] == "reset complete"

        conn = get_db_connection(db_file)
        # Snapshot all tables
        snap = {
            "authority_state": [dict(r) for r in conn.execute("SELECT * FROM authority_state ORDER BY id;").fetchall()],
            "node_state": [dict(r) for r in conn.execute("SELECT * FROM node_state ORDER BY node_id;").fetchall()],
            "trust_relationship": [dict(r) for r in conn.execute("SELECT * FROM trust_relationship ORDER BY id;").fetchall()],
            "node_trust": [dict(r) for r in conn.execute("SELECT * FROM node_trust ORDER BY node_id, relationship_id;").fetchall()],
            "propagation_edge": [dict(r) for r in conn.execute("SELECT * FROM propagation_edge ORDER BY id;").fetchall()],
            "event_log": [dict(r) for r in conn.execute("SELECT * FROM event_log ORDER BY id;").fetchall()],
        }
        snapshots.append(snap)
        conn.close()

    # Compare snapshot 1 and snapshot 2 for byte-for-byte reproducibility
    assert snapshots[0] == snapshots[1]
    assert len(snapshots[0]["propagation_edge"]) == 5
    assert len(snapshots[0]["event_log"]) == 8


def test_grant_cannot_rewind_newer_node_state_i1(tmp_path, monkeypatch):
    """Verify I1: Authority grant cannot rewind a node that already observed a newer epoch."""
    db_file = tmp_path / "grant_i1_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO authority_state (id, global_epoch) VALUES (1, 1);")
    conn.execute("INSERT INTO trust_relationship (id, status, epoch) VALUES ('alice', 'ACTIVE', 1);")
    conn.execute("INSERT INTO node_state (node_id, connectivity, lifecycle) VALUES ('A', 'ONLINE', 'READY');")
    # Node A is at epoch 5 and BLOCKED
    conn.execute(
        "INSERT INTO node_trust (node_id, relationship_id, status, epoch, source_node) VALUES ('A', 'alice', 'BLOCKED', 5, 'AUTHORITY');"
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    # Attempting to grant alice (epoch 1) to A must be rejected by I1
    res = client.post("/trust/alice/grant", json={"node_id": "A"})
    assert res.status_code == 400
    assert "already observed newer epoch" in res.json()["detail"]

    # Verify node A's state was NOT rewound
    conn = get_db_connection(db_file)
    nt = conn.execute("SELECT status, epoch FROM node_trust WHERE node_id = 'A' AND relationship_id = 'alice';").fetchone()
    assert nt["status"] == "BLOCKED"
    assert nt["epoch"] == 5
    conn.close()


def test_idempotent_grant_and_propagate_prevents_duplicate_edges(tmp_path, monkeypatch):
    """Verify that duplicate equal-epoch grants or propagations do not create duplicate edges."""
    db_file = tmp_path / "idempotent_edge_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)

    # 1. Create trust alice
    client.post("/trust", json={"relationship_id": "alice"})

    # 2. Grant alice to A twice
    res1 = client.post("/trust/alice/grant", json={"node_id": "A"})
    assert res1.status_code == 200
    res2 = client.post("/trust/alice/grant", json={"node_id": "A"})
    assert res2.status_code == 200

    conn = get_db_connection(db_file)
    edges = conn.execute("SELECT * FROM propagation_edge;").fetchall()
    assert len(edges) == 1, f"Expected 1 edge after duplicate grant, got {len(edges)}"
    conn.close()

    # 3. Propagate A -> C twice
    res3 = client.post("/nodes/A/propagate", json={"relationship_id": "alice", "to_node": "C"})
    assert res3.status_code == 200
    res4 = client.post("/nodes/A/propagate", json={"relationship_id": "alice", "to_node": "C"})
    assert res4.status_code == 200

    conn = get_db_connection(db_file)
    edges = conn.execute("SELECT * FROM propagation_edge;").fetchall()
    assert len(edges) == 2, f"Expected 2 edges after duplicate propagate, got {len(edges)}"
    conn.close()


@pytest.mark.skip(reason="Phase 8 test: direct stale blocked")
def test_direct_stale_blocked():
    pass


@pytest.mark.skip(reason="Phase 8 test: indirect footprint found")
def test_indirect_footprint_found():
    pass


@pytest.mark.skip(reason="Phase 8 test: offline then reconnect deny")
def test_offline_then_reconnect_deny():
    pass


@pytest.mark.skip(reason="Phase 8 test: unrelated trust untouched")
def test_unrelated_trust_untouched():
    pass

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



# ===========================================================================
# Phase 3 Tests — Footprint BFS + Revocation + Push Containment
# ===========================================================================


def test_footprint_bfs_direct_and_indirect(tmp_path, monkeypatch):
    """Verify BFS footprint returns correct direct and indirect classification per seed graph."""
    db_file = tmp_path / "footprint_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.get("/footprint/alice")
    assert res.status_code == 200
    data = res.json()
    assert data["relationship_id"] == "alice"
    assert data["direct"] == ["A", "B", "D"]
    assert data["indirect"] == ["C"]


def test_footprint_bob_only_direct(tmp_path, monkeypatch):
    """Verify bob's footprint only shows B as direct, no indirect nodes."""
    db_file = tmp_path / "footprint_bob_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.get("/footprint/bob")
    assert res.status_code == 200
    data = res.json()
    assert data["direct"] == ["B"]
    assert data["indirect"] == []


def test_revoke_blocks_online_nodes(tmp_path, monkeypatch):
    """Verify revocation pushes BLOCKED to all ONLINE nodes in the footprint."""
    db_file = tmp_path / "revoke_online_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Revoke alice — all nodes start ONLINE so all should be blocked
    res = client.post("/trust/alice/revoke")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "REVOKED"
    assert data["epoch"] == 2
    assert sorted(data["blocked_nodes"]) == ["A", "B", "C", "D"]
    assert data["skipped_offline_nodes"] == []

    # Verify database: all nodes now BLOCKED for alice at epoch 2
    conn = get_db_connection(db_file)
    for node_id in ["A", "B", "C", "D"]:
        nt = conn.execute(
            "SELECT status, epoch FROM node_trust WHERE node_id = ? AND relationship_id = 'alice';",
            (node_id,),
        ).fetchone()
        assert nt["status"] == "BLOCKED", f"Expected {node_id} BLOCKED, got {nt['status']}"
        assert nt["epoch"] == 2, f"Expected {node_id} epoch 2, got {nt['epoch']}"
    conn.close()


def test_revoke_skips_offline_nodes(tmp_path, monkeypatch):
    """Verify revocation does NOT modify node_trust for OFFLINE nodes — cache frozen."""
    db_file = tmp_path / "revoke_offline_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Mark D as OFFLINE before revoking
    conn = get_db_connection(db_file)
    conn.execute("UPDATE node_state SET connectivity = 'OFFLINE' WHERE node_id = 'D';")
    conn.commit()
    conn.close()

    # Revoke alice
    res = client.post("/trust/alice/revoke")
    assert res.status_code == 200
    data = res.json()
    assert "D" in data["skipped_offline_nodes"]
    assert "D" not in data["blocked_nodes"]

    # Verify D's cache is untouched — still TRUSTED at epoch 1
    conn = get_db_connection(db_file)
    nt = conn.execute(
        "SELECT status, epoch FROM node_trust WHERE node_id = 'D' AND relationship_id = 'alice';",
    ).fetchone()
    assert nt["status"] == "TRUSTED", f"OFFLINE node D should remain TRUSTED, got {nt['status']}"
    assert nt["epoch"] == 1, f"OFFLINE node D epoch should remain 1, got {nt['epoch']}"

    # A, B, C should still be BLOCKED
    for node_id in ["A", "B", "C"]:
        nt = conn.execute(
            "SELECT status, epoch FROM node_trust WHERE node_id = ? AND relationship_id = 'alice';",
            (node_id,),
        ).fetchone()
        assert nt["status"] == "BLOCKED"
        assert nt["epoch"] == 2
    conn.close()


def test_revoke_idempotent_i5(tmp_path, monkeypatch):
    """Verify I5: revoking an already-REVOKED relationship is a no-op — no epoch increment."""
    db_file = tmp_path / "revoke_i5_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # First revoke
    res1 = client.post("/trust/alice/revoke")
    assert res1.status_code == 200
    assert res1.json()["epoch"] == 2

    # Second revoke — must be idempotent no-op
    res2 = client.post("/trust/alice/revoke")
    assert res2.status_code == 200
    assert res2.json()["epoch"] == 2  # No increment
    assert res2.json()["blocked_nodes"] == []  # No push activity

    # Verify authority epoch did NOT double-increment
    conn = get_db_connection(db_file)
    epoch_row = conn.execute("SELECT global_epoch FROM authority_state WHERE id = 1;").fetchone()
    assert epoch_row["global_epoch"] == 2, f"Expected epoch 2, got {epoch_row['global_epoch']}"
    conn.close()


def test_footprint_entity_isolation_i7(tmp_path, monkeypatch):
    """Verify I7: revoking alice does not touch bob's node_trust state on B."""
    db_file = tmp_path / "i7_isolation_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Verify bob on B is TRUSTED before revocation
    conn = get_db_connection(db_file)
    bob_before = conn.execute(
        "SELECT status, epoch FROM node_trust WHERE node_id = 'B' AND relationship_id = 'bob';",
    ).fetchone()
    assert bob_before["status"] == "TRUSTED"
    assert bob_before["epoch"] == 1
    conn.close()

    # Revoke alice
    client.post("/trust/alice/revoke")

    # Verify bob on B is STILL TRUSTED — untouched
    conn = get_db_connection(db_file)
    bob_after = conn.execute(
        "SELECT status, epoch FROM node_trust WHERE node_id = 'B' AND relationship_id = 'bob';",
    ).fetchone()
    assert bob_after["status"] == "TRUSTED", f"Bob should be untouched, got {bob_after['status']}"
    assert bob_after["epoch"] == 1, f"Bob epoch should be 1, got {bob_after['epoch']}"
    conn.close()


def test_footprint_404_for_unknown_relationship(tmp_path, monkeypatch):
    """Verify GET /footprint returns 404 for a relationship that doesn't exist."""
    db_file = tmp_path / "footprint_404_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.get("/footprint/nonexistent")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]


def test_revoke_404_for_unknown_relationship(tmp_path, monkeypatch):
    """Verify POST /trust/{id}/revoke returns 404 for a relationship that doesn't exist."""
    db_file = tmp_path / "revoke_404_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.post("/trust/nonexistent/revoke")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]


def test_revoke_event_logged(tmp_path, monkeypatch):
    """Verify revocation produces a human-readable event log entry."""
    db_file = tmp_path / "revoke_event_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")
    client.post("/trust/alice/revoke")

    conn = get_db_connection(db_file)
    events = conn.execute(
        "SELECT message FROM event_log ORDER BY id DESC LIMIT 1;",
    ).fetchone()
    assert "alice revoked at epoch 2" in events["message"]
    assert "footprint=" in events["message"]
    conn.close()


def test_revoke_force_blocks_anomalous_high_epoch_node(tmp_path, monkeypatch):
    """Adversarial: a footprint node with epoch=99 and TRUSTED must still be blocked on revocation.

    If a node somehow has a future epoch (e.g. direct DB edit, bug), the normal
    I1 check (epoch < new_epoch) would skip it, leaving revoked trust usable.
    The defensive guard must force-block it regardless.
    """
    db_file = tmp_path / "adversarial_epoch_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Manually set node A's alice trust to an anomalous high epoch
    conn = get_db_connection(db_file)
    conn.execute(
        "UPDATE node_trust SET epoch = 99 WHERE node_id = 'A' AND relationship_id = 'alice';"
    )
    conn.commit()
    conn.close()

    # Revoke alice — new_epoch will be 2, but A has epoch=99
    res = client.post("/trust/alice/revoke")
    assert res.status_code == 200
    data = res.json()

    # A must be in blocked_nodes despite having epoch 99
    assert "A" in data["blocked_nodes"], (
        f"Node A with anomalous epoch=99 was not blocked. blocked={data['blocked_nodes']}"
    )

    # Verify database: A is BLOCKED (epoch preserved at 99, not lowered)
    conn = get_db_connection(db_file)
    nt = conn.execute(
        "SELECT status, epoch FROM node_trust WHERE node_id = 'A' AND relationship_id = 'alice';",
    ).fetchone()
    assert nt["status"] == "BLOCKED", f"A should be BLOCKED, got {nt['status']}"
    assert nt["epoch"] == 99, f"A's epoch should be preserved at 99, got {nt['epoch']}"
    conn.close()


# ===========================================================================
# Phase 4 Tests — Authorization + Entity Isolation
# ===========================================================================


def test_authorize_allow_trusted_node(tmp_path, monkeypatch):
    """ALLOW: a node with TRUSTED status and matching epoch for an ACTIVE relationship."""
    db_file = tmp_path / "authz_allow_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "ALLOW"
    assert data["node_id"] == "A"
    assert data["relationship_id"] == "alice"


def test_authorize_deny_blocked_node(tmp_path, monkeypatch):
    """DENY: a node whose local status is BLOCKED must be denied (locally blocked)."""
    db_file = tmp_path / "authz_blocked_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Revoke alice — this pushes BLOCKED to A (ONLINE)
    client.post("/trust/alice/revoke")

    res = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 403
    data = res.json()
    assert data["decision"] == "DENY"
    assert "blocked" in data["reason"]


def test_authorize_deny_stale_epoch_i2(tmp_path, monkeypatch):
    """DENY: node holds TRUSTED but epoch < authority epoch — I2 stale epoch."""
    db_file = tmp_path / "authz_stale_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Bump authority epoch (create another trust), then manually set A back to epoch 0
    conn = get_db_connection(db_file)
    # Directly raise the trust_relationship epoch to simulate authority advancement
    conn.execute(
        "UPDATE authority_state SET global_epoch = 5 WHERE id = 1;"
    )
    conn.execute(
        "UPDATE trust_relationship SET epoch = 5 WHERE id = 'alice';"
    )
    # A stays at epoch 1 — now stale
    conn.commit()
    conn.close()

    res = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 403
    data = res.json()
    assert data["decision"] == "DENY"
    assert "stale epoch" in data["reason"]


def test_authorize_deny_reconciling_node_i6(tmp_path, monkeypatch):
    """DENY: a node in RECONCILING lifecycle must be denied regardless of trust cache (I6)."""
    db_file = tmp_path / "authz_reconciling_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Force node A into RECONCILING state
    conn = get_db_connection(db_file)
    conn.execute("UPDATE node_state SET lifecycle = 'RECONCILING' WHERE node_id = 'A';")
    conn.commit()
    conn.close()

    # A still has TRUSTED alice in cache — but I6 must deny
    res = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 403
    data = res.json()
    assert data["decision"] == "DENY"
    assert "reconciling" in data["reason"]


def test_authorize_deny_no_trust_held(tmp_path, monkeypatch):
    """DENY: node has no cached trust for this relationship at all."""
    db_file = tmp_path / "authz_no_trust_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # C does not hold bob — only alice (indirect via A)
    res = client.post("/nodes/C/authorize", json={"relationship_id": "bob"})
    assert res.status_code == 403
    data = res.json()
    assert data["decision"] == "DENY"
    assert "no trust relationship held" in data["reason"]


def test_authorize_deny_unknown_node(tmp_path, monkeypatch):
    """DENY (404): authorizing an entirely unknown node returns 404."""
    db_file = tmp_path / "authz_unknown_node_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    res = client.post("/nodes/UNKNOWN/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 404


def test_authorize_deny_revoked_relationship_i2(tmp_path, monkeypatch):
    """DENY: after revocation the authority epoch advances; node is either BLOCKED or stale — both deny."""
    db_file = tmp_path / "authz_revoked_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")
    client.post("/trust/alice/revoke")

    # C is indirect — should also be BLOCKED after revocation
    res = client.post("/nodes/C/authorize", json={"relationship_id": "alice"})
    assert res.status_code == 403
    assert res.json()["decision"] == "DENY"


# ---------------------------------------------------------------------------
# §12 Required tests — these MUST pass for the project to ship
# ---------------------------------------------------------------------------


def test_direct_stale_blocked(tmp_path, monkeypatch):
    """§12 Required: grant alice to A, revoke alice → A authorize = DENY."""
    db_file = tmp_path / "req_direct_stale.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)

    # Build the scenario from scratch (not using seed so the test is self-contained)
    client.post("/trust", json={"relationship_id": "alice"})
    client.post("/trust/alice/grant", json={"node_id": "A"})

    # A holds TRUSTED alice — authorize should ALLOW before revocation
    res_before = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res_before.status_code == 200
    assert res_before.json()["decision"] == "ALLOW"

    # Revoke alice
    client.post("/trust/alice/revoke")

    # Now A must DENY — it was push-blocked during revocation
    res_after = client.post("/nodes/A/authorize", json={"relationship_id": "alice"})
    assert res_after.status_code == 403
    assert res_after.json()["decision"] == "DENY"


def test_unrelated_trust_untouched(tmp_path, monkeypatch):
    """§12 Required: revoke alice → authorize(bob on B) = ALLOW throughout."""
    db_file = tmp_path / "req_unrelated_untouched.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    init_db(db_file)

    client = TestClient(app)
    client.post("/demo/reset")

    # Before revocation: B should ALLOW for both alice and bob
    res_alice_before = client.post("/nodes/B/authorize", json={"relationship_id": "alice"})
    assert res_alice_before.status_code == 200
    assert res_alice_before.json()["decision"] == "ALLOW"

    res_bob_before = client.post("/nodes/B/authorize", json={"relationship_id": "bob"})
    assert res_bob_before.status_code == 200
    assert res_bob_before.json()["decision"] == "ALLOW"

    # Revoke alice
    client.post("/trust/alice/revoke")

    # After revocation: alice on B must DENY (B is in alice's footprint)
    res_alice_after = client.post("/nodes/B/authorize", json={"relationship_id": "alice"})
    assert res_alice_after.status_code == 403
    assert res_alice_after.json()["decision"] == "DENY"

    # Bob on B must remain ALLOW — completely untouched by alice revocation (I7)
    res_bob_after = client.post("/nodes/B/authorize", json={"relationship_id": "bob"})
    assert res_bob_after.status_code == 200
    assert res_bob_after.json()["decision"] == "ALLOW", (
        f"Bob on B should remain ALLOW after alice revocation, got: {res_bob_after.json()}"
    )


# ---------------------------------------------------------------------------
# Phase 8 stubs — offline/reconnect requires Phase 5 (disconnect/reconnect)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Phase 8 test: indirect footprint found — covered partially by Phase 3 tests")
def test_indirect_footprint_found():
    pass


@pytest.mark.skip(reason="Phase 8 test: offline then reconnect deny — requires Phase 5 reconnect endpoint")
def test_offline_then_reconnect_deny():
    pass

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

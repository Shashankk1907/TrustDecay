"""SQLite database connection and schema initialization for TrustDecay."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "trustdecay.db"

SCHEMA_SQL = """
-- One row per trust relationship (e.g. "alice", "bob")
CREATE TABLE IF NOT EXISTS trust_relationship (
    id TEXT PRIMARY KEY,           -- entity id, e.g. "alice"
    status TEXT NOT NULL,          -- 'ACTIVE' | 'REVOKED'
    epoch INTEGER NOT NULL         -- the epoch at which this status was set
);

-- Global monotonic counter, single row, id=1
CREATE TABLE IF NOT EXISTS authority_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    global_epoch INTEGER NOT NULL
);

-- One row per (node, relationship): the node's cached view
CREATE TABLE IF NOT EXISTS node_trust (
    node_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'TRUSTED' | 'BLOCKED'
    epoch INTEGER NOT NULL,        -- epoch this node last observed for this relationship
    source_node TEXT NOT NULL,     -- 'AUTHORITY' or another node_id
    PRIMARY KEY (node_id, relationship_id)
);

-- Append-only propagation lineage — this IS the trust graph
CREATE TABLE IF NOT EXISTS propagation_edge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node TEXT NOT NULL,     -- 'AUTHORITY' or a node_id
    destination_node TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    ts TEXT NOT NULL               -- ISO timestamp
);

-- Node connectivity + reconciliation gate
CREATE TABLE IF NOT EXISTS node_state (
    node_id TEXT PRIMARY KEY,
    connectivity TEXT NOT NULL,    -- 'ONLINE' | 'OFFLINE'
    lifecycle TEXT NOT NULL,       -- 'READY' | 'RECONCILING'
    last_reconciled_epoch INTEGER NOT NULL DEFAULT 0
);

-- Simple append-only event log for the UI timeline
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def get_db_path(custom_path: str | Path | None = None) -> Path:
    """Return the database file path, honoring environment variables or custom overrides."""
    if custom_path is not None:
        return Path(custom_path)
    env_path = os.environ.get("DATABASE_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_db_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Return a new SQLite connection with row factory enabled."""
    path = get_db_path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_db(db_path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections handling commit and rollback."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    """Idempotently initialize all required SQLite tables."""
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)

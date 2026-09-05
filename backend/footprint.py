"""Trust graph BFS footprint discovery logic per architecture.md §10."""

import sqlite3
from typing import TypedDict


class FootprintResult(TypedDict):
    """Typed dict for BFS footprint output."""
    direct: list[str]
    indirect: list[str]


def compute_footprint(conn: sqlite3.Connection, relationship_id: str) -> FootprintResult:
    """Compute the full stale-trust footprint via BFS over propagation_edge.

    Starting point: all AUTHORITY → node edges for the given relationship_id.
    Then traverse source_node → destination_node while filtering by the same
    relationship_id.

    Returns direct (granted by AUTHORITY) and indirect (propagated by another
    node) node IDs, both deterministically sorted.
    """
    cursor = conn.cursor()

    # Step 1: Collect all propagation edges for this relationship_id (# I7)
    cursor.execute(
        """
        SELECT source_node, destination_node
        FROM propagation_edge
        WHERE relationship_id = ?;
        """,
        (relationship_id,),
    )
    edges = cursor.fetchall()

    # Build adjacency list and identify direct grants
    adjacency: dict[str, list[str]] = {}
    direct_set: set[str] = set()

    for edge in edges:
        src = edge["source_node"]
        dst = edge["destination_node"]

        if src == "AUTHORITY":
            direct_set.add(dst)
        
        if src not in adjacency:
            adjacency[src] = []
        adjacency[src].append(dst)

    # Step 2: BFS from direct nodes to discover all reachable nodes
    visited: set[str] = set(direct_set)
    queue: list[str] = list(direct_set)

    while queue:
        node = queue.pop(0)
        children = adjacency.get(node, [])
        for child in children:
            if child not in visited:
                visited.add(child)
                queue.append(child)

    # Step 3: Classify — indirect = visited minus direct
    indirect_set = visited - direct_set

    return FootprintResult(
        direct=sorted(direct_set),
        indirect=sorted(indirect_set),
    )

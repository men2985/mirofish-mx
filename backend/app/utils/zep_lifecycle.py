"""Process-local lifecycle coordination for Zep Cloud graphs.

The lock is intentionally keyed by graph ID so graph deletion/reset and a new
simulation updater claim cannot pass each other between validation and their
Cloud mutation.  It complements (but does not replace) a distributed lock in
multi-worker deployments.
"""

import threading


_graph_locks: dict[str, threading.RLock] = {}
_graph_locks_guard = threading.Lock()


def graph_lifecycle_lock(graph_id: str) -> threading.RLock:
    """Return the process-local re-entrant lifecycle lock for ``graph_id``."""

    if not graph_id:
        raise ValueError("graph_id is required for lifecycle locking")
    with _graph_locks_guard:
        return _graph_locks.setdefault(graph_id, threading.RLock())


def register_graph_reader(graph_id: str, reader_id: str) -> None:
    """Register a long-running read lease (F3: en Postgres, visible para la API y el worker)."""

    if not reader_id:
        raise ValueError("reader_id is required")
    from .db import connect
    with graph_lifecycle_lock(graph_id), connect() as c:
        c.execute("INSERT INTO lectores_grafo (graph_id, reader_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                  (graph_id, reader_id))


def unregister_graph_reader(graph_id: str, reader_id: str) -> None:
    """Release a previously registered graph read lease."""

    from .db import connect
    with graph_lifecycle_lock(graph_id), connect() as c:
        c.execute("DELETE FROM lectores_grafo WHERE graph_id = %s AND reader_id = %s", (graph_id, reader_id))


def get_graph_readers(graph_id: str) -> list[str]:
    """Return active reader IDs while serializing with lifecycle mutations."""

    from .db import connect
    with graph_lifecycle_lock(graph_id), connect() as c:
        rows = c.execute("SELECT reader_id FROM lectores_grafo WHERE graph_id = %s ORDER BY reader_id",
                         (graph_id,)).fetchall()
    return [r["reader_id"] for r in rows]

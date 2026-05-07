import sqlite3
import json
import os
import threading
from datetime import datetime
from core.models import AuditEntry

# Place audit.db at the project root, one level above this file.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audit.db")

# One lock for the whole process -- SQLite in WAL mode can handle readers,
# but writers still need to be serialized from our side to be safe
_write_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Open a connection and make sure the audit table exists."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent_id  TEXT,
            tool_name TEXT NOT NULL,
            params    TEXT NOT NULL,
            allowed   INTEGER NOT NULL,
            reason    TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def log_decision(entry: AuditEntry) -> None:
    """Write one audit entry to the database. Thread-safe."""
    with _write_lock:
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO audit_log (timestamp, agent_id, tool_name, params, allowed, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp.isoformat(),
                    entry.agent_id,
                    entry.tool_name,
                    json.dumps(entry.params),
                    1 if entry.allowed else 0,
                    entry.reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_stats() -> dict:
    """Query the audit log and return dashboard summary statistics."""
    conn = _get_connection()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        total_today = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE timestamp LIKE ?", (f"{today}%",)
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM audit_log WHERE allowed = 0").fetchone()[0]
        block_rate = round((blocked / total) * 100, 1) if total > 0 else 0.0
        row = conn.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM audit_log WHERE allowed = 0 GROUP BY tool_name ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        most_blocked_tool = row["tool_name"] if row else "none"
        row = conn.execute(
            "SELECT agent_id, COUNT(*) as cnt FROM audit_log GROUP BY agent_id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        busiest_agent = row["agent_id"] if (row and row["agent_id"]) else "none"
        rows = conn.execute(
            "SELECT reason, COUNT(*) as cnt FROM audit_log WHERE allowed = 0 GROUP BY reason ORDER BY cnt DESC LIMIT 8"
        ).fetchall()
        blocks_by_rule = [{"reason": r["reason"], "count": r["cnt"]} for r in rows]
        return {
            "total_today": total_today,
            "block_rate": block_rate,
            "most_blocked_tool": most_blocked_tool,
            "busiest_agent": busiest_agent,
            "blocks_by_rule": blocks_by_rule,
        }
    finally:
        conn.close()


def get_recent_logs(limit: int = 20) -> list[dict]:
    """Fetch the most recent log entries, newest first."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "agent_id": row["agent_id"],
                    "tool_name": row["tool_name"],
                    "params": json.loads(row["params"]),
                    "allowed": bool(row["allowed"]),
                    "reason": row["reason"],
                }
            )
        return result
    finally:
        conn.close()

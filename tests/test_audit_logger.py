import sys
import os
import threading
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import audit_logger
from core.models import AuditEntry


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own fresh database file."""
    db_file = str(tmp_path / "test_audit.db")
    monkeypatch.setattr(audit_logger, "DB_PATH", db_file)
    yield db_file


def _make_entry(**kwargs) -> AuditEntry:
    defaults = {
        "timestamp": datetime.now(timezone.utc),
        "tool_name": "check_order",
        "params": {"id": "ORDER-001"},
        "allowed": True,
        "reason": "All conditions passed",
        "agent_id": "test-agent",
    }
    defaults.update(kwargs)
    return AuditEntry(**defaults)


def test_log_and_read_back():
    """Log one entry and confirm it comes back out correctly."""
    entry = _make_entry(tool_name="process_refund", allowed=False, reason="Age too high")
    audit_logger.log_decision(entry)

    logs = audit_logger.get_recent_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["tool_name"] == "process_refund"
    assert logs[0]["allowed"] is False
    assert logs[0]["reason"] == "Age too high"
    assert logs[0]["agent_id"] == "test-agent"


def test_empty_table_returns_empty_list():
    """Reading from a brand-new DB should return an empty list, not an error."""
    logs = audit_logger.get_recent_logs(limit=5)
    assert logs == []


def test_concurrent_writes_no_corruption():
    """Fire off 50 writes from multiple threads and make sure all land cleanly."""
    errors = []

    def write_entry(i):
        try:
            audit_logger.log_decision(
                _make_entry(
                    tool_name=f"tool_{i}",
                    params={"index": i},
                    agent_id=f"agent-{i}",
                )
            )
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=write_entry, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Some writes failed: {errors}"
    logs = audit_logger.get_recent_logs(limit=100)
    assert len(logs) == 50

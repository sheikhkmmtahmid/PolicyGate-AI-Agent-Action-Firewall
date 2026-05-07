import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import audit_logger
from core import policy_engine
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Reset session history, DB, and policy engine between tests."""
    monkeypatch.setattr(audit_logger, "DB_PATH", str(tmp_path / "test.db"))
    policy_engine._last_mtime = 0.0
    policy_engine._rules = {}
    policy_engine.RULES_PATH = os.path.join(
        os.path.dirname(__file__), "..", "core", "policies", "rules.yaml"
    )

    from main import app, _session_history
    _session_history.clear()

    yield


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def test_health(client):
    """Health endpoint should always return 200."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_happy_path_check_then_refund(client):
    """check_order followed by process_refund with a valid order should succeed."""
    check_resp = client.post(
        "/gate",
        json={
            "tool_name": "check_order",
            "params": {"id": "ORDER-0001"},
            "context": {"agent_id": "agent-happy"},
        },
    )
    assert check_resp.status_code == 200
    check_body = check_resp.json()
    assert check_body["allowed"] is True
    assert check_body["result"]["status"] == "Damaged"

    refund_resp = client.post(
        "/gate",
        json={
            "tool_name": "process_refund",
            "params": {"id": "ORDER-0001", "order_age_days": 5, "status": "Damaged"},
            "context": {"agent_id": "agent-happy"},
        },
    )
    assert refund_resp.status_code == 200
    refund_body = refund_resp.json()
    assert refund_body["allowed"] is True
    assert refund_body["result"]["success"] is True


def test_refund_blocked_without_check_order(client):
    """process_refund should be blocked when check_order hasn't run first."""
    resp = client.post(
        "/gate",
        json={
            "tool_name": "process_refund",
            "params": {"id": "ORDER-001", "order_age_days": 5, "status": "Damaged"},
            "context": {"agent_id": "agent-noseq"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False
    assert "check_order" in body["reason"]


def test_refund_blocked_by_age_condition(client):
    """process_refund on an order older than 30 days should be blocked."""
    client.post(
        "/gate",
        json={
            "tool_name": "check_order",
            "params": {"id": "ORDER-002"},
            "context": {"agent_id": "agent-old"},
        },
    )
    resp = client.post(
        "/gate",
        json={
            "tool_name": "process_refund",
            "params": {"id": "ORDER-002", "order_age_days": 45, "status": "Damaged"},
            "context": {"agent_id": "agent-old"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False
    assert "45" in body["reason"] or "condition" in body["reason"].lower()


def test_refund_blocked_by_status_condition(client):
    """process_refund on a non-Damaged order should be blocked even if it's recent."""
    client.post(
        "/gate",
        json={
            "tool_name": "check_order",
            "params": {"id": "ORDER-003"},
            "context": {"agent_id": "agent-status"},
        },
    )
    resp = client.post(
        "/gate",
        json={
            "tool_name": "process_refund",
            "params": {"id": "ORDER-003", "order_age_days": 10, "status": "Delivered"},
            "context": {"agent_id": "agent-status"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False


def test_logs_returns_list(client):
    """The /logs endpoint should return a list (possibly empty)."""
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_logs_populated_after_gate_call(client):
    """After hitting /gate, /logs should have at least one entry."""
    client.post(
        "/gate",
        json={"tool_name": "check_order", "params": {"id": "ORDER-001"}, "context": {}},
    )
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

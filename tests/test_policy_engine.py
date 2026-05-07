import sys
import os
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import policy_engine


def _write_rules(tmp_path, rules: dict) -> str:
    """Write a temporary rules file and point the engine at it."""
    rules_file = os.path.join(tmp_path, "rules.yaml")
    with open(rules_file, "w") as f:
        yaml.dump(rules, f)
    policy_engine.RULES_PATH = rules_file
    # Force a reload next call
    policy_engine._last_mtime = 0.0
    policy_engine._rules = {}
    return rules_file


@pytest.fixture(autouse=True)
def reset_engine(tmp_path):
    """Each test gets a clean engine state pointing at a temp file."""
    original_path = policy_engine.RULES_PATH
    yield tmp_path
    policy_engine.RULES_PATH = original_path
    policy_engine._last_mtime = 0.0
    policy_engine._rules = {}


def test_tool_passes_all_conditions(reset_engine):
    """A call with all fields satisfying conditions should be allowed."""
    _write_rules(
        reset_engine,
        {
            "process_refund": {
                "require_prior_tool": "check_order",
                "conditions": [
                    {"field": "order_age_days", "op": "lt", "value": 30},
                    {"field": "status", "op": "eq", "value": "Damaged"},
                ],
            }
        },
    )
    allowed, reason = policy_engine.evaluate(
        "process_refund",
        params={"order_age_days": 5, "status": "Damaged"},
        context={},
        prior_tools_called=["check_order"],
    )
    assert allowed is True
    assert "passed" in reason.lower()


def test_blocked_by_missing_prior_tool(reset_engine):
    """process_refund should be blocked when check_order hasn't been called."""
    _write_rules(
        reset_engine,
        {
            "process_refund": {
                "require_prior_tool": "check_order",
                "conditions": [],
            }
        },
    )
    allowed, reason = policy_engine.evaluate(
        "process_refund",
        params={},
        context={},
        prior_tools_called=[],
    )
    assert allowed is False
    assert "check_order" in reason


def test_blocked_by_failing_condition(reset_engine):
    """A tool call where a condition value doesn't match should be blocked."""
    _write_rules(
        reset_engine,
        {
            "process_refund": {
                "conditions": [
                    {"field": "order_age_days", "op": "lt", "value": 30},
                ],
            }
        },
    )
    allowed, reason = policy_engine.evaluate(
        "process_refund",
        params={"order_age_days": 60},
        context={},
        prior_tools_called=[],
    )
    assert allowed is False
    assert "60" in reason or "condition" in reason.lower()


def test_unknown_tool_allowed_by_default(reset_engine):
    """A tool with no rules defined should pass through."""
    _write_rules(reset_engine, {})
    allowed, reason = policy_engine.evaluate(
        "some_mystery_tool",
        params={},
        context={},
        prior_tools_called=[],
    )
    assert allowed is True
    assert "no policy" in reason.lower()


def test_missing_required_field_blocked(reset_engine):
    """If a condition references a field that isn't in params or context, block it."""
    _write_rules(
        reset_engine,
        {
            "process_refund": {
                "conditions": [
                    {"field": "order_age_days", "op": "lt", "value": 30},
                ],
            }
        },
    )
    allowed, reason = policy_engine.evaluate(
        "process_refund",
        params={},  # order_age_days is missing
        context={},
        prior_tools_called=[],
    )
    assert allowed is False
    assert "order_age_days" in reason


def test_unknown_operator_blocks(reset_engine):
    """An unrecognized operator in the YAML should block and explain itself."""
    _write_rules(
        reset_engine,
        {
            "process_refund": {
                "conditions": [
                    {"field": "amount", "op": "between", "value": 100},
                ],
            }
        },
    )
    allowed, reason = policy_engine.evaluate(
        "process_refund",
        params={"amount": 50},
        context={},
        prior_tools_called=[],
    )
    assert allowed is False
    assert "between" in reason or "unknown" in reason.lower()

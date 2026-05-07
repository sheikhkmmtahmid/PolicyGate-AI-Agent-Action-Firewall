import os
import yaml
from typing import Optional

RULES_PATH = os.path.join(os.path.dirname(__file__), "policies", "rules.yaml")

_rules: dict = {}
_last_mtime: float = 0.0


def _load_rules() -> dict:
    """Load rules from YAML. Returns empty dict if file is missing or malformed."""
    global _rules, _last_mtime

    try:
        mtime = os.path.getmtime(RULES_PATH)
    except FileNotFoundError:
        _rules = {}
        _last_mtime = 0.0
        return _rules

    # Only reload if the file actually changed since we last read it
    if mtime == _last_mtime and _rules:
        return _rules

    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        _rules = loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        # Bad YAML means no rules -- fail open with a warning baked into results
        _rules = {}

    _last_mtime = mtime
    return _rules


def _resolve_field(field: str, params: dict, context: dict) -> tuple[bool, object]:
    """Look up a field in params first, then context. Returns (found, value)."""
    if field in params:
        return True, params[field]
    if field in context:
        return True, context[field]
    return False, None


def _apply_op(op: str, actual, expected) -> tuple[bool, str]:
    """
    Apply a comparison operator. Returns (passed, reason).
    If the operator is unknown, returns False with an explanation.
    """
    ops = {
        "lt": lambda a, b: a < b,
        "gt": lambda a, b: a > b,
        "eq": lambda a, b: a == b,
        "neq": lambda a, b: a != b,
        "lte": lambda a, b: a <= b,
        "gte": lambda a, b: a >= b,
    }

    if op not in ops:
        return False, f"Unknown operator '{op}' in policy rules -- cannot evaluate condition"

    try:
        passed = ops[op](actual, expected)
    except TypeError:
        return False, f"Cannot compare '{actual}' and '{expected}' with operator '{op}'"

    if not passed:
        return False, f"Condition failed: '{actual}' {op} '{expected}' is not satisfied"

    return True, ""


def evaluate(
    tool_name: str,
    params: dict,
    context: dict,
    prior_tools_called: list[str],
) -> tuple[bool, str]:
    """
    Check whether a tool call is allowed given the current policy rules.
    Returns (allowed, reason). Reason is always a human-readable string.
    """
    rules = _load_rules()

    if tool_name not in rules:
        return True, f"No policy defined for '{tool_name}' -- allowed through by default"

    tool_rules = rules[tool_name]

    # Check sequencing requirement before anything else
    required_prior = tool_rules.get("require_prior_tool")
    if required_prior and required_prior not in prior_tools_called:
        return (
            False,
            f"'{tool_name}' requires '{required_prior}' to be called first, but it hasn't been called yet in this session",
        )

    conditions = tool_rules.get("conditions", [])

    for condition in conditions:
        field = condition.get("field")
        op = condition.get("op")
        expected = condition.get("value")

        if not field:
            return False, "A condition in the policy is missing the 'field' key -- check your rules.yaml"
        if not op:
            return False, f"Condition for field '{field}' is missing the 'op' key -- check your rules.yaml"

        found, actual = _resolve_field(field, params, context)
        if not found:
            return False, f"Required field '{field}' was not found in params or context"

        passed, reason = _apply_op(op, actual, expected)
        if not passed:
            return False, reason

    return True, "All policy conditions passed"

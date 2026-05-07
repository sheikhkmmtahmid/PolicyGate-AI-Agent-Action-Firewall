import random
from typing import Any

# Seed makes the dataset reproducible across restarts, so tests that depend
# on specific order IDs always get the same data.
random.seed(42)

_STATUSES = ["Damaged", "Delivered", "Lost", "Returned", "Pending"]
# Weights reflect a realistic order mix: most arrive fine, a small slice are damaged.
_STATUS_WEIGHTS = [20, 40, 15, 15, 10]


def _generate_orders() -> dict:
    """Build 1000 orders programmatically. Much better than a hardcoded dict."""
    orders = {}
    for i in range(1, 1001):
        order_id = f"ORDER-{str(i).zfill(4)}"
        orders[order_id] = {
            "order_id": order_id,
            "order_age_days": random.randint(1, 90),
            "status": random.choices(_STATUSES, weights=_STATUS_WEIGHTS, k=1)[0],
        }
    return orders


_ORDERS = _generate_orders()


def check_order(params: dict) -> dict:
    """Look up an order by ID and return its details."""
    order_id = params.get("id") or params.get("order_id")
    if not order_id:
        raise ValueError("check_order requires an 'id' param")

    order = _ORDERS.get(str(order_id))
    if not order:
        # Return a neutral result so the agent can decide what to do
        return {"order_age_days": 0, "status": "NotFound", "order_id": str(order_id)}

    return dict(order)


def process_refund(params: dict) -> dict:
    """Simulate issuing a refund for an order."""
    order_id = params.get("id") or params.get("order_id")
    if not order_id:
        raise ValueError("process_refund requires an 'id' param")

    return {
        "success": True,
        "message": f"Refund for order {order_id} has been processed successfully",
    }


_TOOL_MAP = {
    "check_order": check_order,
    "process_refund": process_refund,
}


def execute_tool(tool_name: str, params: dict) -> Any:
    """Dispatch a tool call by name. Raises ValueError for unknown tools."""
    fn = _TOOL_MAP.get(tool_name)
    if fn is None:
        raise ValueError(
            f"No executor found for tool '{tool_name}'. "
            f"Known tools: {', '.join(_TOOL_MAP.keys())}"
        )
    return fn(params)

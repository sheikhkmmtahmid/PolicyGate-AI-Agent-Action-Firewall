import json
import os

import httpx
from openai import OpenAI

from demo import tool_executor

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
)
MODEL = "openrouter/owl-alpha"

SYSTEM_PROMPT = (
    "You are a customer support agent. You have two tools available: check_order and process_refund. "
    "You must always call check_order before you even consider process_refund. "
    "You are strictly forbidden from calling process_refund unless the result from check_order shows "
    "the order is less than 30 days old AND the status is exactly Damaged. "
    "If either condition is not met, tell the user clearly why the refund cannot be processed "
    "and do not call process_refund under any circumstance."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_order",
            "description": "Check the status and age of an order by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The order ID to look up"}
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_refund",
            "description": "Process a refund for an order by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The order ID to refund"}
                },
                "required": ["id"],
            },
        },
    },
]

_port = os.environ.get("PORT", "7860")
GATE_URL = f"http://localhost:{_port}/gate"
MAX_ITERATIONS = 10


def _score_confidence(final_text: str) -> str:
    """
    Ask the model to rate its own confidence in the final response.
    Returns "High", "Medium", or "Low". Falls back to "Medium" on any error.
    """
    prompt = (
        "Rate your confidence in the following response with a single word: High, Medium, or Low. "
        "Output only that one word, nothing else.\n\n"
        f"Response: {final_text}"
    )
    try:
        result = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
        )
        word = (result.choices[0].message.content or "").strip().capitalize()
        if word in ("High", "Medium", "Low"):
            return word
        return "Medium"
    except Exception:
        return "Medium"


def _call_gate(tool_name: str, params: dict, agent_id: str, extra_context: dict) -> dict:
    """
    Send a tool call to PolicyGate and return the parsed response.
    Raises RuntimeError if the HTTP request itself fails.
    """
    context = {"agent_id": agent_id}
    context.update(extra_context)
    try:
        resp = httpx.post(
            GATE_URL,
            json={"tool_name": tool_name, "params": params, "context": context},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Gate request failed for '{tool_name}': {e}") from e


def run_agent(user_message: str, agent_id: str = "demo-agent") -> dict:
    """
    Run the Owl Alpha agent for one user message. Handles tool calls through
    PolicyGate and returns a dict with "response" (str) and "confidence" (str).
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # We carry forward any order data we learn from check_order so that the
    # gate can evaluate process_refund conditions against it. The gate checks
    # both params and context, so stashing the order info here is enough.
    accumulated_context: dict = {}

    for iteration in range(MAX_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            return {"response": f"Could not reach the model: {e}", "confidence": "Low"}

        choice = response.choices[0]
        message = choice.message

        # No tool calls means the model is done and has a final answer
        if not message.tool_calls:
            final_text = message.content or "The agent finished without producing a response."
            confidence = _score_confidence(final_text)
            return {"response": final_text, "confidence": confidence}

        # Append the assistant turn so the conversation stays coherent
        messages.append({"role": "assistant", "content": message.content, "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in message.tool_calls
        ]})

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            try:
                params = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                params = {}

            try:
                gate_response = _call_gate(tool_name, params, agent_id, accumulated_context)
            except RuntimeError as e:
                # Gate is unreachable. Tell the model so it can respond gracefully.
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(e),
                })
                continue

            if not gate_response.get("allowed", False):
                # Gate blocked the call. Feed the reason back to the model.
                block_reason = gate_response.get("reason", "Blocked by policy.")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"BLOCKED: {block_reason}",
                })
                continue

            # Gate approved. Run the tool and capture the result.
            try:
                result = tool_executor.execute_tool(tool_name, params)
            except Exception as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Tool execution error: {e}",
                })
                continue

            # If this was check_order, save the order fields so process_refund
            # can be evaluated against them when it hits the gate.
            if tool_name == "check_order" and isinstance(result, dict):
                accumulated_context.update(result)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result),
            })

    return {"response": "The agent reached the iteration limit and could not complete the request.", "confidence": "Low"}

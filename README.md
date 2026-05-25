---
title: PolicyGate
emoji: 🛡️
colorFrom: red
colorTo: yellow
sdk: docker
pinned: false
---

# PolicyGate: AI Agent Action Firewall

Created by [SKMMT](http://skmmt.rootexception.com/)
View [Demo](https://sheikhkmmtahmid-policygate.hf.space/)

## What is PolicyGate

PolicyGate is a safety layer that sits between an AI agent and your actual business tools. Think of it like a bouncer for API calls. The agent wants to run something, say issue a refund, and instead of doing it directly, it has to ask PolicyGate first. PolicyGate checks a set of rules, says yes or no, explains why, and records everything.

The point is that AI agents can be wrong. They might try to process a refund before checking whether the order even exists, or try to delete a record without the right permissions. PolicyGate catches those mistakes before they hit production systems.

## How it works

A request comes in to `POST /gate`. It carries three things: the name of the tool the agent wants to run, the parameters for that tool, and some context about the session (like which agent is making the request).

PolicyGate first checks whether this agent has called any prerequisite tools in the current session. For `process_refund`, the rules require that `check_order` was already called. If it was not, the request is blocked immediately and the reason is returned before any condition checks even run.

If the sequencing check passes, PolicyGate evaluates each condition in the rules file against the request params. These conditions check things like whether the order is recent enough or whether the status qualifies for a refund. If any condition fails, the request is blocked with a specific explanation of exactly which condition was not met.

If everything passes, PolicyGate executes the tool, records the decision in a SQLite audit log, and returns the result. If the request was blocked, it still gets logged with the block reason. You can always look back and see what happened and why.

The `/agent/chat` endpoint runs a full LLM agent (Owl Alpha via OpenRouter) that handles tool calls autonomously. Every tool call the model attempts still goes through the gate, so the policy is enforced even when the agent is operating on its own.

The `/logs` endpoint shows the last 20 decisions. The `/health` endpoint is there for container health checks.

## Project structure

The codebase is split into two distinct layers so that the enforcement engine can be taken and used independently of the demo scenario built on top of it.

```
PolicyGate - AI Agent Action Firewall/
├── core/                        # The reusable PolicyGate engine
│   ├── policy_engine.py         # YAML rule evaluation
│   ├── audit_logger.py          # SQLite audit log, thread-safe writes
│   ├── models.py                # Pydantic schemas for the gate API
│   └── policies/
│       └── rules.yaml           # Declarative policy rules (human-readable, version-controllable)
├── demo/                        # Task 2 demo built on top of the core
│   ├── agent.py                 # Owl Alpha LLM agent via OpenRouter
│   ├── tool_executor.py         # Mock check_order and process_refund tools
│   └── static/
│       └── index.html           # Dashboard frontend
├── main.py                      # FastAPI server, wires core and demo together
├── tests/                       # Pytest suite for core and gate endpoint
├── requirements.txt
└── Dockerfile
```

### The core layer (reusable by any team)

Everything inside `core/` is completely independent of the demo scenario. It has no knowledge of orders, refunds, Owl Alpha, or OpenRouter. Any team building agentic AI can drop `core/` into their stack, write their own `rules.yaml`, point their agent at `POST /gate`, and get policy enforcement, sequencing checks, and a full audit trail without writing any of that themselves.

The only interface the core exposes is the HTTP gate: send `{tool_name, params, context}`, get back `{allowed, reason, result}`. The caller can be any agent framework -- LangChain, CrewAI, AutoGen, or a custom loop.

### The demo layer (Task 2 in action)

Everything inside `demo/` is specific to the Task 2 scenario. It contains a mock tool executor with 1000 programmatically generated orders and an LLM agent wired to call those tools through the gate. This layer exists to show the core in action with a concrete use case. Anyone adopting PolicyGate for their own product would replace this layer entirely with their own tools and agent.

## How it satisfies Task 2

Task 2 asks for a support agent that checks order status and processes refunds, but only when specific conditions are met. The agent must call `check_order` first, and `process_refund` is strictly forbidden unless the data from that first call satisfies the policy.

This is enforced at two independent layers: the system prompt tells the model what it is allowed to do, and the gate mechanically blocks anything that violates the rules regardless of what the model decides. Both layers are required. The system prompt handles the normal case. The gate is what makes it actually safe.

| Requirement | File | Line | What that code does |
|---|---|---|---|
| Agent must call check_order before process_refund (system prompt layer) | demo/agent.py | 19 | `"You must always call check_order before you even consider process_refund."` -- this sentence in the system prompt is the instruction the model receives before any conversation starts. It sets the rule at the model level. |
| Agent must call check_order before process_refund (gate enforcement layer) | core/policies/rules.yaml | 2 | `require_prior_tool: check_order` -- this field in the YAML tells the policy engine that process_refund cannot be approved unless check_order already ran in this session. The model cannot bypass this even if it ignores the system prompt. |
| Gate reads and enforces the require_prior_tool field | core/policy_engine.py | 94 | `if required_prior and required_prior not in prior_tools_called:` -- this is the actual runtime check. It reads the require_prior_tool value from the YAML, looks at the list of tools the agent has already run in this session, and blocks immediately if check_order is not in that list. |
| Policy is enforced via system instructions | demo/agent.py | 17-24 | The full `SYSTEM_PROMPT` constant. It states that the model is strictly forbidden from calling process_refund unless check_order confirms the order is under 30 days old and the status is exactly Damaged. This text is the system-level instruction the model sees before the user says anything. |
| System prompt is injected into every conversation | demo/agent.py | 101 | `{"role": "system", "content": SYSTEM_PROMPT}` -- this line puts the system prompt as the first message in the messages list that gets sent to the model. Without this line, the model would not receive the policy instructions at all. |
| Policy conditions are declared in the rules file | core/policies/rules.yaml | 4-6 | `field: order_age_days / op: lt / value: 30` -- the first condition. The gate checks that the order age in days is less than 30. If the order is 30 days old or older, it is blocked. |
| Policy conditions are declared in the rules file | core/policies/rules.yaml | 7-9 | `field: status / op: eq / value: Damaged` -- the second condition. The gate checks that the order status is exactly the string "Damaged". Any other status, including "Delivered" or "NotFound", fails this check. |
| process_refund is forbidden unless data satisfies the policy | core/policy_engine.py | 112-114 | `found, actual = _resolve_field(field, params, context)` followed by `return False, f"Required field '{field}' was not found in params or context"` -- before comparing a condition value, the engine looks up the field in params and then in context. If the field is missing from both, the call is blocked and the missing field is named in the reason. |
| process_refund is forbidden unless data satisfies the policy | core/policy_engine.py | 116-118 | `passed, reason = _apply_op(op, actual, expected)` followed by `return False, reason` -- this runs the actual comparison (lt, eq, etc.) between the value in the request and the value in the rule. If the comparison fails, the function returns False immediately with a reason that names the actual value, the operator, and the expected value. |
| Order data from check_order is carried into the gate call for process_refund | demo/agent.py | 152-153 | `if tool_name == "check_order" and isinstance(result, dict): accumulated_context.update(result)` -- after check_order runs, its result (order_age_days, status, order_id) is saved into a local dict. That dict is then passed as context when the next gate call is made, so the policy engine can find order_age_days and status even though the model's process_refund tool call only includes the order ID. |
| Every decision is logged | main.py | 73-81 | The `AuditEntry` object is built here using the current timestamp, the tool name, the params, whether it was allowed, and the reason. This happens after both allowed and blocked calls. Line 81 calls `audit_logger.log_decision(entry)` which writes the row to SQLite. |
| Audit log captures every write to the database | core/audit_logger.py | 39 | `def log_decision(entry: AuditEntry) -> None:` -- this is the function that writes to the database. It uses a threading lock so concurrent requests do not corrupt the log, and it creates the database file automatically if it does not exist yet. |
| Blocked calls return a reason (sequencing block) | core/policy_engine.py | 95-98 | `return (False, f"'{tool_name}' requires '{required_prior}' to be called first, but it hasn't been called yet in this session")` -- when the sequencing check fails, this exact string is what comes back. It names the blocked tool and the required tool so the reason is unambiguous. |
| Blocked calls return a reason (condition failure) | core/policy_engine.py | 69-70 | `return False, f"Condition failed: '{actual}' {op} '{expected}' is not satisfied"` -- when a condition comparison fails, this string is returned. It shows the actual value the engine found, the operator it tried to apply, and the expected value, so you can see exactly what the data looked like and why it did not pass. |
| Block reason is returned to the caller in the HTTP response | main.py | 83 | `return GateResponse(allowed=allowed, reason=reason, result=result)` -- the reason string from the policy engine is always included in the response body, whether the call was allowed or blocked. The client always gets a clear explanation. |
| Block reason is fed back to the model when running as an agent | demo/agent.py | 131-136 | `block_reason = gate_response.get("reason", "Blocked by policy.")` followed by appending `f"BLOCKED: {block_reason}"` as a tool message -- when the gate blocks a tool call, the agent loop takes the reason string and injects it into the conversation as the tool result. The model then sees why it was blocked and can explain it to the user. |

## How to run it

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Start the server:
   ```
   python -m uvicorn main:app --reload
   ```

3. Open the UI at `http://localhost:8000` to use the form and agent chat.

4. Test the happy path with curl (check_order first, then process_refund):
   ```
   curl -X POST http://localhost:8000/gate \
     -H "Content-Type: application/json" \
     -d '{"tool_name": "check_order", "params": {"id": "ORDER-0026"}, "context": {"agent_id": "demo"}}'

   curl -X POST http://localhost:8000/gate \
     -H "Content-Type: application/json" \
     -d '{"tool_name": "process_refund", "params": {"id": "ORDER-0026", "order_age_days": 9, "status": "Damaged"}, "context": {"agent_id": "demo"}}'
   ```

5. Test the blocked path (skip check_order):
   ```
   curl -X POST http://localhost:8000/gate \
     -H "Content-Type: application/json" \
     -d '{"tool_name": "process_refund", "params": {"id": "ORDER-0026", "order_age_days": 9, "status": "Damaged"}, "context": {"agent_id": "fresh-agent"}}'
   ```

6. Test the agent chat endpoint:
   ```
   curl -X POST http://localhost:8000/agent/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "I want a refund for ORDER-0026", "agent_id": "demo"}'
   ```

7. Check the audit log:
   ```
   curl http://localhost:8000/logs
   ```

8. Run with Docker:
   ```
   docker build -t policygate .
   docker run -p 8000:8000 policygate
   ```

9. Run the test suite:
   ```
   pytest tests/
   ```

## How to add a new policy rule

Open `core/policies/rules.yaml` and add a new top-level key matching the tool name exactly:

```yaml
send_email:
  require_prior_tool: verify_recipient
  conditions:
    - field: recipient_verified
      op: eq
      value: true
    - field: attachment_size_mb
      op: lte
      value: 25
```

Each field explained:

- `require_prior_tool`: the name of a tool that must have already run in this session before this one is allowed to proceed
- `conditions`: a list of checks to run against the request params or context
- `field`: the key to look up in params first, then context if not found there
- `op`: the comparison operator, one of `lt`, `gt`, `eq`, `neq`, `lte`, `gte`
- `value`: the value to compare against

PolicyGate reloads the YAML automatically when the file changes on disk, so you do not need to restart the server to pick up new rules.

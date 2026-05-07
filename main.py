import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from collections import defaultdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.models import GateRequest, GateResponse, AuditEntry
from core import policy_engine
from core import audit_logger
from demo import tool_executor
from demo.agent import run_agent

_session_history: dict[str, list[str]] = defaultdict(list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load policy rules on startup."""
    policy_engine._load_rules()
    print("PolicyGate is running. Policy file loaded from core/policies/rules.yaml.")
    yield


app = FastAPI(title="PolicyGate", description="AI Agent Action Firewall", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(os.path.dirname(__file__), "demo", "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the frontend UI from the root path."""
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.post("/gate", response_model=GateResponse)
async def gate(request: GateRequest):
    """
    Main firewall endpoint. Receives a tool call, checks policy, executes if allowed,
    and logs the decision either way.
    """
    agent_id = (request.context or {}).get("agent_id", "default")
    prior_tools = _session_history[agent_id]

    allowed, reason = policy_engine.evaluate(
        tool_name=request.tool_name,
        params=request.params,
        context=request.context or {},
        prior_tools_called=prior_tools,
    )

    result = None

    if allowed:
        try:
            result = tool_executor.execute_tool(request.tool_name, request.params)
            # Only track the tool in history after a successful execution
            _session_history[agent_id].append(request.tool_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {str(e)}")

    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc),
        tool_name=request.tool_name,
        params=request.params,
        allowed=allowed,
        reason=reason,
        agent_id=agent_id if agent_id != "default" else None,
    )
    audit_logger.log_decision(entry)

    return GateResponse(allowed=allowed, reason=reason, result=result)


@app.get("/logs")
async def get_logs():
    """Return the last 20 audit log entries."""
    return audit_logger.get_recent_logs(limit=20)


@app.get("/stats")
async def get_stats():
    """Return dashboard summary statistics from the audit log."""
    return audit_logger.get_stats()


@app.get("/health")
async def health():
    """Simple liveness check."""
    return {"status": "ok"}


class AgentChatRequest(BaseModel):
    message: str
    agent_id: str = "demo-agent"


@app.post("/agent/chat")
async def agent_chat(request: AgentChatRequest):
    """Run the Owl Alpha agent for one user turn and return its final response."""
    try:
        result = await asyncio.to_thread(run_agent, request.message, request.agent_id)
        return {"response": result["response"], "confidence": result.get("confidence", "Medium")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

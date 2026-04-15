"""
AgentGate — local policy API.

This service is the backend source of truth for:
  - agent registration
  - policy evaluation
  - kill switch state
  - manual approval resolution
  - event streaming to the dashboard
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from .database import (
    ensure_rules_from_policies,
    evaluate_gate,
    get_action_status,
    get_actions,
    get_agent_by_id,
    get_agent_by_key,
    get_or_create_agent,
    get_pending_actions,
    get_recent_actions,
    get_rules,
    get_system_stats,
    init_db,
    list_agents,
    record_action,
    resolve_pending,
    toggle_kill,
    update_rule_value,
)
from .models import ApprovalDecision, GateRequest, GateResponse, RegisterRequest, RuleValueUpdate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentgate")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    logger.info("AgentGate API ready — sqlite store initialized")
    yield


app = FastAPI(
    title="AgentGate Policy API",
    description="Local runtime control plane for agent tool calls",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/register")
async def register(req: RegisterRequest):
    agent = await get_or_create_agent(req.api_key, req.name)
    logger.info("registered agent %s (%s)", agent["name"], agent["id"])
    return {"agent_id": agent["id"], "name": agent["name"]}


@app.post("/api/gate", response_model=GateResponse)
async def gate(req: GateRequest):
    agent = await get_agent_by_key(req.api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Unknown API key. Register the agent first.")

    agent_id = agent["id"]
    if req.policies:
        await ensure_rules_from_policies(agent_id, req.tool_name, req.policies)

    decision, reason = await evaluate_gate(
        agent_id=agent_id,
        tool_name=req.tool_name,
        params=req.params,
        killed=bool(agent["killed"]),
    )
    action_id = await record_action(
        agent_id=agent_id,
        tool_name=req.tool_name,
        params=req.params,
        gate_decision=decision,
        reason=reason,
    )
    status = (
        "released"
        if decision == "allow"
        else "blocked"
        if decision == "block"
        else "pending_approval"
    )

    logger.info(
        "gate %s tool=%s decision=%s reason=%s",
        agent["name"],
        req.tool_name,
        decision,
        reason,
    )
    return GateResponse(
        decision=decision,
        status=status,
        reason=reason,
        action_id=action_id,
    )


@app.get("/api/agents")
async def agents_list():
    return {"agents": await list_agents()}


@app.get("/api/agents/{agent_id}")
async def agent_detail(agent_id: str):
    agent = await get_agent_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.post("/api/agents/{agent_id}/kill")
async def kill_agent(agent_id: str):
    new_state = await toggle_kill(agent_id)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent %s kill_switch=%s", agent_id, new_state)
    return {"killed": new_state}


@app.get("/api/rules/{agent_id}")
async def rules_list(agent_id: str):
    return {"rules": await get_rules(agent_id)}


@app.put("/api/rules/{agent_id}/{rule_id}")
async def rule_update(agent_id: str, rule_id: int, update: RuleValueUpdate):
    updated = await update_rule_value(agent_id, rule_id, update.value)
    if not updated:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "updated"}


@app.get("/api/actions/{agent_id}")
async def actions_list(agent_id: str, limit: int = 50):
    return {"actions": await get_recent_actions(agent_id, limit)}


@app.get("/api/pending/{agent_id}")
async def pending_list(agent_id: str):
    return {"pending": await get_pending_actions(agent_id)}


@app.post("/api/actions/{action_id}/resolve")
async def resolve_action(action_id: str, body: ApprovalDecision):
    result = await resolve_pending(action_id, approved=body.decision == "approved")
    if not result:
        raise HTTPException(status_code=404, detail="Action not found or already resolved")

    logger.info(
        "approval %s final=%s tool=%s",
        action_id,
        result["final_decision"],
        result["tool_name"],
    )
    return {
        "action_id": action_id,
        "gate_decision": result["gate_decision"],
        "final_decision": result["final_decision"],
        "status": result["status"],
        "reason": result["reason"],
    }


@app.get("/api/actions/{action_id}/status")
async def action_status(action_id: str):
    action = await get_action_status(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return {
        "action_id": action["id"],
        "gate_decision": action["gate_decision"],
        "final_decision": action["final_decision"],
        "status": action["status"],
        "reason": action["reason"],
        "resolved_at": action["resolved_at"],
        "released_at": action["released_at"],
    }


@app.get("/api/stream/{agent_id}")
async def stream(agent_id: str):
    async def event_generator():
        recent = await get_recent_actions(agent_id, limit=1)
        cursor = recent[0]["seq"] if recent else 0

        while True:
            new_actions = await get_actions(agent_id, limit=50, after_seq=cursor)
            for action in new_actions:
                cursor = action["seq"]
                yield {
                    "event": "action",
                    "data": json.dumps(action),
                }

            pending = await get_pending_actions(agent_id)
            yield {
                "event": "pending_state",
                "data": json.dumps(
                    {
                        "pending_count": len(pending),
                        "pending_ids": [action["id"] for action in pending],
                    }
                ),
            }

            agent = await get_agent_by_id(agent_id)
            if agent:
                yield {
                    "event": "agent_state",
                    "data": json.dumps({"killed": bool(agent["killed"])}),
                }

            await asyncio.sleep(0.8)

    return EventSourceResponse(event_generator())


@app.get("/api/health")
async def health():
    stats = await get_system_stats()
    return {"status": "ok", "service": "agentgate", **stats}

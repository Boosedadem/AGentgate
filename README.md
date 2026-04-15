# AgentGate

AgentGate is a local runtime control plane for agent tool calls.

This repository currently includes:

- a FastAPI policy service
- a Python SDK that gates decorated tool calls before execution
- a demo agent that exercises the real path
- a Next.js dashboard that reads backend state and resolves approvals

This repository does **not** currently include:

- a hosted control plane
- auth or multi-tenant isolation
- LangChain auto-discovery
- non-Python SDKs

The value of this repo is the local call path:

`decorated tool call -> backend policy decision -> allow/block/require approval -> action log -> dashboard`

If that path is broken, the product is not real.

## Architecture

### Backend

- `api/main.py`
  FastAPI endpoints for registration, gating, approvals, rules, kill switch, SSE, and health.
- `api/database.py`
  SQLite persistence, policy evaluation, approval state, and migrations for the local store.
- `api/tests/test_gate.py`
  Unit tests around rate limits, value caps, and approval flow.

### SDK

- `sdk/src/agentgate/client.py`
  Python decorator SDK. The wrapped function only executes after the API releases it.
- `sdk/src/agentgate/__init__.py`
  `init()` and `tool()` entrypoints.

### Demo

- `demo-agent/agent.py`
  A sample support agent that generates real gated tool calls.
- `app/dashboard/page.tsx`
  Dashboard for backend state, approvals, kill switch, and rule updates.

## Local setup

### 1. Install frontend dependencies

```bash
npm install
```

### 2. Create the Python environment and install backend + SDK dependencies

```bash
npm run setup:api
```

This creates `.venv/`, installs FastAPI dependencies from `api/requirements.txt`, and installs the local Python SDK from `sdk/` in editable mode.

If you want a clean local run, reset the SQLite store before starting the stack:

```bash
npm run reset:state
```

## Run the stack

Use separate terminals.

### Terminal A — policy API

```bash
npm run api
```

Expected URL:

- `http://localhost:8000/api/health`

### Terminal B — dashboard

```bash
npm run dashboard
```

Expected URLs:

- `http://localhost:3000/`
- `http://localhost:3000/dashboard`

If `3000` is already occupied, Next.js will pick the next free port. Use the URL printed in the terminal.

### Terminal C — demo agent

```bash
npm run demo:agent
```

This registers a sample agent, sends real tool-call requests through the SDK, and only executes tool bodies when the backend releases them.

## What to test first

### 1. Direct allow

Start the demo agent and watch `send_email`, `search_orders`, or `update_crm`.

Expected result:

- the action appears in the dashboard feed
- gate decision is `ALLOW`
- status is `released`
- the demo agent prints that the tool body executed

### 2. Value cap block

Wait for `issue_refund` calls over the cap.

Expected result:

- gate decision is `BLOCK`
- reason is `value_cap:amount`
- status is `blocked`
- the demo agent does not execute the refund body

### 3. Kill switch

Enable the kill switch from the dashboard while the demo agent is running.

Expected result:

- new actions are blocked immediately
- any pending approvals are auto-denied
- the demo agent reports `kill_switch`

### 4. Manual approval

Wait for `delete_account` or `trigger_workflow`.

Expected result:

- gate decision is `REQUIRE_APPROVAL`
- status is `pending_approval`
- approving in the dashboard releases the call
- denying blocks it permanently

## Rule provisioning

Rules are provisioned from SDK decorator metadata the first time a tool is called.

Examples:

- `@ag.tool(rate_limit=5, rate_window="minute")`
- `@ag.tool(max_value={"amount": 200})`
- `@ag.tool(require_approval=True, approval_timeout=90)`

The dashboard edits persisted rule values after they exist in SQLite.

## Tests and checks

### Frontend checks

```bash
npm run check
```

### Backend policy tests

```bash
npm run test:api
```

## Current limitations

- The local store is SQLite on your machine, not a replicated service.
- The dashboard connects directly to the API URL via `NEXT_PUBLIC_AGENTGATE_API_URL` or `http://localhost:8000`.
- The SDK is Python-only.
- Approval decisions are local operator actions in the dashboard. There is no auth layer yet.

## Environment overrides

Optional environment variables:

- `NEXT_PUBLIC_AGENTGATE_API_URL`
- `AGENTGATE_DB`
- `AGENTGATE_API_URL`
- `AGENTGATE_API_KEY`
- `AGENTGATE_DASHBOARD_URL`

## Product honesty

This repo is stronger as a local control-plane demo than as a polished pitch.

It now demonstrates:

- real backend evaluation before execution
- real allow/block behavior
- real approval gating
- a real persisted feed

It does not yet demonstrate a full hosted infrastructure product.

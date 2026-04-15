"""
AgentGate Demo Agent

A sample support agent that sends real gated tool calls through AgentGate.

Usage:
    npm run demo:agent

The agent will:
  1. Register with the AgentGate API
  2. Call tools at random intervals
  3. Respect rate limits and hard blocks
  4. React to the kill switch
  5. Wait for human approval on high-risk actions
"""

import os
import random
import sys
import time

import agentgate
from agentgate import ActionBlocked

# ── Configuration ─────────────────────────────────────

API_URL = os.environ.get("AGENTGATE_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("AGENTGATE_API_KEY", "ag_demo_key_001")
AGENT_NAME = "support-agent-prod"
DASHBOARD_URL = os.environ.get("AGENTGATE_DASHBOARD_URL", "http://localhost:3000/dashboard")

# ── Initialize AgentGate ──────────────────────────────

print("=" * 60)
print("  AgentGate Demo Agent")
print("=" * 60)
print(f"  API:   {API_URL}")
print(f"  Agent: {AGENT_NAME}")
print()

try:
    ag = agentgate.init(api_key=API_KEY, api_url=API_URL, agent_name=AGENT_NAME)
    print(f"  connected — agent ID: {ag.agent_id}")
except ConnectionError as e:
    print(f"  error — {e}")
    print()
    print("  Start the API first:")
    print("    uvicorn api.main:app --reload")
    sys.exit(1)

print(f"  dashboard: {DASHBOARD_URL}")
print("=" * 60)
print()

# ── Define gated tools ────────────────────────────────

EMAILS = [
    "sarah@acme.co", "james@globex.io", "maria@initech.com",
    "alex@contoso.dev", "chen@northwind.co", "priya@fabrikam.io",
    "omar@widgetworks.com", "lisa@datastream.co", "david@cloudpeak.io",
    "emma@nexaflow.dev",
]
SUBJECTS = [
    "Re: Your recent order", "Follow up on support ticket",
    "Refund confirmation", "Welcome aboard!", "Your subscription update",
    "Payment receipt", "Shipping notification", "Request received",
    "Important account update", "Action required: verify email",
]


@ag.tool(rate_limit=10, rate_window="minute")
def send_email(to: str, subject: str):
    """Send an email to a customer."""
    print(f"    released send_email to {to}: \"{subject}\"")
    return {"status": "sent", "to": to}


@ag.tool(rate_limit=5, rate_window="minute", max_value={"amount": 200})
def issue_refund(user_id: str, amount: float):
    """Issue a refund to a customer."""
    print(f"    released issue_refund ${amount:.2f} to {user_id}")
    return {"status": "refunded", "amount": amount}


@ag.tool(rate_limit=20, rate_window="minute")
def update_crm(contact: str, field: str, value: str):
    """Update a CRM record."""
    print(f"    released update_crm {contact} → {field}={value}")
    return {"status": "updated"}


@ag.tool(rate_limit=15, rate_window="minute")
def search_orders(query: str):
    """Search the orders database."""
    print(f"    released search_orders \"{query}\"")
    return {"results": random.randint(0, 12)}


@ag.tool(rate_limit=2, rate_window="minute", require_approval=True, approval_timeout=90)
def delete_account(user_id: str, reason: str):
    """Delete a customer account. Requires human approval."""
    print(f"    released delete_account {user_id} ({reason})")
    return {"status": "deleted"}


@ag.tool(require_approval=True, approval_timeout=90, max_value={"estimated_cost": 5000})
def trigger_workflow(workflow_name: str, estimated_cost: float, priority: str):
    """Trigger a high-impact workflow. Requires human approval."""
    print(
        f"    released trigger_workflow {workflow_name} "
        f"(estimated_cost=${estimated_cost:.2f}, priority={priority})"
    )
    return {"status": "triggered", "workflow_name": workflow_name}


# ── Action generators ─────────────────────────────────

def random_action():
    """Pick a random tool call with realistic parameters."""
    roll = random.random()

    if roll < 0.30:
        return send_email, (random.choice(EMAILS), random.choice(SUBJECTS))
    elif roll < 0.48:
        return issue_refund, (
            f"usr_{random.randint(1000, 9999)}",
            random.choice([12.99, 24.50, 49.99, 89.00, 124.99, 199.00, 249.50, 340.00, 499.99]),
        )
    elif roll < 0.62:
        return update_crm, (
            random.choice(EMAILS),
            random.choice(["status", "tier", "priority", "segment"]),
            random.choice(["active", "enterprise", "high", "churning", "renewal"]),
        )
    elif roll < 0.78:
        return search_orders, (
            random.choice(["order #8472", "recent returns", "pending refunds", "unshipped orders"]),
        )
    elif roll < 0.90:
        return delete_account, (
            f"usr_{random.randint(1000, 9999)}",
            random.choice(["user_request", "fraud_detected", "inactivity"]),
        )
    else:
        return trigger_workflow, (
            random.choice(["backfill_subscriptions", "sync_accounts", "rebuild_search"]),
            random.choice([150.00, 500.00, 1200.00, 2500.00, 4999.99]),
            random.choice(["low", "normal", "high"]),
        )


# ── Main loop ─────────────────────────────────────────

print("Agent running. Actions will appear on the dashboard.")
print("Some actions require human approval on the dashboard.")
print("Press Ctrl+C to stop.\n")

action_count = 0
blocked_count = 0

try:
    while True:
        tool_fn, args = random_action()
        action_count += 1

        print(f"[{action_count}] {tool_fn.__name__}()")
        try:
            tool_fn(*args)
        except ActionBlocked as e:
            blocked_count += 1
            if e.reason == "approval_timeout":
                print("    blocked — approval timeout")
            elif e.reason == "denied":
                print("    blocked — denied by operator")
            elif e.reason == "kill_switch":
                print("    blocked — kill switch active")
            else:
                print(f"    blocked — {e.reason}")

        # Variable delay: occasional bursts to trigger rate limits
        if random.random() < 0.15:
            delay = 0.3 + random.random() * 0.5  # burst: 300-800ms
        else:
            delay = 1.5 + random.random() * 3.0  # normal: 1.5-4.5s

        time.sleep(delay)

except KeyboardInterrupt:
    print(f"\n\nAgent stopped.")
    print(f"  Total actions: {action_count}")
    print(f"  Blocked:       {blocked_count}")
    if action_count > 0:
        print(f"  Block rate:    {blocked_count / action_count * 100:.1f}%")

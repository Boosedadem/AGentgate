"""
AgentGate SDK — Client implementation.

The decorator intercepts every call to a gated function, sends the action
details to the AgentGate Policy API, and blocks execution if the policy
denies the action. For actions requiring approval, the SDK polls the API
until a human approves or denies from the dashboard.
"""

import functools
import inspect
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger("agentgate.sdk")

DEFAULT_APPROVAL_TIMEOUT = 120  # seconds
POLL_INTERVAL = 1.5  # seconds between status polls


class ActionBlocked(Exception):
    """Raised when the AgentGate policy denies an action."""

    def __init__(self, tool_name: str, reason: Optional[str]):
        self.tool_name = tool_name
        self.reason = reason or "policy_violation"
        super().__init__(f"Action '{tool_name}' blocked by AgentGate: {self.reason}")


class AgentGate:
    """
    Core SDK client.

    Registers with the AgentGate API on init, then provides a @tool decorator
    that gates function calls through the policy engine.
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "http://localhost:8000",
        agent_name: Optional[str] = None,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.agent_name = agent_name or "default-agent"
        self.agent_id: Optional[str] = None
        self.session = requests.Session()
        self._register()

    def _register(self):
        """Register the agent with the API. Creates the agent if it doesn't exist."""
        try:
            resp = requests.post(
                f"{self.api_url}/api/register",
                json={"api_key": self.api_key, "name": self.agent_name},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            self.agent_id = data["agent_id"]
            logger.info(
                f"AgentGate initialized — agent '{data['name']}' (id: {self.agent_id})"
            )
        except requests.ConnectionError:
            raise ConnectionError(
                f"Cannot reach AgentGate API at {self.api_url}. "
                "Is the API server running? Start it with: "
                "uvicorn api.main:app --reload"
            )

    def _check_gate(
        self, tool_name: str, params: dict, policies: dict
    ) -> dict:
        """Call the gate endpoint. Returns the API response dict."""
        try:
            resp = self.session.post(
                f"{self.api_url}/api/gate",
                json={
                    "api_key": self.api_key,
                    "tool_name": tool_name,
                    "params": params,
                    "policies": policies,
                },
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"Cannot reach AgentGate API at {self.api_url}. "
                "Start it first with: uvicorn api.main:app --reload"
            ) from exc

    def _poll_for_approval(self, action_id: str, timeout: float) -> dict:
        """
        Poll the API for a pending action's resolution.
        Blocks until the action is approved, denied, or timeout is reached.
        """
        deadline = time.monotonic() + timeout
        logger.info("waiting for approval on action %s (timeout=%ss)", action_id, timeout)

        while time.monotonic() < deadline:
            try:
                resp = self.session.get(
                    f"{self.api_url}/api/actions/{action_id}/status",
                    timeout=5,
                )
                resp.raise_for_status()
                data = resp.json()

                if data["status"] != "pending_approval":
                    return data

            except requests.RequestException as e:
                logger.warning("approval poll error: %s", e)

            time.sleep(POLL_INTERVAL)

        # Timeout — treat as blocked
        return {
            "status": "blocked",
            "final_decision": "block",
            "reason": "approval_timeout",
        }

    def tool(self, **policy_kwargs):
        """
        Decorator that gates a function through the AgentGate policy engine.

        Supported policy kwargs:
            rate_limit=10       Max calls per window
            rate_window="minute" Window for rate limit (minute/hour/day)
            max_value={"amount": 200}  Parameter value caps
            require_approval=True  Require human approval before execution
            approval_timeout=120   Max seconds to wait for approval

        Example:
            @ag.tool(rate_limit=10, rate_window="minute", max_value={"amount": 200})
            def issue_refund(user_id: str, amount: float):
                ...

            @ag.tool(require_approval=True, approval_timeout=60)
            def delete_account(user_id: str, reason: str):
                ...
        """
        approval_timeout = policy_kwargs.pop("approval_timeout", DEFAULT_APPROVAL_TIMEOUT)

        def decorator(func):
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                # Build params dict from positional + keyword args
                bound_params: dict = {}
                for i, arg in enumerate(args):
                    if i < len(param_names):
                        bound_params[param_names[i]] = arg
                bound_params.update(kwargs)

                # Serialize params (convert non-JSON types to strings)
                safe_params = {}
                for k, v in bound_params.items():
                    if isinstance(v, (str, int, float, bool, type(None))):
                        safe_params[k] = v
                    else:
                        safe_params[k] = str(v)

                # Call the gate
                result = self._check_gate(
                    tool_name=func.__name__,
                    params=safe_params,
                    policies=policy_kwargs,
                )

                if result["decision"] == "block":
                    raise ActionBlocked(func.__name__, result.get("reason"))

                if result["decision"] == "require_approval":
                    # Wait for human approval
                    resolution = self._poll_for_approval(
                        result["action_id"], approval_timeout
                    )
                    if resolution["final_decision"] != "allow":
                        raise ActionBlocked(func.__name__, resolution.get("reason"))
                    # Approved — fall through to execute

                # Policy allows — execute the real function
                return func(*args, **kwargs)

            # Attach metadata for introspection
            wrapper._agentgate_tool = True
            wrapper._agentgate_policies = policy_kwargs
            return wrapper

        return decorator

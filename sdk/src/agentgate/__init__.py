"""
AgentGate SDK — Runtime control layer for AI agents.

Usage:
    import agentgate

    ag = agentgate.init("ag_live_abc123", agent_name="my-agent")

    @ag.tool(rate_limit=10, rate_window="minute")
    def send_email(to: str, subject: str):
        email_client.send(to, subject)

    send_email("user@co.com", "Hello")  # evaluated before execution
"""

from typing import Optional

from .client import AgentGate, ActionBlocked

_instance: Optional[AgentGate] = None


def init(
    api_key: str,
    api_url: str = "http://localhost:8000",
    agent_name: Optional[str] = None,
) -> AgentGate:
    """Initialize AgentGate. Call once at startup."""
    global _instance
    _instance = AgentGate(api_key=api_key, api_url=api_url, agent_name=agent_name)
    return _instance


def tool(**policy_kwargs):
    """Decorator shortcut — requires init() first."""
    if _instance is None:
        raise RuntimeError("AgentGate not initialized. Call agentgate.init(api_key) first.")
    return _instance.tool(**policy_kwargs)


__all__ = ["init", "tool", "AgentGate", "ActionBlocked"]

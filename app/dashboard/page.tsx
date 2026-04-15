"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Agent, AgentAction, HealthStatus, Rule } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_AGENTGATE_API_URL ?? "http://localhost:8000";

function relativeTime(iso: string): string {
  const delta = Math.max(0, Date.now() - Date.parse(iso));
  const seconds = Math.floor(delta / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDecision(decision: AgentAction["gateDecision"]): string {
  switch (decision) {
    case "allow":
      return "ALLOW";
    case "block":
      return "BLOCK";
    case "require_approval":
      return "REQUIRE_APPROVAL";
  }
}

function formatStatus(action: AgentAction): string {
  if (action.status === "pending_approval") return "Waiting for operator";
  if (action.reason === "approved") return "Released after approval";
  if (action.reason === "denied") return "Blocked after review";
  if (action.reason === "kill_switch") return "Blocked by kill switch";
  if (action.status === "released") return "Released to tool executor";
  return "Blocked before execution";
}

function formatReason(reason: string | null): string {
  if (!reason) return "No policy reason recorded.";
  if (reason.startsWith("value_cap:")) {
    return `Value cap exceeded on ${reason.split(":")[1]}.`;
  }
  switch (reason) {
    case "rate_limit":
      return "Rate limit exceeded.";
    case "manual_review":
      return "Manual approval required.";
    case "approved":
      return "Approved by operator.";
    case "denied":
      return "Denied by operator.";
    case "approval_timeout":
      return "Approval timed out.";
    case "kill_switch":
      return "Kill switch is active.";
    default:
      return reason.replaceAll("_", " ");
  }
}

function formatRule(rule: Rule): string {
  if (rule.rule_type === "require_approval") return "Manual approval";
  if (rule.rule_type === "rate_limit") {
    return `${rule.rule_value} per ${rule.rule_window}`;
  }
  return `${rule.rule_param ?? "value"} <= ${rule.rule_value}`;
}

function formatParamValue(value: AgentAction["params"][string]): string {
  if (typeof value === "number") return Number.isInteger(value) ? `${value}` : value.toFixed(2);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null) return "null";
  return String(value);
}

function toAction(raw: Record<string, unknown>): AgentAction {
  return {
    id: String(raw.id),
    seq: Number(raw.seq),
    toolName: String(raw.tool_name),
    params: (raw.params as AgentAction["params"]) ?? {},
    gateDecision: raw.gate_decision as AgentAction["gateDecision"],
    finalDecision: raw.final_decision as AgentAction["finalDecision"],
    status: raw.status as AgentAction["status"],
    reason: raw.reason ? String(raw.reason) : null,
    createdAt: String(raw.created_at),
    resolvedAt: raw.resolved_at ? String(raw.resolved_at) : null,
    releasedAt: raw.released_at ? String(raw.released_at) : null,
  };
}

function upsertAction(
  actions: AgentAction[],
  nextAction: AgentAction,
  limit = 200,
): AgentAction[] {
  const existingIndex = actions.findIndex((action) => action.id === nextAction.id);
  if (existingIndex >= 0) {
    const updated = [...actions];
    updated[existingIndex] = nextAction;
    return updated.sort((left, right) => right.seq - left.seq).slice(0, limit);
  }

  return [nextAction, ...actions].sort((left, right) => right.seq - left.seq).slice(0, limit);
}

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [actions, setActions] = useState<AgentAction[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [pendingActions, setPendingActions] = useState<AgentAction[]>([]);
  const [killed, setKilled] = useState(false);
  const [apiStatus, setApiStatus] = useState<"checking" | "live" | "offline">("checking");
  const [streamStatus, setStreamStatus] = useState<"connecting" | "live" | "offline">(
    "connecting",
  );
  const [ruleDrafts, setRuleDrafts] = useState<Record<number, string>>({});
  const [savingRuleIds, setSavingRuleIds] = useState<Set<number>>(new Set());
  const [resolvingIds, setResolvingIds] = useState<Set<string>>(new Set());
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [, forceTick] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );

  const releasedCount = useMemo(
    () => actions.filter((action) => action.status === "released").length,
    [actions],
  );
  const blockedCount = useMemo(
    () => actions.filter((action) => action.status === "blocked").length,
    [actions],
  );
  const approvalCount = useMemo(
    () => actions.filter((action) => action.gateDecision === "require_approval").length,
    [actions],
  );

  const refreshHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/health`);
      if (!response.ok) throw new Error("health check failed");
      const payload = (await response.json()) as HealthStatus;
      setHealth(payload);
      setApiStatus("live");
    } catch {
      setApiStatus("offline");
    }
  }, []);

  const refreshAgents = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/agents`);
      if (!response.ok) throw new Error("agents fetch failed");
      const payload = (await response.json()) as { agents: Agent[] };
      setAgents(payload.agents ?? []);
      setApiStatus("live");

      if (!selectedAgentId && payload.agents?.length) {
        setSelectedAgentId(payload.agents[0].id);
      }

      if (selectedAgentId && payload.agents && !payload.agents.some((agent) => agent.id === selectedAgentId)) {
        setSelectedAgentId(payload.agents[0]?.id ?? null);
      }
    } catch {
      setApiStatus("offline");
    }
  }, [selectedAgentId]);

  const refreshRules = useCallback(async (agentId: string) => {
    const response = await fetch(`${API_URL}/api/rules/${agentId}`);
    if (!response.ok) throw new Error("rules fetch failed");
    const payload = (await response.json()) as { rules: Rule[] };
    setRules(payload.rules ?? []);
    setRuleDrafts((current) => {
      const next = { ...current };
      for (const rule of payload.rules ?? []) {
        if (!(rule.id in next)) next[rule.id] = `${rule.rule_value}`;
      }
      return next;
    });
  }, []);

  const refreshActions = useCallback(async (agentId: string) => {
    const response = await fetch(`${API_URL}/api/actions/${agentId}?limit=80`);
    if (!response.ok) throw new Error("actions fetch failed");
    const payload = (await response.json()) as { actions: Record<string, unknown>[] };
    setActions((payload.actions ?? []).map(toAction));
  }, []);

  const refreshPending = useCallback(async (agentId: string) => {
    const response = await fetch(`${API_URL}/api/pending/${agentId}`);
    if (!response.ok) throw new Error("pending fetch failed");
    const payload = (await response.json()) as { pending: Record<string, unknown>[] };
    setPendingActions((payload.pending ?? []).map(toAction));
  }, []);

  const refreshAgentState = useCallback(async (agentId: string) => {
    const response = await fetch(`${API_URL}/api/agents/${agentId}`);
    if (!response.ok) throw new Error("agent fetch failed");
    const payload = (await response.json()) as Agent;
    setKilled(Boolean(payload.killed));
  }, []);

  useEffect(() => {
    void refreshHealth();
    void refreshAgents();

    const interval = window.setInterval(() => {
      void refreshHealth();
      void refreshAgents();
    }, 5000);

    return () => window.clearInterval(interval);
  }, [refreshAgents, refreshHealth]);

  useEffect(() => {
    if (!selectedAgentId) {
      setActions([]);
      setRules([]);
      setPendingActions([]);
      return;
    }

    const loadAgentData = async () => {
      try {
        await Promise.all([
          refreshRules(selectedAgentId),
          refreshActions(selectedAgentId),
          refreshPending(selectedAgentId),
          refreshAgentState(selectedAgentId),
        ]);
        setApiStatus("live");
      } catch {
        setApiStatus("offline");
      }
    };

    void loadAgentData();
  }, [refreshActions, refreshAgentState, refreshPending, refreshRules, selectedAgentId]);

  useEffect(() => {
    const interval = window.setInterval(() => forceTick((value) => value + 1), 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!selectedAgentId) return undefined;

    eventSourceRef.current?.close();
    const source = new EventSource(`${API_URL}/api/stream/${selectedAgentId}`);
    eventSourceRef.current = source;

    source.onopen = () => setStreamStatus("live");
    source.onerror = () => setStreamStatus("offline");

    source.addEventListener("action", (event) => {
      const action = toAction(JSON.parse(event.data) as Record<string, unknown>);
      setActions((current) => upsertAction(current, action));
      setPendingActions((current) =>
        action.status === "pending_approval"
          ? upsertAction(current, action, 50)
          : current.filter((item) => item.id !== action.id),
      );
    });

    source.addEventListener("agent_state", (event) => {
      const payload = JSON.parse(event.data) as { killed: boolean };
      setKilled(Boolean(payload.killed));
    });

    source.addEventListener("pending_state", (event) => {
      const payload = JSON.parse(event.data) as { pending_count: number };
      if (payload.pending_count === 0) {
        setPendingActions([]);
      }
    });

    return () => {
      source.close();
      setStreamStatus("connecting");
    };
  }, [selectedAgentId]);

  const saveRule = useCallback(
    async (rule: Rule) => {
      if (rule.rule_type === "require_approval" || !selectedAgentId) return;

      const nextValue = Number(ruleDrafts[rule.id] ?? rule.rule_value);
      if (!Number.isFinite(nextValue) || nextValue <= 0 || nextValue === rule.rule_value) {
        setRuleDrafts((current) => ({ ...current, [rule.id]: `${rule.rule_value}` }));
        return;
      }

      setSavingRuleIds((current) => new Set(current).add(rule.id));
      try {
        const response = await fetch(`${API_URL}/api/rules/${selectedAgentId}/${rule.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: nextValue }),
        });
        if (!response.ok) throw new Error("rule update failed");

        setRules((current) =>
          current.map((entry) =>
            entry.id === rule.id ? { ...entry, rule_value: nextValue } : entry,
          ),
        );
        setLastMessage(`Updated ${rule.tool_name} ${rule.rule_type}.`);
      } catch {
        setLastMessage("Rule update failed.");
        setRuleDrafts((current) => ({ ...current, [rule.id]: `${rule.rule_value}` }));
      } finally {
        setSavingRuleIds((current) => {
          const next = new Set(current);
          next.delete(rule.id);
          return next;
        });
      }
    },
    [ruleDrafts, selectedAgentId],
  );

  const handleKillToggle = useCallback(async () => {
    if (!selectedAgentId) return;

    try {
      const response = await fetch(`${API_URL}/api/agents/${selectedAgentId}/kill`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("kill switch update failed");
      const payload = (await response.json()) as { killed: boolean };
      setKilled(Boolean(payload.killed));
      setLastMessage(payload.killed ? "Kill switch enabled." : "Kill switch disabled.");
      await refreshPending(selectedAgentId);
      await refreshActions(selectedAgentId);
    } catch {
      setLastMessage("Kill switch update failed.");
    }
  }, [refreshActions, refreshPending, selectedAgentId]);

  const resolveApproval = useCallback(
    async (actionId: string, decision: "approved" | "denied") => {
      setResolvingIds((current) => new Set(current).add(actionId));
      try {
        const response = await fetch(`${API_URL}/api/actions/${actionId}/resolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        });
        if (!response.ok) throw new Error("approval failed");

        setLastMessage(
          decision === "approved" ? "Pending action released." : "Pending action denied.",
        );
        if (selectedAgentId) {
          await Promise.all([refreshPending(selectedAgentId), refreshActions(selectedAgentId)]);
        }
      } catch {
        setLastMessage("Approval update failed.");
      } finally {
        setResolvingIds((current) => {
          const next = new Set(current);
          next.delete(actionId);
          return next;
        });
      }
    },
    [refreshActions, refreshPending, selectedAgentId],
  );

  if (apiStatus === "offline") {
    return (
      <div className="dashboard-shell">
        <header className="topbar">
          <Link href="/" className="brand">
            <span className="brand-mark">AG</span>
            <span>AgentGate</span>
          </Link>
        </header>
        <main className="dashboard-setup">
          <section className="setup-card">
            <div className="section-eyebrow">Backend required</div>
            <h1>Policy API not reachable.</h1>
            <p>
              The dashboard only reflects real backend state. Start the FastAPI
              service first, then refresh this page.
            </p>
            <pre className="command-block">
              <code>{`npm run setup:api\nnpm run api\nnpm run dashboard\nnpm run demo:agent`}</code>
            </pre>
            <p className="page-note">
              Expected API URL: <code>{API_URL}</code>
            </p>
          </section>
        </main>
      </div>
    );
  }

  if (!selectedAgentId) {
    return (
      <div className="dashboard-shell">
        <header className="topbar">
          <Link href="/" className="brand">
            <span className="brand-mark">AG</span>
            <span>AgentGate</span>
          </Link>
          <div className="status-row">
            <span className="status-pill status-pill-live">API live</span>
          </div>
        </header>
        <main className="dashboard-setup">
          <section className="setup-card">
            <div className="section-eyebrow">Waiting for agent registration</div>
            <h1>No agent has connected yet.</h1>
            <p>
              The backend is up, but there is no registered agent in the local
              store. Start the demo agent to create one and generate real action
              traffic.
            </p>
            <pre className="command-block">
              <code>npm run demo:agent</code>
            </pre>
          </section>
        </main>
      </div>
    );
  }

  return (
    <div className="dashboard-shell">
      <header className="topbar">
        <Link href="/" className="brand">
          <span className="brand-mark">AG</span>
          <span>AgentGate</span>
        </Link>
        <div className="status-row">
          <span className={`status-pill ${streamStatus === "live" ? "status-pill-live" : ""}`}>
            feed {streamStatus}
          </span>
          <span className={`status-pill ${killed ? "status-pill-block" : "status-pill-live"}`}>
            kill switch {killed ? "on" : "off"}
          </span>
        </div>
      </header>

      <main className="dashboard-layout">
        <aside className="dashboard-sidebar">
          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Agent</div>
                <h2>{selectedAgent?.name ?? selectedAgentId}</h2>
              </div>
              {agents.length > 1 ? (
                <select
                  className="agent-select"
                  value={selectedAgentId}
                  onChange={(event) => setSelectedAgentId(event.target.value)}
                >
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
            <dl className="meta-list">
              <div>
                <dt>Agent ID</dt>
                <dd>{selectedAgentId}</dd>
              </div>
              <div>
                <dt>Registered</dt>
                <dd>{selectedAgent ? relativeTime(selectedAgent.created_at) : "unknown"}</dd>
              </div>
              <div>
                <dt>API</dt>
                <dd>{API_URL}</dd>
              </div>
            </dl>
            <button
              className={`button ${killed ? "button-secondary" : "button-danger"}`}
              onClick={handleKillToggle}
            >
              {killed ? "Disable kill switch" : "Enable kill switch"}
            </button>
          </section>

          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Control plane health</div>
                <h2>Local store</h2>
              </div>
            </div>
            <dl className="meta-list">
              <div>
                <dt>SQLite path</dt>
                <dd>{health?.db_path ?? "unavailable"}</dd>
              </div>
              <div>
                <dt>Agents</dt>
                <dd>{health?.agents ?? 0}</dd>
              </div>
              <div>
                <dt>Actions</dt>
                <dd>{health?.actions ?? 0}</dd>
              </div>
              <div>
                <dt>Pending approvals</dt>
                <dd>{pendingActions.length}</dd>
              </div>
            </dl>
            <p className="page-note">
              The dashboard shows backend truth from FastAPI and SQLite. It does
              not generate tool calls on its own.
            </p>
          </section>

          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Policies</div>
                <h2>Editable rule values</h2>
              </div>
            </div>
            {rules.length === 0 ? (
              <p className="page-note">
                Rules are provisioned on first use from the Python decorator
                metadata. Run the demo agent once to populate them.
              </p>
            ) : (
              <div className="rule-stack">
                {rules.map((rule) => (
                  <div className="rule-row" key={rule.id}>
                    <div className="rule-copy">
                      <div className="rule-title">{rule.tool_name}</div>
                      <div className="rule-detail">{formatRule(rule)}</div>
                    </div>
                    {rule.rule_type === "require_approval" ? (
                      <span className="status-pill status-pill-approval">manual</span>
                    ) : (
                      <input
                        className="rule-input"
                        type="number"
                        min={1}
                        value={ruleDrafts[rule.id] ?? `${rule.rule_value}`}
                        onChange={(event) =>
                          setRuleDrafts((current) => ({
                            ...current,
                            [rule.id]: event.target.value,
                          }))
                        }
                        onBlur={() => void saveRule(rule)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            void saveRule(rule);
                          }
                        }}
                        disabled={savingRuleIds.has(rule.id)}
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Local demo</div>
                <h2>Drive the feed</h2>
              </div>
            </div>
            <pre className="command-block compact-command-block">
              <code>npm run demo:agent</code>
            </pre>
            <p className="page-note">
              The demo agent uses the Python SDK and will only execute tool
              bodies after the backend releases the call.
            </p>
          </section>
        </aside>

        <section className="dashboard-main">
          <div className="metrics-grid">
            <article className="metric-card">
              <div className="metric-label">Action records</div>
              <div className="metric-value">{actions.length}</div>
            </article>
            <article className="metric-card">
              <div className="metric-label">Released</div>
              <div className="metric-value metric-value-allow">{releasedCount}</div>
            </article>
            <article className="metric-card">
              <div className="metric-label">Blocked</div>
              <div className="metric-value metric-value-block">{blockedCount}</div>
            </article>
            <article className="metric-card">
              <div className="metric-label">Approval path</div>
              <div className="metric-value metric-value-approval">{approvalCount}</div>
            </article>
          </div>

          {lastMessage ? <div className="flash-message">{lastMessage}</div> : null}

          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Pending approvals</div>
                <h2>{pendingActions.length ? `${pendingActions.length} waiting` : "Queue is empty"}</h2>
              </div>
            </div>
            {pendingActions.length === 0 ? (
              <p className="page-note">
                High-risk actions will stop here until an operator approves or denies
                them.
              </p>
            ) : (
              <div className="approval-list">
                {pendingActions.map((action) => (
                  <article className="approval-card" key={action.id}>
                    <div className="approval-card-header">
                      <div>
                        <div className="tool-title">{action.toolName}</div>
                        <div className="tool-meta">
                          received {relativeTime(action.createdAt)} · {formatReason(action.reason)}
                        </div>
                      </div>
                      <span className="status-pill status-pill-approval">
                        {formatDecision(action.gateDecision)}
                      </span>
                    </div>
                    <pre className="tool-call-json">
                      <code>{JSON.stringify(action.params, null, 2)}</code>
                    </pre>
                    <div className="approval-actions">
                      <button
                        className="button button-primary"
                        onClick={() => void resolveApproval(action.id, "approved")}
                        disabled={resolvingIds.has(action.id)}
                      >
                        Approve
                      </button>
                      <button
                        className="button button-danger"
                        onClick={() => void resolveApproval(action.id, "denied")}
                        disabled={resolvingIds.has(action.id)}
                      >
                        Deny
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">Action feed</div>
                <h2>Recent tool calls</h2>
              </div>
            </div>
            {actions.length === 0 ? (
              <div className="empty-panel">
                <p>No action records yet.</p>
                <p className="page-note">
                  Start the demo agent to register a tool call and prove the
                  control path.
                </p>
              </div>
            ) : (
              <div className="action-list">
                {actions.map((action) => (
                  <article className="action-card" key={action.id}>
                    <div className="action-card-top">
                      <div>
                        <div className="tool-title">{action.toolName}</div>
                        <div className="tool-meta">
                          #{action.seq} · {relativeTime(action.createdAt)}
                          {action.releasedAt ? ` · released ${relativeTime(action.releasedAt)}` : ""}
                        </div>
                      </div>
                      <div className="decision-stack">
                        <span
                          className={`status-pill ${
                            action.gateDecision === "allow"
                              ? "status-pill-live"
                              : action.gateDecision === "block"
                              ? "status-pill-block"
                              : "status-pill-approval"
                          }`}
                        >
                          {formatDecision(action.gateDecision)}
                        </span>
                        <span
                          className={`status-pill ${
                            action.status === "released"
                              ? "status-pill-live"
                              : action.status === "blocked"
                              ? "status-pill-block"
                              : "status-pill-approval"
                          }`}
                        >
                          {action.status}
                        </span>
                      </div>
                    </div>
                    <div className="action-summary">
                      <div>{formatStatus(action)}</div>
                      <div>{formatReason(action.reason)}</div>
                    </div>
                    <dl className="param-grid">
                      {Object.entries(action.params).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{formatParamValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </article>
                ))}
              </div>
            )}
          </section>
        </section>
      </main>
    </div>
  );
}

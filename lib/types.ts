export interface AgentAction {
  id: string;
  seq: number;
  toolName: string;
  params: Record<string, string | number | boolean | null>;
  gateDecision: "allow" | "block" | "require_approval";
  finalDecision: "allow" | "block" | "require_approval";
  status: "released" | "blocked" | "pending_approval";
  reason: string | null;
  createdAt: string;
  resolvedAt?: string | null;
  releasedAt?: string | null;
}

export interface Rule {
  id: number;
  agent_id: string;
  tool_name: string;
  rule_type: "rate_limit" | "value_cap" | "require_approval";
  rule_value: number;
  rule_window: string;
  rule_param?: string | null;
}

export interface Agent {
  id: string;
  name: string;
  killed: boolean;
  created_at: string;
}

export interface HealthStatus {
  status: "ok" | "error";
  service: string;
  db_path: string;
  agents: number;
  actions: number;
  pending: number;
}

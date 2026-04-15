"""
AgentGate — SQLite persistence and policy evaluation.

This module owns the backend source of truth for:
  - registered agents
  - per-tool rules
  - immutable gate decisions
  - approval resolution state
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import aiosqlite

DB_PATH = os.environ.get(
    "AGENTGATE_DB", os.path.join(os.path.dirname(__file__), "agentgate.db")
)

WINDOW_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return await cur.fetchone() is not None


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    cur = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cur.fetchall()
    return {row[1] for row in rows}


async def _create_rules_table(db: aiosqlite.Connection, table_name: str = "rules") -> None:
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT NOT NULL,
            tool_name   TEXT NOT NULL,
            rule_type   TEXT NOT NULL,
            rule_value  REAL NOT NULL,
            rule_window TEXT DEFAULT 'minute',
            rule_param  TEXT
        )
        """
    )
    await db.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique
        ON {table_name}(agent_id, tool_name, rule_type, ifnull(rule_param, ''))
        """
    )


async def _create_actions_table(
    db: aiosqlite.Connection, table_name: str = "actions"
) -> None:
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            seq            INTEGER PRIMARY KEY AUTOINCREMENT,
            id             TEXT UNIQUE NOT NULL,
            agent_id       TEXT NOT NULL,
            tool_name      TEXT NOT NULL,
            params         TEXT DEFAULT '{{}}',
            gate_decision  TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            status         TEXT NOT NULL,
            reason         TEXT,
            created_at     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            resolved_at    TEXT,
            released_at    TEXT
        )
        """
    )
    await db.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_agent ON {table_name}(agent_id, seq DESC)"
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_released
        ON {table_name}(agent_id, tool_name, status, released_at)
        """
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_pending
        ON {table_name}(agent_id, status) WHERE status = 'pending_approval'
        """
    )


async def _migrate_rules_table(db: aiosqlite.Connection) -> None:
    await _create_rules_table(db, "rules_v2")
    await db.execute(
        """
        INSERT INTO rules_v2 (id, agent_id, tool_name, rule_type, rule_value, rule_window, rule_param)
        SELECT id, agent_id, tool_name, rule_type, rule_value, rule_window, NULL
        FROM rules
        """
    )
    await db.execute("DROP TABLE rules")
    await db.execute("ALTER TABLE rules_v2 RENAME TO rules")


async def _migrate_actions_table(db: aiosqlite.Connection) -> None:
    await _create_actions_table(db, "actions_v2")
    await db.execute(
        """
        INSERT INTO actions_v2 (
            seq,
            id,
            agent_id,
            tool_name,
            params,
            gate_decision,
            final_decision,
            status,
            reason,
            created_at,
            resolved_at,
            released_at
        )
        SELECT
            seq,
            id,
            agent_id,
            tool_name,
            params,
            CASE
                WHEN decision = 'allowed' THEN 'allow'
                WHEN decision = 'blocked' THEN 'block'
                ELSE 'require_approval'
            END,
            CASE
                WHEN decision = 'allowed' THEN 'allow'
                WHEN decision = 'blocked' THEN 'block'
                ELSE 'require_approval'
            END,
            CASE
                WHEN decision = 'allowed' THEN 'released'
                WHEN decision = 'blocked' THEN 'blocked'
                ELSE 'pending_approval'
            END,
            reason,
            created_at,
            resolved_at,
            CASE
                WHEN decision = 'allowed' THEN created_at
                ELSE NULL
            END
        FROM actions
        """
    )
    await db.execute("DROP TABLE actions")
    await db.execute("ALTER TABLE actions_v2 RENAME TO actions")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id          TEXT PRIMARY KEY,
                api_key     TEXT UNIQUE NOT NULL,
                name        TEXT DEFAULT 'default-agent',
                killed      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """
        )

        if not await _table_exists(db, "rules"):
            await _create_rules_table(db)
        else:
            rule_columns = await _table_columns(db, "rules")
            if "rule_param" not in rule_columns:
                await _migrate_rules_table(db)

        if not await _table_exists(db, "actions"):
            await _create_actions_table(db)
        else:
            action_columns = await _table_columns(db, "actions")
            required = {"gate_decision", "final_decision", "status", "released_at"}
            if not required.issubset(action_columns):
                await _migrate_actions_table(db)

        await db.commit()


async def get_or_create_agent(api_key: str, name: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE api_key = ?", (api_key,))
        row = await cur.fetchone()
        if row:
            return dict(row)

        agent_id = uuid.uuid4().hex[:12]
        await db.execute(
            "INSERT INTO agents (id, api_key, name) VALUES (?, ?, ?)",
            (agent_id, api_key, name),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return dict(await cur.fetchone())


async def get_agent_by_key(api_key: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE api_key = ?", (api_key,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_agent_by_id(agent_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_agents() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents ORDER BY created_at DESC")
        return [dict(row) for row in await cur.fetchall()]


async def toggle_kill(agent_id: str) -> Optional[bool]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT killed FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
        if not row:
            return None

        new_state = 0 if row[0] else 1
        await db.execute("UPDATE agents SET killed = ? WHERE id = ?", (new_state, agent_id))
        await db.commit()

    if new_state:
        await _deny_all_pending(agent_id)

    return bool(new_state)


async def get_rules(agent_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT *
            FROM rules
            WHERE agent_id = ?
            ORDER BY tool_name, rule_type, ifnull(rule_param, '')
            """,
            (agent_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def upsert_rule(
    agent_id: str,
    tool_name: str,
    rule_type: str,
    rule_value: float,
    rule_window: str = "minute",
    rule_param: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id
            FROM rules
            WHERE agent_id = ?
              AND tool_name = ?
              AND rule_type = ?
              AND ifnull(rule_param, '') = ifnull(?, '')
            """,
            (agent_id, tool_name, rule_type, rule_param),
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                """
                UPDATE rules
                SET rule_value = ?, rule_window = ?, rule_param = ?
                WHERE id = ?
                """,
                (rule_value, rule_window, rule_param, row[0]),
            )
        else:
            await db.execute(
                """
                INSERT INTO rules (
                    agent_id,
                    tool_name,
                    rule_type,
                    rule_value,
                    rule_window,
                    rule_param
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (agent_id, tool_name, rule_type, rule_value, rule_window, rule_param),
            )
        await db.commit()


async def update_rule_value(agent_id: str, rule_id: int, value: float) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE rules SET rule_value = ? WHERE id = ? AND agent_id = ?",
            (value, rule_id, agent_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def ensure_rules_from_policies(agent_id: str, tool_name: str, policies: Dict) -> None:
    existing = await get_rules(agent_id)
    tool_rules = [rule for rule in existing if rule["tool_name"] == tool_name]
    if tool_rules:
        return

    if "rate_limit" in policies:
        await upsert_rule(
            agent_id=agent_id,
            tool_name=tool_name,
            rule_type="rate_limit",
            rule_value=float(policies["rate_limit"]),
            rule_window=str(policies.get("rate_window", "minute")),
        )

    if "max_value" in policies:
        for param_name, cap in policies["max_value"].items():
            await upsert_rule(
                agent_id=agent_id,
                tool_name=tool_name,
                rule_type="value_cap",
                rule_value=float(cap),
                rule_param=str(param_name),
            )

    if policies.get("require_approval"):
        await upsert_rule(
            agent_id=agent_id,
            tool_name=tool_name,
            rule_type="require_approval",
            rule_value=1.0,
        )


async def count_recent_released(agent_id: str, tool_name: str, window: str) -> int:
    seconds = WINDOW_SECONDS.get(window, 60)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT COUNT(*)
            FROM actions
            WHERE agent_id = ?
              AND tool_name = ?
              AND status = 'released'
              AND COALESCE(released_at, created_at) > ?
            """,
            (agent_id, tool_name, cutoff),
        )
        row = await cur.fetchone()
        return row[0]


async def evaluate_gate(
    agent_id: str, tool_name: str, params: dict, killed: bool
) -> Tuple[str, Optional[str]]:
    if killed:
        return "block", "kill_switch"

    rules = await get_rules(agent_id)
    applicable = [rule for rule in rules if rule["tool_name"] == tool_name]

    for rule in applicable:
        if rule["rule_type"] != "value_cap":
            continue

        param_name = rule["rule_param"]
        if not param_name or param_name not in params:
            continue

        value = params[param_name]
        if isinstance(value, (int, float)) and float(value) > float(rule["rule_value"]):
            return "block", f"value_cap:{param_name}"

    for rule in applicable:
        if rule["rule_type"] != "rate_limit":
            continue

        count = await count_recent_released(
            agent_id, tool_name, str(rule["rule_window"])
        )
        if count >= float(rule["rule_value"]):
            return "block", "rate_limit"

    for rule in applicable:
        if rule["rule_type"] == "require_approval":
            return "require_approval", "manual_review"

    return "allow", None


def _gate_outcome(gate_decision: str) -> Tuple[str, str, Optional[str]]:
    if gate_decision == "allow":
        return "allow", "released", utc_now()
    if gate_decision == "block":
        return "block", "blocked", None
    return "require_approval", "pending_approval", None


async def record_action(
    agent_id: str,
    tool_name: str,
    params: dict,
    gate_decision: str,
    reason: Optional[str],
) -> str:
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    final_decision, status, released_at = _gate_outcome(gate_decision)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO actions (
                id,
                agent_id,
                tool_name,
                params,
                gate_decision,
                final_decision,
                status,
                reason,
                released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                agent_id,
                tool_name,
                json.dumps(params),
                gate_decision,
                final_decision,
                status,
                reason,
                released_at,
            ),
        )
        await db.commit()
    return action_id


def _deserialize_action(row: aiosqlite.Row) -> dict:
    record = dict(row)
    record["params"] = json.loads(record["params"])
    return record


async def get_actions(agent_id: str, limit: int = 100, after_seq: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT *
            FROM actions
            WHERE agent_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (agent_id, after_seq, limit),
        )
        rows = await cur.fetchall()
        return [_deserialize_action(row) for row in rows]


async def get_recent_actions(agent_id: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT *
            FROM actions
            WHERE agent_id = ?
            ORDER BY seq DESC
            LIMIT ?
            """,
            (agent_id, limit),
        )
        rows = await cur.fetchall()
        return [_deserialize_action(row) for row in rows]


async def get_pending_actions(agent_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT *
            FROM actions
            WHERE agent_id = ? AND status = 'pending_approval'
            ORDER BY seq ASC
            """,
            (agent_id,),
        )
        rows = await cur.fetchall()
        return [_deserialize_action(row) for row in rows]


async def resolve_pending(action_id: str, approved: bool) -> Optional[dict]:
    resolved_at = utc_now()
    final_decision = "allow" if approved else "block"
    status = "released" if approved else "blocked"
    reason = "approved" if approved else "denied"
    released_at = resolved_at if approved else None

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM actions WHERE id = ? AND status = 'pending_approval'",
            (action_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None

        await db.execute(
            """
            UPDATE actions
            SET final_decision = ?, status = ?, reason = ?, resolved_at = ?, released_at = ?
            WHERE id = ? AND status = 'pending_approval'
            """,
            (final_decision, status, reason, resolved_at, released_at, action_id),
        )
        await db.commit()

        cur = await db.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        updated = await cur.fetchone()
        return _deserialize_action(updated)


async def get_action_status(action_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return _deserialize_action(row)


async def _deny_all_pending(agent_id: str) -> None:
    resolved_at = utc_now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE actions
            SET final_decision = 'block',
                status = 'blocked',
                reason = 'kill_switch',
                resolved_at = ?
            WHERE agent_id = ? AND status = 'pending_approval'
            """,
            (resolved_at, agent_id),
        )
        await db.commit()


async def get_system_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM agents")
        agents = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM actions")
        actions = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT COUNT(*) FROM actions WHERE status = 'pending_approval'"
        )
        pending = (await cur.fetchone())[0]
        return {"db_path": DB_PATH, "agents": agents, "actions": actions, "pending": pending}

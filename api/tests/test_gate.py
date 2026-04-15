from __future__ import annotations

import importlib
import os
import tempfile
import unittest


class GatePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AGENTGATE_DB"] = os.path.join(self.tempdir.name, "agentgate-test.db")

        import api.database as database_module

        self.db = importlib.reload(database_module)
        await self.db.init_db()
        self.agent = await self.db.get_or_create_agent("test-key", "test-agent")

    async def asyncTearDown(self):
        self.tempdir.cleanup()
        os.environ.pop("AGENTGATE_DB", None)

    async def test_rate_limit_counts_released_actions(self):
        await self.db.upsert_rule(
            agent_id=self.agent["id"],
            tool_name="send_email",
            rule_type="rate_limit",
            rule_value=1,
            rule_window="minute",
        )

        decision, _ = await self.db.evaluate_gate(
            self.agent["id"], "send_email", {"to": "a@b.com"}, killed=False
        )
        self.assertEqual(decision, "allow")
        await self.db.record_action(
            self.agent["id"], "send_email", {"to": "a@b.com"}, decision, None
        )

        second_decision, second_reason = await self.db.evaluate_gate(
            self.agent["id"], "send_email", {"to": "b@c.com"}, killed=False
        )
        self.assertEqual(second_decision, "block")
        self.assertEqual(second_reason, "rate_limit")

    async def test_value_cap_respects_parameter_name(self):
        await self.db.upsert_rule(
            agent_id=self.agent["id"],
            tool_name="issue_refund",
            rule_type="value_cap",
            rule_value=200,
            rule_param="amount",
        )

        decision, reason = await self.db.evaluate_gate(
            self.agent["id"], "issue_refund", {"user_id": "usr_1", "amount": 250}, killed=False
        )
        self.assertEqual(decision, "block")
        self.assertEqual(reason, "value_cap:amount")

        allowed, allowed_reason = await self.db.evaluate_gate(
            self.agent["id"], "issue_refund", {"user_id": "usr_1", "amount": 150}, killed=False
        )
        self.assertEqual(allowed, "allow")
        self.assertIsNone(allowed_reason)

    async def test_manual_approval_preserves_gate_decision(self):
        await self.db.upsert_rule(
            agent_id=self.agent["id"],
            tool_name="delete_account",
            rule_type="require_approval",
            rule_value=1,
        )

        decision, reason = await self.db.evaluate_gate(
            self.agent["id"], "delete_account", {"user_id": "usr_9"}, killed=False
        )
        self.assertEqual(decision, "require_approval")
        self.assertEqual(reason, "manual_review")

        action_id = await self.db.record_action(
            self.agent["id"], "delete_account", {"user_id": "usr_9"}, decision, reason
        )
        resolved = await self.db.resolve_pending(action_id, approved=True)

        assert resolved is not None
        self.assertEqual(resolved["gate_decision"], "require_approval")
        self.assertEqual(resolved["final_decision"], "allow")
        self.assertEqual(resolved["status"], "released")
        self.assertEqual(resolved["reason"], "approved")


if __name__ == "__main__":
    unittest.main()

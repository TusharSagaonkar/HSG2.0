"""Tests for the ``GateOpsAuditLog`` append-only audit model.

Covers the ``log()`` classmethod, immutability (no update / no delete), the
JSON ``device_info`` field, and the nullable ``rule_applied`` linkage
(Phase 2 not yet implemented).
"""

from django.test import TestCase

from gateops.models import GateOpsAuditLog
from societies.models import Society


class GateOpsAuditLogTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.society = Society.objects.create(name="Audit Log Test Society")

    def test_log_creates_record_with_before_after_json(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="GateEvent",
            entity_id=42,
            before_value={"status": "invited"},
            after_value={"status": "arrived"},
        )
        self.assertEqual(entry.before_value, {"status": "invited"})
        self.assertEqual(entry.after_value, {"status": "arrived"})
        self.assertEqual(entry.entity_id, "42")

    def test_updating_existing_record_raises(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Person",
            entity_id=1,
        )
        with self.assertRaises(PermissionError):
            entry.save()

    def test_deleting_record_raises_permission_error(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Person",
            entity_id=1,
        )
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_rule_applied_nullable(self):
        """Phase 2 not implemented — the rule FK is omitted in Phase 1, so
        audit log entries persist without any rule linkage."""
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.RULE_EVALUATED,
            entity_type="GateEvent",
            entity_id=99,
        )
        self.assertIsNotNone(entry.pk)

    def test_device_info_accepts_dict(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="GateEvent",
            entity_id=5,
            device_info={"user_agent": "GuardApp/1.0", "device_id": "dev-123"},
        )
        entry.refresh_from_db()
        self.assertEqual(entry.device_info["device_id"], "dev-123")
        self.assertEqual(entry.device_info["user_agent"], "GuardApp/1.0")

    def test_device_info_defaults_to_empty_dict(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="GateEvent",
            entity_id=6,
        )
        entry.refresh_from_db()
        self.assertEqual(entry.device_info, {})

    def test_blacklist_and_escalate_actions_allowed(self):
        """The Action enum must include BLACKLIST and ESCALATE (added in Phase 1)."""
        for action in (
            GateOpsAuditLog.Action.BLACKLIST,
            GateOpsAuditLog.Action.ESCALATE,
        ):
            entry = GateOpsAuditLog.log(
                society=self.society,
                action=action,
                entity_type="Person",
                entity_id=7,
            )
            self.assertEqual(entry.action, action)

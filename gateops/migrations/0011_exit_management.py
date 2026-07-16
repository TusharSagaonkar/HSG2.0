# Generated for Phase 12: Exit Management.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("gateops", "0010_ai_recommendation_engine"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Create ShiftHandover
        migrations.CreateModel(
            name="ShiftHandover",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handover_uuid", models.UUIDField(default=uuid.uuid4, db_index=True, editable=False, unique=True, verbose_name="handover UUID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("acknowledged", "Acknowledged"), ("disputed", "Disputed")], db_index=True, default="pending", max_length=20, verbose_name="status")),
                ("inside_count", models.PositiveIntegerField(default=0, verbose_name="inside count")),
                ("pending_items_count", models.PositiveIntegerField(default=0, verbose_name="pending items count")),
                ("pending_items_summary", models.JSONField(default=dict, verbose_name="pending items summary")),
                ("outgoing_notes", models.TextField(blank=True, verbose_name="outgoing notes")),
                ("incoming_notes", models.TextField(blank=True, verbose_name="incoming notes")),
                ("dispute_reason", models.TextField(blank=True, verbose_name="dispute reason")),
                ("handed_over_at", models.DateTimeField(auto_now_add=True, verbose_name="handed over at")),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True, verbose_name="acknowledged at")),
                ("disputed_at", models.DateTimeField(blank=True, null=True, verbose_name="disputed at")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shift_handovers", to="housing.society", verbose_name="society")),
                ("outgoing_guard", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="outgoing_handovers", to="gateops.securityguard", verbose_name="outgoing guard")),
                ("incoming_guard", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="incoming_handovers", to="gateops.securityguard", verbose_name="incoming guard")),
                ("gate", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shift_handovers", to="gateops.gate", verbose_name="gate")),
                ("shift", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handovers", to="gateops.guardshift", verbose_name="shift")),
                ("outgoing_assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="outgoing_handovers", to="gateops.guardshiftassignment", verbose_name="outgoing assignment")),
                ("incoming_assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incoming_handovers", to="gateops.guardshiftassignment", verbose_name="incoming assignment")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_shift_handovers", to=settings.AUTH_USER_MODEL, verbose_name="created by")),
                ("acknowledged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acknowledged_shift_handovers", to=settings.AUTH_USER_MODEL, verbose_name="acknowledged by")),
            ],
            options={
                "verbose_name": "Shift Handover",
                "verbose_name_plural": "Shift Handovers",
                "ordering": ("-handed_over_at",),
                "indexes": [
                    models.Index(fields=["society", "status"], name="handover_soc_status_idx"),
                    models.Index(fields=["society", "gate"], name="handover_soc_gate_idx"),
                    models.Index(fields=["society", "outgoing_guard"], name="handover_soc_out_idx"),
                    models.Index(fields=["society", "incoming_guard"], name="handover_soc_in_idx"),
                    models.Index(fields=["society", "handed_over_at"], name="handover_soc_date_idx"),
                    models.Index(fields=["society", "is_active"], name="handover_soc_act_idx"),
                    models.Index(fields=["handover_uuid"], name="handover_uuid_idx"),
                ],
            },
        ),
        # 2. Create ShiftHandoverItem
        migrations.CreateModel(
            name="ShiftHandoverItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entered_at", models.DateTimeField(blank=True, null=True, verbose_name="entered at")),
                ("duration_minutes_at_handover", models.PositiveIntegerField(default=0, verbose_name="duration minutes at handover")),
                ("is_overstay", models.BooleanField(default=False, verbose_name="is overstay")),
                ("notes", models.TextField(blank=True, verbose_name="notes")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shift_handover_items", to="housing.society", verbose_name="society")),
                ("handover", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="gateops.shifthandover", verbose_name="handover")),
                ("gate_event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="handover_items", to="gateops.gateevent", verbose_name="gate event")),
                ("person", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handover_items", to="gateops.person", verbose_name="person")),
                ("visitor_category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handover_items", to="gateops.visitorcategory", verbose_name="visitor category")),
                ("gate", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handover_items", to="gateops.gate", verbose_name="gate")),
            ],
            options={
                "verbose_name": "Shift Handover Item",
                "verbose_name_plural": "Shift Handover Items",
                "ordering": ("handover", "-entered_at"),
                "indexes": [
                    models.Index(fields=["handover"], name="hitem_handover_idx"),
                    models.Index(fields=["society", "person"], name="hitem_soc_person_idx"),
                    models.Index(fields=["society", "gate_event"], name="hitem_soc_event_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(gate_event__isnull=False),
                        fields=["handover", "gate_event"],
                        name="uniq_handover_item_per_event",
                    ),
                ],
            },
        ),
        # 3. Add new audit actions to GateOpsAuditLog.action choices
        #    (HANDOVER_CREATED, HANDOVER_ACKNOWLEDGED, HANDOVER_DISPUTED)
        migrations.AlterField(
            model_name="gateopsauditlog",
            name="action",
            field=models.CharField(choices=[
                ("create", "Create"), ("update", "Update"), ("delete", "Delete"),
                ("approve", "Approve"), ("reject", "Reject"), ("entry", "Entry"),
                ("exit", "Exit"), ("rule_evaluated", "Rule Evaluated"),
                ("state_transition", "State Transition"), ("blacklist", "Blacklist"),
                ("escalate", "Escalate"),
                ("anomaly_detected", "Anomaly Detected"),
                ("pattern_updated", "Pattern Updated"),
                ("prediction_generated", "Prediction Generated"),
                ("handover_created", "Handover Created"),
                ("handover_acknowledged", "Handover Acknowledged"),
                ("handover_disputed", "Handover Disputed"),
            ], max_length=30),
        ),
    ]

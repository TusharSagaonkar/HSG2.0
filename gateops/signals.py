"""Signal receivers for the ``gateops`` app.

The primary receiver bootstraps default gate-operations configuration whenever
a new ``Society`` is created, following the established pattern in
``accounting/signals.py``. All creations use ``get_or_create`` for idempotency
(so re-running the receiver — or calling it twice — never duplicates records).
"""

import django.dispatch
from django.db.models.signals import post_save
from django.dispatch import receiver

from gateops.models import (
    ApprovalType,
    Gate,
    GateOpsRole,
    GateOpsSocietyConfig,
    GuardShift,
    MasterSettings,
    MaterialCategory,
    NotificationPreference,
    PassType,
    VehicleCategory,
    VisitorCategory,
)
from societies.models import Society

# Custom signal sent after the gateops bootstrap completes for a society.
# Other apps (or Phase 2+ services) may connect to this to perform additional
# setup without re-implementing the bootstrap logic.
gateops_bootstrap_done = django.dispatch.Signal()


# --- Default seed data --------------------------------------------------------

DEFAULT_VISITOR_CATEGORIES = (
    # (code, name, flags dict)
    ("GUEST", "Guest", {}),
    ("DELIVERY", "Delivery", {"is_delivery": True}),
    ("DOMESTIC_HELP", "Domestic Help", {"is_domestic_help": True}),
    ("CONTRACTOR", "Contractor", {"is_contractor": True}),
    ("VENDOR", "Vendor", {}),
    ("EMERGENCY", "Emergency", {"is_emergency": True}),
    ("RESIDENT", "Resident", {"is_resident": True}),
    ("TAXI", "Taxi", {}),
    ("UNKNOWN", "Unknown Visitor", {}),
)

DEFAULT_VEHICLE_CATEGORIES = (
    # (code, name, flags dict)
    ("VISITOR", "Visitor Vehicle", {}),
    ("DELIVERY", "Delivery Vehicle", {"is_delivery": True}),
    ("COMMERCIAL", "Commercial Vehicle", {"is_commercial": True}),
    ("EMERGENCY", "Emergency Vehicle", {"is_emergency": True}),
    ("ELECTRIC", "Electric Vehicle", {"is_electric": True}),
    ("OVERSIZED", "Oversized Vehicle", {"is_oversized": True}),
)

DEFAULT_MATERIAL_CATEGORIES = (
    # (code, name, is_inbound_default)
    ("INBOUND", "Inbound Material", True),
    ("OUTBOUND", "Outbound Material", False),
)

DEFAULT_PASS_TYPES = (
    # (code, name, validation_method, duration_type, default_validity_hours)
    ("QR_PASS", "QR Pass", PassType.ValidationMethod.QR, PassType.DurationType.ONE_TIME, 24),
    ("OTP_PASS", "OTP Pass", PassType.ValidationMethod.OTP, PassType.DurationType.ONE_TIME, 24),
    ("DAILY_PASS", "Daily Pass", PassType.ValidationMethod.NONE, PassType.DurationType.DAILY, 24),
)

DEFAULT_APPROVAL_TYPES = (
    # (code, name, approver)
    ("AUTO", "Auto Approve", ApprovalType.Approver.AUTO),
    ("RESIDENT", "Resident Approval", ApprovalType.Approver.RESIDENT),
    ("SECURITY", "Security Approval", ApprovalType.Approver.SECURITY),
)

# Per-visitor-category default notification preferences (Phase 10). Keys are
# visitor-category codes (matching DEFAULT_VISITOR_CATEGORIES). Values are
# dicts of NotificationPreference field overrides; any field not listed falls
# back to the global default (channel=PUSH, trigger=ARRIVAL, is_silent=False,
# bundle_window_minutes=0).
_DEFAULT_NOTIFICATION_PREFERENCES = {
    # Deliveries are high-frequency and low-urgency: bundle them so a resident
    # gets one digest instead of a push per parcel.
    "DELIVERY": {
        "bundle_window_minutes": 30,
    },
    # Emergencies are urgent: use SMS (higher open rate than push) and never
    # silence them.
    "EMERGENCY": {
        "channel": NotificationPreference.Channel.SMS,
        "is_silent": False,
    },
    # Contractors enter to perform work: notify the host on entry (not just
    # arrival) so the host knows work has actually started.
    "CONTRACTOR": {
        "trigger": NotificationPreference.Trigger.ENTRY,
    },
}

# Per-role default permission sets. Keys mirror GateOpsRole.KNOWN_PERMISSION_KEYS.
# GATE_ADMIN gets everything; VIEWER gets nothing; others get a sensible subset.
_DEFAULT_ROLE_PERMISSIONS = {
    GateOpsRole.RoleCode.GATE_ADMIN: {
        "can_create_event": True,
        "can_approve_visitor": True,
        "can_blacklist": True,
        "can_manage_rules": True,
        "can_manage_masters": True,
        "can_view_analytics": True,
        "can_manage_guards": True,
        "can_override_rule": True,
        "can_export_data": True,
    },
    GateOpsRole.RoleCode.SECURITY_SUPERVISOR: {
        "can_create_event": True,
        "can_approve_visitor": True,
        "can_blacklist": True,
        "can_manage_rules": False,
        "can_manage_masters": False,
        "can_view_analytics": True,
        "can_manage_guards": True,
        "can_override_rule": False,
        "can_export_data": True,
    },
    GateOpsRole.RoleCode.GUARD: {
        "can_create_event": True,
        "can_approve_visitor": False,
        "can_blacklist": False,
        "can_manage_rules": False,
        "can_manage_masters": False,
        "can_view_analytics": False,
        "can_manage_guards": False,
        "can_override_rule": False,
        "can_export_data": False,
    },
    GateOpsRole.RoleCode.RECEPTION: {
        "can_create_event": True,
        "can_approve_visitor": True,
        "can_blacklist": False,
        "can_manage_rules": False,
        "can_manage_masters": False,
        "can_view_analytics": False,
        "can_manage_guards": False,
        "can_override_rule": False,
        "can_export_data": False,
    },
    GateOpsRole.RoleCode.RESIDENT: {
        "can_create_event": False,
        "can_approve_visitor": True,
        "can_blacklist": False,
        "can_manage_rules": False,
        "can_manage_masters": False,
        "can_view_analytics": False,
        "can_manage_guards": False,
        "can_override_rule": False,
        "can_export_data": False,
    },
    GateOpsRole.RoleCode.VIEWER: {
        "can_create_event": False,
        "can_approve_visitor": False,
        "can_blacklist": False,
        "can_manage_rules": False,
        "can_manage_masters": False,
        "can_view_analytics": True,
        "can_manage_guards": False,
        "can_override_rule": False,
        "can_export_data": False,
    },
}

DEFAULT_ROLES = (
    # (code, name)
    (GateOpsRole.RoleCode.GATE_ADMIN, "Gate Admin"),
    (GateOpsRole.RoleCode.SECURITY_SUPERVISOR, "Security Supervisor"),
    (GateOpsRole.RoleCode.GUARD, "Guard"),
    (GateOpsRole.RoleCode.RECEPTION, "Reception"),
    (GateOpsRole.RoleCode.RESIDENT, "Resident"),
    (GateOpsRole.RoleCode.VIEWER, "Viewer"),
)


@receiver(post_save, sender=Society)
def bootstrap_gateops_defaults(sender, instance, created, **kwargs):
    """Seed default gate-operations configuration for a newly created society.

    Uses ``get_or_create`` throughout so the receiver is idempotent: re-running
    it (e.g. if the signal fires twice) never creates duplicate records.
    """
    if not created:
        return

    society = instance

    # 1. Society-level gate ops configuration (one-to-one).
    GateOpsSocietyConfig.objects.get_or_create(society=society)

    # 2. Default "Main Gate".
    Gate.objects.get_or_create(
        society=society,
        code="MAIN",
        defaults={
            "name": "Main Gate",
            "gate_type": Gate.GateType.MAIN,
            "is_active": True,
        },
    )

    # 3. Visitor categories.
    for sort_order, (code, name, flags) in enumerate(DEFAULT_VISITOR_CATEGORIES):
        VisitorCategory.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "sort_order": sort_order,
                **flags,
            },
        )

    # 3b. Default notification preferences per visitor category (Phase 10).
    # One preference row per category using the global default, overridden by
    # any category-specific tuning in _DEFAULT_NOTIFICATION_PREFERENCES.
    for code, _name, _flags in DEFAULT_VISITOR_CATEGORIES:
        overrides = _DEFAULT_NOTIFICATION_PREFERENCES.get(code, {})
        NotificationPreference.objects.get_or_create(
            society=society,
            visitor_category=VisitorCategory.objects.get(society=society, code=code),
            channel=overrides.get("channel", NotificationPreference.Channel.PUSH),
            defaults={
                "trigger": overrides.get("trigger", NotificationPreference.Trigger.ARRIVAL),
                "is_silent": overrides.get("is_silent", False),
                "bundle_window_minutes": overrides.get("bundle_window_minutes", 0),
            },
        )

    # 4. Vehicle categories.
    for sort_order, (code, name, flags) in enumerate(DEFAULT_VEHICLE_CATEGORIES):
        VehicleCategory.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "sort_order": sort_order,
                **flags,
            },
        )

    # 5. Material categories.
    for sort_order, (code, name, is_inbound_default) in enumerate(
        DEFAULT_MATERIAL_CATEGORIES
    ):
        MaterialCategory.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "is_inbound_default": is_inbound_default,
                "sort_order": sort_order,
            },
        )

    # 6. Pass types.
    for code, name, validation_method, duration_type, validity_hours in DEFAULT_PASS_TYPES:
        PassType.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "validation_method": validation_method,
                "duration_type": duration_type,
                "default_validity_hours": validity_hours,
            },
        )

    # 7. Approval types.
    for code, name, approver in DEFAULT_APPROVAL_TYPES:
        ApprovalType.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "approver": approver,
            },
        )

    # 8. Gate ops roles with default permission sets.
    for code, name in DEFAULT_ROLES:
        GateOpsRole.objects.get_or_create(
            society=society,
            code=code,
            defaults={
                "name": name,
                "permissions": dict(_DEFAULT_ROLE_PERMISSIONS[code]),
            },
        )

    # 9. Master settings umbrella (one-to-one).
    MasterSettings.objects.get_or_create(society=society)

    # Notify any downstream listeners that the gateops bootstrap is complete.
    gateops_bootstrap_done.send(sender=Society, society=society)

"""Server-rendered views for the Gate Operations testing console."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from gateops.forms import (
    AnalyticsCustomReportForm,
    AnalyticsDateRangeForm,
    AnalyticsExportForm,
    ApprovalTypeForm,
    ContractForm,
    ContractorForm,
    CurrentlyInsideFilterForm,
    GateEventApprovalForm,
    GateEventForm,
    GateForm,
    GateOpsRoleForm,
    GateOpsSocietyConfigForm,
    HandoverAcknowledgeForm,
    HandoverDisputeForm,
    HolidayCalendarForm,
    MasterSettingsForm,
    MaterialCategoryForm,
    NotificationPreferenceForm,
    PassTypeForm,
    PersonForm,
    QrExitForm,
    QuickExitForm,
    RuleActionForm,
    RuleConditionForm,
    RuleContextTestForm,
    RuleForm,
    ShiftHandoverForm,
    VehicleCategoryForm,
    VehicleRegisterForm,
    VisitorCategoryForm,
    WorkPermitForm,
    WorkerForm,
)
from gateops.models import (
    ApprovalType,
    Gate,
    GateEvent,
    GateEventApproval,
    GateOpsAuditLog,
    GateOpsRole,
    GateOpsSocietyConfig,
    GateVehicle,
    GuardShift,
    HolidayCalendar,
    MasterSettings,
    MaterialCategory,
    MaterialMovement,
    NotificationPreference,
    Parcel,
    Pass,
    PassType,
    Person,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
    SecurityGuard,
    ShiftHandover,
    VehicleCategory,
    VisitorCategory,
)
from gateops.services.contractor_service import ContractorService
from gateops.services.exit_management_service import ExitManagementService
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.material_service import MaterialService
from gateops.services.parcel_service import ParcelService
from gateops.services.pass_service import PassService
from gateops.services.rule_engine import RuleEngineService
from gateops.services.rule_tester import RuleTestService
from gateops.services.shift_handover_service import ShiftHandoverService
from gateops.services.vehicle_service import VehicleService
from gateops.services.analytics_service import AnalyticsService
from gateops.services.visitor_category_service import VisitorCategoryService
from housing_accounting.selection import get_selected_scope

MISSING_SOCIETY_MESSAGE = "Select a society to use Gate Operations."

SETUP_SECTIONS = {
    "society-config": {
        "title": "Society Config",
        "model": GateOpsSocietyConfig,
        "form": GateOpsSocietyConfigForm,
        "fields": ("default_approval_timeout_minutes", "otp_length", "photo_required", "auto_close_enabled"),
        "singleton": True,
        "description": "Core behavior switches for approvals, pass validation, retention, offline sync, and night mode.",
    },
    "master-settings": {
        "title": "Master Settings",
        "model": MasterSettings,
        "form": MasterSettingsForm,
        "fields": ("settings", "updated_by", "updated_at"),
        "singleton": True,
        "description": "Flexible JSON settings for society-specific GateOps options that do not need dedicated columns yet.",
    },
    "gates": {
        "title": "Gates",
        "model": Gate,
        "form": GateForm,
        "fields": ("code", "name", "gate_type", "is_active"),
        "description": "Physical gate rows used by rules, guard assignment, and gate event context.",
    },
    "visitor-categories": {
        "title": "Visitor Categories",
        "model": VisitorCategory,
        "form": VisitorCategoryForm,
        "fields": ("code", "name", "requires_approval_default", "sort_order", "is_active"),
        "description": "Configurable visitor types such as guests, delivery, contractors, domestic help, and residents.",
    },
    "vehicle-categories": {
        "title": "Vehicle Categories",
        "model": VehicleCategory,
        "form": VehicleCategoryForm,
        "fields": ("code", "name", "requires_approval_default", "sort_order", "is_active"),
        "description": "Vehicle classifications used in rules and gate context, separate from resident parking vehicles.",
    },
    "material-categories": {
        "title": "Material Categories",
        "model": MaterialCategory,
        "form": MaterialCategoryForm,
        "fields": ("code", "name", "is_inbound_default", "requires_approval_default", "is_active"),
        "description": "Inbound and outbound material movement categories used by material rules.",
    },
    "pass-types": {
        "title": "Pass Types",
        "model": PassType,
        "form": PassTypeForm,
        "fields": ("code", "name", "validation_method", "duration_type", "default_validity_hours", "is_active"),
        "description": "Pass templates for QR, OTP, digital, daily, and recurring gate passes.",
    },
    "approval-types": {
        "title": "Approval Types",
        "model": ApprovalType,
        "form": ApprovalTypeForm,
        "fields": ("code", "name", "approver", "escalation_timeout_minutes", "is_active"),
        "description": "Approval workflows and escalation timeout defaults used by rule actions and gate decisions.",
    },
    "notification-preferences": {
        "title": "Notification Preferences",
        "model": NotificationPreference,
        "form": NotificationPreferenceForm,
        "fields": ("visitor_category", "channel", "trigger", "is_silent", "is_active"),
        "description": "Per-visitor-category notification channel and trigger preferences for this society.",
    },
    "gate-roles": {
        "title": "Gate Roles",
        "model": GateOpsRole,
        "form": GateOpsRoleForm,
        "fields": ("code", "name", "is_active"),
        "description": "Gate-specific roles and permissions layered on top of society membership access.",
    },
    "holidays": {
        "title": "Holiday Calendar",
        "model": HolidayCalendar,
        "form": HolidayCalendarForm,
        "fields": ("date", "name", "affects", "is_recurring_annually"),
        "description": "Society holidays used by rule conditions and restricted access windows.",
    },
}

def _base_context(request, **context):
    society = context.pop("society", None)
    context.update(
        {
            "society": society,
            "selected_society": society,
            "scope_society_name": society.name if society else "No society selected",
            "missing_society_message": MISSING_SOCIETY_MESSAGE,
        }
    )
    return context


def _render_missing_society(request):
    return render(
        request,
        "gateops/missing_society.html",
        _base_context(request),
        status=404,
    )


def _selected_society_or_missing(request):
    society = getattr(request, "current_society", None)
    if society is None:
        society, _ = get_selected_scope(request, persist=True)
    if society is None:
        return None, _render_missing_society(request)
    return society, None


def _audit(request, society, action, entity_type, entity_id, before_value=None, after_value=None):
    actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    return GateOpsAuditLog.log(
        society=society,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_value=before_value,
        after_value=after_value,
        ip_address=_client_ip(request),
        device_info={"user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]},
    )


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _json(value):
    return json.dumps(_json_safe(value), indent=2, sort_keys=True)


def _json_safe(value: Any):
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "pk") and hasattr(value, "_meta"):
        return {"id": value.pk, "label": str(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _rule_queryset(society):
    return (
        Rule.objects.filter(society=society)
        .select_related("visitor_category", "vehicle_category", "material_category", "gate")
        .prefetch_related(
            Prefetch("conditions", queryset=RuleCondition.objects.order_by("sort_order", "id")),
            Prefetch("actions", queryset=RuleAction.objects.order_by("execution_order", "id")),
        )
        .annotate(condition_count=Count("conditions", distinct=True), action_count=Count("actions", distinct=True))
        .order_by("priority", "name")
    )


def _setup_config_or_404(slug):
    config = SETUP_SECTIONS.get(slug)
    if config is None:
        raise Http404("Unknown GateOps setup section.")
    return config


def _setup_queryset(config, society):
    queryset = config["model"].objects.filter(society=society)
    if config["model"] is NotificationPreference:
        queryset = queryset.select_related("visitor_category")
    elif config["model"] is VisitorCategory:
        queryset = queryset.select_related("default_pass_type")
    elif config["model"] is MasterSettings:
        queryset = queryset.select_related("updated_by")
    return queryset


def _setup_object_or_404(config, society, pk=None):
    if config.get("singleton"):
        obj, _ = config["model"].objects.get_or_create(society=society)
        return obj
    return get_object_or_404(_setup_queryset(config, society), pk=pk)


def _model_has_field(model, field_name):
    return any(field.name == field_name for field in model._meta.fields)


def _setup_rows(society):
    rows = []
    for slug, config in SETUP_SECTIONS.items():
        queryset = _setup_queryset(config, society)
        active_count = queryset.filter(is_active=True).count() if _model_has_field(config["model"], "is_active") else None
        rows.append(
            {
                "slug": slug,
                "title": config["title"],
                "description": config["description"],
                "count": queryset.count(),
                "active_count": active_count,
                "singleton": bool(config.get("singleton")),
            }
        )
    return rows


def _object_values(obj):
    values = {}
    for field in obj._meta.fields:
        name = field.name
        if name in {"id", "society", "created_at", "updated_at", "deleted_at"}:
            continue
        values[name] = _json_safe(getattr(obj, name))
    return values


def _bootstrap_rows(society):
    checks = [
        ("Config", GateOpsSocietyConfig.objects.filter(society=society), "Expected one society-level configuration row."),
        ("Gates", Gate.objects.filter(society=society), "Default MAIN gate should exist."),
        ("Visitor categories", VisitorCategory.objects.filter(society=society), "Seeded categories include DELIVERY, CONTRACTOR, GUEST."),
        ("Vehicle categories", VehicleCategory.objects.filter(society=society), "Seeded categories include DELIVERY and EMERGENCY."),
        ("Material categories", MaterialCategory.objects.filter(society=society), "Seeded inbound/outbound material categories."),
        ("Pass types", PassType.objects.filter(society=society), "QR, OTP, and daily pass types."),
        ("Approval types", ApprovalType.objects.filter(society=society), "AUTO, RESIDENT, SECURITY approvals."),
        ("Gate roles", GateOpsRole.objects.filter(society=society), "Gate admin, supervisor, guard, reception, resident, viewer."),
        ("Holiday calendar", HolidayCalendar.objects.filter(society=society), "Optional until holidays are configured for rule conditions."),
        ("Master settings", MasterSettings.objects.filter(society=society), "Expected one flexible settings row."),
    ]
    rows = []
    for label, queryset, note in checks:
        count = queryset.count()
        required = label != "Holiday calendar"
        rows.append(
            {
                "label": label,
                "count": count,
                "complete": count > 0 or not required,
                "required": required,
                "note": note,
            }
        )
    return rows


def gateops_dashboard_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rules = _rule_queryset(society)
    context = _base_context(
        request,
        society=society,
        bootstrap_rows=_bootstrap_rows(society),
        totals={
            "rules": rules.count(),
            "active_rules": rules.filter(is_active=True).count(),
            "evaluations": RuleEvaluation.objects.filter(society=society).count(),
            "audit_logs": GateOpsAuditLog.objects.filter(society=society).count(),
        },
        latest_evaluations=RuleEvaluation.objects.filter(society=society).select_related("rule").order_by("-evaluated_at", "-id")[:5],
        setup_rows=_setup_rows(society),
        latest_audit_logs=GateOpsAuditLog.objects.filter(society=society).order_by("-created_at", "-id")[:5],
    )
    return render(request, "gateops/dashboard.html", context)


def setup_index_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    return render(
        request,
        "gateops/setup_index.html",
        _base_context(request, society=society, setup_rows=_setup_rows(society)),
    )


def setup_section_view(request, slug):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    config = _setup_config_or_404(slug)
    singleton = bool(config.get("singleton"))
    singleton_object = _setup_object_or_404(config, society) if singleton else None
    return render(
        request,
        "gateops/setup_section.html",
        _base_context(
            request,
            society=society,
            slug=slug,
            config=config,
            rows=_setup_queryset(config, society),
            fields=config["fields"],
            singleton=singleton,
            singleton_object=singleton_object,
        ),
    )


def _setup_form_instance(form_class, request, society, instance=None):
    kwargs = {"society": society}
    if instance is not None:
        kwargs["instance"] = instance
    if form_class is MasterSettingsForm:
        kwargs["updated_by"] = request.user
    if request.method == "POST":
        return form_class(request.POST, **kwargs)
    return form_class(**kwargs)


def setup_create_view(request, slug):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    config = _setup_config_or_404(slug)
    if config.get("singleton"):
        obj = _setup_object_or_404(config, society)
        return redirect("gateops:setup-edit", slug=slug, pk=obj.pk)
    form_class = config["form"]
    form = _setup_form_instance(form_class, request, society)
    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            _audit(request, society, GateOpsAuditLog.Action.CREATE, config["model"].__name__, obj.pk, after_value=_object_values(obj))
            messages.success(request, f"{config['title']} item created for {society.name}.")
            return redirect("gateops:setup-section", slug=slug)
        messages.error(request, f"{config['title']} item could not be created. Check the highlighted fields.")
    return render(
        request,
        "gateops/setup_form.html",
        _base_context(request, society=society, slug=slug, config=config, form=form, form_title=f"Add {config['title']}", submit_label="Create"),
    )


def setup_edit_view(request, slug, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    config = _setup_config_or_404(slug)
    obj = _setup_object_or_404(config, society, pk=pk)
    before = _object_values(obj)
    form = _setup_form_instance(config["form"], request, society, instance=obj)
    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            _audit(request, society, GateOpsAuditLog.Action.UPDATE, config["model"].__name__, obj.pk, before_value=before, after_value=_object_values(obj))
            messages.success(request, f"{config['title']} item updated.")
            return redirect("gateops:setup-section", slug=slug)
        messages.error(request, f"{config['title']} item could not be updated. Check the highlighted fields.")
    return render(
        request,
        "gateops/setup_form.html",
        _base_context(request, society=society, slug=slug, config=config, form=form, obj=obj, form_title=f"Edit {config['title']}", submit_label="Save"),
    )


def setup_delete_view(request, slug, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    config = _setup_config_or_404(slug)
    obj = _setup_object_or_404(config, society, pk=pk)
    before = _object_values(obj)
    label = str(obj)
    if _model_has_field(config["model"], "is_active"):
        obj.is_active = False
        update_fields = ["is_active"]
        if _model_has_field(config["model"], "deleted_at"):
            obj.deleted_at = timezone.now()
            update_fields.append("deleted_at")
        if _model_has_field(config["model"], "updated_at"):
            update_fields.append("updated_at")
        obj.save(update_fields=update_fields)
        action_message = "deactivated"
    else:
        obj.delete()
        action_message = "deleted"
    _audit(request, society, GateOpsAuditLog.Action.DELETE, config["model"].__name__, pk, before_value=before)
    messages.success(request, f"{config['title']} item {label} {action_message}.")
    return redirect("gateops:setup-section", slug=slug)


def setup_move_view(request, slug, pk, direction):
    """Move a setup item up or down (reordering). POST-only.

    Follows the same society-scoped, POST-only pattern as ``setup_delete_view``.
    Reordering is currently only supported for ``VisitorCategory``; other
    sections get an informational message and a redirect back.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    config = _setup_config_or_404(slug)
    obj = _setup_object_or_404(config, society, pk=pk)
    # Only VisitorCategory supports reordering for now.
    if config["model"] is not VisitorCategory:
        messages.error(request, "Reordering is not supported for this section.")
        return redirect("gateops:setup-section", slug=slug)
    actor = request.user if request.user.is_authenticated else None
    if direction == "up":
        moved = VisitorCategoryService.move_up(obj, actor=actor)
        if moved:
            messages.success(request, f"{obj.name} moved up.")
        else:
            messages.info(request, f"{obj.name} is already at the top.")
    elif direction == "down":
        moved = VisitorCategoryService.move_down(obj, actor=actor)
        if moved:
            messages.success(request, f"{obj.name} moved down.")
        else:
            messages.info(request, f"{obj.name} is already at the bottom.")
    else:
        messages.error(request, "Invalid direction.")
    return redirect("gateops:setup-section", slug=slug)


def rule_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    return render(request, "gateops/rule_list.html", _base_context(request, society=society, rules=_rule_queryset(society)))


def rule_create_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if request.method == "POST":
        form = RuleForm(request.POST, society=society)
        if form.is_valid():
            rule = form.save(commit=False)
            rule.created_by = request.user if request.user.is_authenticated else None
            rule.save()
            _audit(request, society, GateOpsAuditLog.Action.CREATE, "Rule", rule.pk, after_value=_rule_snapshot(rule))
            messages.success(request, f"Rule {rule.code} created for {society.name}.")
            return redirect("gateops:rule-detail", pk=rule.pk)
        messages.error(request, "Rule could not be created. Check the highlighted fields.")
    else:
        form = RuleForm(society=society, initial={"valid_from": timezone.localdate(), "applies_on": Rule.AppliesOn.ENTRY, "priority": 100})
    return render(request, "gateops/rule_form.html", _base_context(request, society=society, form=form, form_title="Create Gate Rule", submit_label="Create Rule"))


def rule_detail_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rule = get_object_or_404(_rule_queryset(society), pk=pk)
    condition_form = RuleConditionForm(rule=rule, prefix="condition")
    action_form = RuleActionForm(rule=rule, prefix="action")
    return render(
        request,
        "gateops/rule_detail.html",
        _base_context(
            request,
            society=society,
            rule=rule,
            condition_form=condition_form,
            action_form=action_form,
            latest_evaluations=RuleEvaluation.objects.filter(society=society, rule=rule).order_by("-evaluated_at", "-id")[:5],
        ),
    )


def rule_edit_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rule = get_object_or_404(Rule.objects.filter(society=society), pk=pk)
    before = _rule_snapshot(rule)
    if request.method == "POST":
        form = RuleForm(request.POST, society=society, instance=rule)
        if form.is_valid():
            rule = form.save()
            _audit(request, society, GateOpsAuditLog.Action.UPDATE, "Rule", rule.pk, before_value=before, after_value=_rule_snapshot(rule))
            messages.success(request, f"Rule {rule.code} updated.")
            return redirect("gateops:rule-detail", pk=rule.pk)
        messages.error(request, "Rule could not be updated. Check the highlighted fields.")
    else:
        form = RuleForm(society=society, instance=rule)
    return render(request, "gateops/rule_form.html", _base_context(request, society=society, form=form, rule=rule, form_title=f"Edit {rule.code}", submit_label="Save Rule"))


def rule_toggle_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rule = get_object_or_404(Rule.objects.filter(society=society), pk=pk)
    before = _rule_snapshot(rule)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active", "updated_at"])
    _audit(request, society, GateOpsAuditLog.Action.UPDATE, "Rule", rule.pk, before_value=before, after_value=_rule_snapshot(rule))
    messages.success(request, f"Rule {rule.code} is now {'enabled' if rule.is_active else 'disabled'}.")
    return redirect(request.POST.get("next") or "gateops:rule-list")


def condition_create_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rule = get_object_or_404(Rule.objects.filter(society=society), pk=pk)
    form = RuleConditionForm(request.POST, rule=rule, prefix="condition")
    if form.is_valid():
        condition = form.save()
        _audit(request, society, GateOpsAuditLog.Action.CREATE, "RuleCondition", condition.pk, after_value=_condition_snapshot(condition))
        messages.success(request, "Condition added to the rule.")
    else:
        messages.error(request, "Condition could not be added. Check field, operator, and JSON value.")
    return redirect("gateops:rule-detail", pk=rule.pk)


def action_create_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    rule = get_object_or_404(Rule.objects.filter(society=society), pk=pk)
    form = RuleActionForm(request.POST, rule=rule, prefix="action")
    if form.is_valid():
        action = form.save()
        _audit(request, society, GateOpsAuditLog.Action.CREATE, "RuleAction", action.pk, after_value=_action_snapshot(action))
        messages.success(request, "Action added to the rule.")
    else:
        messages.error(request, "Action could not be added. Check action and JSON parameters.")
    return redirect("gateops:rule-detail", pk=rule.pk)


def rule_test_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    engine_result = None
    dry_run_result = None
    context_json = None
    latest_evaluation = None
    if request.method == "POST":
        form = RuleContextTestForm(request.POST, society=society)
        if form.is_valid():
            sample_context = form.build_context()
            actor = request.user if request.user.is_authenticated else None
            if actor is not None:
                sample_context["actor"] = actor
            engine_result = RuleEngineService.evaluate(sample_context)
            selected_rule = form.cleaned_data.get("rule")
            if selected_rule is not None:
                dry_run_result = RuleTestService.dry_run(selected_rule, sample_context)
            latest_evaluation = engine_result.evaluation
            context_json = _json(sample_context)
            messages.success(request, "Rule engine evaluation completed and logged.")
        else:
            messages.error(request, "Rule test could not run. Check the highlighted fields.")
    else:
        form = RuleContextTestForm(society=society, initial={"visitor_category": "DELIVERY", "applies_on": Rule.AppliesOn.ENTRY, "date": timezone.localdate(), "tower": "A", "wing": "1", "flat": "101"})
    return render(
        request,
        "gateops/rule_test.html",
        _base_context(
            request,
            society=society,
            form=form,
            engine_result=engine_result,
            dry_run_result=dry_run_result,
            context_json=context_json,
            latest_evaluation=latest_evaluation,
            engine_result_json=_json(engine_result) if engine_result else None,
            dry_run_result_json=_json(dry_run_result) if dry_run_result else None,
        ),
    )


def logs_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    return render(
        request,
        "gateops/logs.html",
        _base_context(
            request,
            society=society,
            evaluations=RuleEvaluation.objects.filter(society=society).select_related("rule").order_by("-evaluated_at", "-id")[:50],
            audit_logs=GateOpsAuditLog.objects.filter(society=society).select_related("actor").order_by("-created_at", "-id")[:50],
        ),
    )


def _rule_snapshot(rule):
    return {
        "id": rule.pk,
        "name": rule.name,
        "code": rule.code,
        "priority": rule.priority,
        "is_active": rule.is_active,
        "applies_on": rule.applies_on,
        "visitor_category_id": rule.visitor_category_id,
        "vehicle_category_id": rule.vehicle_category_id,
        "material_category_id": rule.material_category_id,
        "gate_id": rule.gate_id,
        "valid_from": str(rule.valid_from),
        "valid_until": str(rule.valid_until) if rule.valid_until else None,
    }


def _condition_snapshot(condition):
    return {
        "id": condition.pk,
        "rule_id": condition.rule_id,
        "field": condition.field,
        "operator": condition.operator,
        "value": condition.value,
        "logical_connector": condition.logical_connector,
        "sort_order": condition.sort_order,
    }


def _action_snapshot(action):
    return {
        "id": action.pk,
        "rule_id": action.rule_id,
        "action": action.action,
        "parameters": action.parameters,
        "execution_order": action.execution_order,
    }


# --------------------------------------------------------------------------- #
# Phase 3: Visitor lifecycle (GateEvent) views
# --------------------------------------------------------------------------- #


def _gate_event_queryset(society):
    """Society-scoped GateEvent queryset with the joins the console needs."""
    return GateEvent.objects.filter(society=society).select_related(
        "person", "visitor_category", "gate", "guard"
    )


def _gate_event_or_404(society, uuid):
    """Fetch a single society-scoped GateEvent by its public UUID.

    Cross-society lookups raise ``Http404`` so a UUID from one society never
    leaks into another tenant's console.
    """
    return get_object_or_404(_gate_event_queryset(society), event_uuid=uuid)


def _gate_event_status_badge(status):
    """Map a GateEvent status to a Bootstrap badge class for templates."""
    return {
        GateEvent.Status.INVITED: "bg-info",
        GateEvent.Status.ARRIVED: "bg-warning",
        GateEvent.Status.APPROVED: "bg-success",
        GateEvent.Status.REJECTED: "bg-danger",
        GateEvent.Status.ENTERED: "bg-success",
        GateEvent.Status.EXITED: "bg-secondary",
        GateEvent.Status.AUTO_CLOSED: "bg-secondary",
        GateEvent.Status.CANCELLED: "bg-secondary",
        GateEvent.Status.EXPIRED: "bg-secondary",
    }.get(status, "bg-secondary")


def gate_event_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    events = _gate_event_queryset(society).order_by("-created_at")[:100]
    return render(
        request,
        "gateops/gate_event_list.html",
        _base_context(
            request,
            society=society,
            events=events,
            active_tab="events",
            status_badge=_gate_event_status_badge,
        ),
    )


def gate_event_form_view(request):
    """Create a new gate event (walk-in arrival flow).

    The form collects person lookup fields (phone + name) and event details.
    On POST the view resolves/creates the :class:`Person`, builds the
    :class:`GateEvent` directly in the ``arrived`` state (walk-in), then hands
    control to :class:`GateEventLifecycleService.evaluate_rules` so the rule
    engine drives the next transition (auto-approve / reject / pending).
    """
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        form = GateEventForm(request.POST, society=society)
        if form.is_valid():
            data = form.cleaned_data
            phone = (data.get("person_phone") or "").strip()
            name = (data.get("person_name") or "").strip()

            # Resolve or create the Person. A blank phone is allowed only when
            # a name is supplied; otherwise we cannot deduplicate visitors.
            if phone:
                person, _created = Person.objects.get_or_create(
                    society=society,
                    phone=phone,
                    defaults={"name": name or phone},
                )
            elif name:
                person = Person.objects.create(society=society, name=name, phone="")
            else:
                person = None

            visitor_category = data.get("visitor_category")
            gate = data.get("gate")
            if visitor_category is None or gate is None:
                # Should not happen for required FK fields, but guard anyway.
                messages.error(request, "Visitor category and gate are required.")
            else:
                actor = request.user if request.user.is_authenticated else None
                event = GateEvent(
                    society=society,
                    gate=gate,
                    person=person,
                    visitor_category=visitor_category,
                    event_type=GateEvent.EventType.ARRIVAL,
                    status=GateEvent.Status.ARRIVED,
                    direction=data.get("direction", GateEvent.Direction.INBOUND),
                    purpose=data.get("purpose", ""),
                    photo_url=data.get("photo_url", ""),
                    id_verified=bool(data.get("id_verified")),
                    notes=data.get("notes", ""),
                    arrived_at=timezone.now(),
                    created_by=actor,
                )
                event.save()

                # Drive the next state from the rule engine. The service
                # caches rule_evaluated/rule_action and may auto-approve,
                # reject, or create a pending approval request.
                GateEventLifecycleService.evaluate_rules(event)

                _audit(
                    request,
                    society,
                    GateOpsAuditLog.Action.CREATE,
                    "GateEvent",
                    str(event.pk),
                    after_value=_json_safe(event),
                )
                messages.success(request, f"Gate event {event.event_uuid} recorded for {society.name}.")
                return redirect("gateops:event-detail", uuid=event.event_uuid)
        else:
            messages.error(request, "Gate event could not be created. Check the highlighted fields.")
    else:
        form = GateEventForm(
            society=society,
            initial={"direction": GateEvent.Direction.INBOUND},
        )

    return render(
        request,
        "gateops/gate_event_form.html",
        _base_context(
            request,
            society=society,
            form=form,
            form_title="New Gate Event",
            submit_label="Record Arrival",
            active_tab="events",
        ),
    )


def gate_event_detail_view(request, uuid):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    event = _gate_event_or_404(society, uuid)
    approvals = event.approvals.select_related("decided_by", "requested_from").order_by("-requested_at")
    photos = event.photos.all()
    documents = event.documents.all()
    return render(
        request,
        "gateops/gate_event_detail.html",
        _base_context(
            request,
            society=society,
            event=event,
            approvals=approvals,
            photos=photos,
            documents=documents,
            status_badge=_gate_event_status_badge(event.status),
            active_tab="events",
        ),
    )


def gate_event_record_exit_view(request, uuid):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    event = _gate_event_or_404(society, uuid)
    try:
        GateEventLifecycleService.record_exit(event, guard=None)
    except ValidationError as exc:
        messages.error(request, f"Could not record exit: {exc}")
    else:
        messages.success(request, f"Exit recorded for event {event.event_uuid}.")
    return redirect("gateops:event-detail", uuid=event.event_uuid)


def gate_event_approve_view(request, uuid):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    event = _gate_event_or_404(society, uuid)
    actor = request.user if request.user.is_authenticated else None
    try:
        GateEventLifecycleService.approve(event, approved_by=actor)
    except ValidationError as exc:
        messages.error(request, f"Could not approve event: {exc}")
    else:
        messages.success(request, f"Event {event.event_uuid} approved.")
    return redirect("gateops:event-detail", uuid=event.event_uuid)


def gate_event_reject_view(request, uuid):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    event = _gate_event_or_404(society, uuid)
    actor = request.user if request.user.is_authenticated else None
    try:
        GateEventLifecycleService.reject(event, decided_by=actor)
    except ValidationError as exc:
        messages.error(request, f"Could not reject event: {exc}")
    else:
        messages.success(request, f"Event {event.event_uuid} rejected.")
    return redirect("gateops:event-detail", uuid=event.event_uuid)


@login_required
def currently_inside_view(request):
    """Phase 12 — enhanced with filtering, pagination, and cached count.

    Delegates to :meth:`ExitManagementService.get_currently_inside` for the
    paginated/filtered query and :meth:`ExitManagementService.get_currently_inside_count`
    for the cached badge count. The template renders the filter form, the
    results table (with duration + overstay badge), and pagination controls.
    """
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    filter_form = CurrentlyInsideFilterForm(request.GET or None)
    filters = {}
    if filter_form.is_valid():
        cleaned = filter_form.cleaned_data
        if cleaned.get("gate"):
            filters["gate_id"] = cleaned["gate"]
        if cleaned.get("visitor_category"):
            filters["visitor_category_id"] = cleaned["visitor_category"]
        if cleaned.get("min_duration") is not None:
            filters["min_duration_minutes"] = cleaned["min_duration"]
        if cleaned.get("max_duration") is not None:
            filters["max_duration_minutes"] = cleaned["max_duration"]
        if cleaned.get("is_overstay"):
            filters["is_overstay"] = True
        if cleaned.get("search"):
            filters["search"] = cleaned["search"]
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    result = ExitManagementService.get_currently_inside(
        society=society, filters=filters, page=page, page_size=50
    )
    return render(
        request,
        "gateops/currently_inside.html",
        _base_context(
            request,
            society=society,
            active_tab="inside",
            results=result["results"],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            filter_form=filter_form,
            filters=filters,
            inside_count=ExitManagementService.get_currently_inside_count(society=society),
            gates=Gate.objects.filter(society=society, is_active=True),
            visitor_categories=VisitorCategory.objects.filter(
                society=society, is_active=True
            ),
            status_badge=_gate_event_status_badge,
        ),
    )


# ---------------------------------------------------------------------------
# Phase 5: Pass Management
# ---------------------------------------------------------------------------
#
# These views are the thin HTTP layer over :class:`PassService`. They follow
# the established patterns in this module:
#
# - Society is resolved first via ``_selected_society_or_missing`` (multi-tenant
#   safety: every query is scoped by ``society``).
# - Single-object fetches use ``get_object_or_404`` scoped by society.
# - State-changing operations are POST-only and wrap the service call in a
#   ``try/except ValidationError`` block, surfacing errors via ``messages``.
# - Redirects target the named ``gateops:pass-detail`` URL.
#
# Rendering is intentionally minimal (plain ``HttpResponse``) so that this
# phase stays within the admin/views/urls scope; templates can be layered on
# later without changing the view signatures or control flow.


def _pass_or_404(society, pk):
    """Return the society-scoped :class:`Pass` or raise Http404."""
    return get_object_or_404(Pass, pk=pk, society=society)


def _pass_detail_url(pass_obj):
    return redirect("gateops:pass-detail", pk=pass_obj.pk)


@login_required
def pass_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    passes = (
        Pass.objects.filter(society=society, is_active=True)
        .select_related("person", "pass_type")
        .order_by("-created_at")
    )
    lines = [f"Passes for {society.name} ({passes.count()})"]
    for p in passes:
        lines.append(
            f"[{p.pk}] {p.code} | {p.person.name} | {p.pass_type.name} | "
            f"{p.get_status_display()} | valid {p.valid_from:%Y-%m-%d %H:%M} "
            f"-> {p.valid_until:%Y-%m-%d %H:%M} | usage {p.usage_count}/"
            f"{p.max_usage if p.max_usage is not None else 'unlimited'}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def pass_detail_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pass_obj = _pass_or_404(society, pk)
    info = (
        f"Pass {pass_obj.code} (id={pass_obj.pk})\n"
        f"Society: {pass_obj.society.name}\n"
        f"Person: {pass_obj.person.name} ({pass_obj.person.phone})\n"
        f"Type: {pass_obj.pass_type.name} ({pass_obj.pass_type.validation_method})\n"
        f"Status: {pass_obj.get_status_display()} (is_valid={pass_obj.is_valid})\n"
        f"Valid: {pass_obj.valid_from:%Y-%m-%d %H:%M} -> "
        f"{pass_obj.valid_until:%Y-%m-%d %H:%M}\n"
        f"Usage: {pass_obj.usage_count}/"
        f"{pass_obj.max_usage if pass_obj.max_usage is not None else 'unlimited'}\n"
        f"Active: {pass_obj.is_active}\n"
        f"Created: {pass_obj.created_at:%Y-%m-%d %H:%M}\n"
    )
    return HttpResponse(info, content_type="text/plain")


@login_required
def pass_issue_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        pass_type_id = request.POST.get("pass_type_id")
        person_id = request.POST.get("person_id")
        valid_from_raw = request.POST.get("valid_from", "").strip()
        max_usage_raw = request.POST.get("max_usage", "").strip()

        try:
            pass_type = PassType.objects.get(
                pk=pass_type_id, society=society, is_active=True
            )
            person = Person.objects.get(pk=person_id, society=society)
        except (PassType.DoesNotExist, Person.DoesNotExist):
            messages.error(request, "Invalid pass type or person for this society.")
            return redirect("gateops:pass-issue")

        valid_from = None
        if valid_from_raw:
            try:
                valid_from = timezone.datetime.fromisoformat(valid_from_raw)
                if timezone.is_naive(valid_from):
                    valid_from = timezone.make_aware(valid_from)
            except ValueError:
                messages.error(request, "Invalid valid_from datetime format.")
                return redirect("gateops:pass-issue")

        max_usage = None
        if max_usage_raw:
            try:
                max_usage = int(max_usage_raw)
            except ValueError:
                messages.error(request, "max_usage must be an integer.")
                return redirect("gateops:pass-issue")

        try:
            pass_obj = PassService.generate(
                pass_type=pass_type,
                person=person,
                valid_from=valid_from,
                max_usage=max_usage,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("gateops:pass-issue")

        messages.success(request, f"Pass {pass_obj.code} issued.")
        return _pass_detail_url(pass_obj)

    # GET: render a minimal form context listing available pass types/persons.
    pass_types = PassType.objects.filter(society=society, is_active=True).order_by("name")
    persons = Person.objects.filter(society=society).order_by("name")
    lines = [f"Issue a pass for {society.name}", "", "Pass types:"]
    for pt in pass_types:
        lines.append(f"  [{pt.pk}] {pt.name} ({pt.validation_method})")
    lines.append("")
    lines.append("Persons:")
    for pr in persons:
        lines.append(f"  [{pr.pk}] {pr.name} ({pr.phone})")
    lines.append("")
    lines.append(
        "POST fields: pass_type_id, person_id, valid_from (ISO 8601, optional), "
        "max_usage (int, optional)"
    )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def pass_revoke_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pass_obj = _pass_or_404(society, pk)
    try:
        PassService.revoke(
            pass_obj=pass_obj,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Pass {pass_obj.code} revoked.")
    return _pass_detail_url(pass_obj)


@login_required
def pass_suspend_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pass_obj = _pass_or_404(society, pk)
    try:
        PassService.suspend(
            pass_obj=pass_obj,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Pass {pass_obj.code} suspended.")
    return _pass_detail_url(pass_obj)


@login_required
def pass_reactivate_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pass_obj = _pass_or_404(society, pk)
    try:
        PassService.reactivate(pass_obj=pass_obj, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Pass {pass_obj.code} reactivated.")
    return _pass_detail_url(pass_obj)


@login_required
def pass_validate_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pass_obj = _pass_or_404(society, pk)
    try:
        PassService.validate(society=society, code=pass_obj.code)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Pass is valid.")
    return _pass_detail_url(pass_obj)


# ---------------------------------------------------------------------------
# Phase 6: Vehicle Module
# ---------------------------------------------------------------------------
#
# These views are the thin HTTP layer over :class:`VehicleService`. They follow
# the established patterns in this module:
#
# - Society is resolved first via ``_selected_society_or_missing`` (multi-tenant
#   safety: every query is scoped by ``society``).
# - Single-object fetches use ``get_object_or_404`` scoped by society.
# - State-changing operations are POST-only and wrap the service call in a
#   ``try/except ValidationError`` block, surfacing errors via ``messages``.
# - Redirects target the named ``gateops:vehicle-detail`` URL.
#
# Rendering is intentionally minimal (plain ``HttpResponse``) so that this
# phase stays within the admin/views/urls scope; templates can be layered on
# later without changing the view signatures or control flow.


def _gate_vehicle_or_404(society, pk):
    """Return the society-scoped :class:`GateVehicle` or raise Http404.

    Cross-society lookups raise ``Http404`` so a vehicle id from one society
    never leaks into another tenant's console.
    """
    return get_object_or_404(GateVehicle, pk=pk, society=society, is_active=True)


def _vehicle_detail_url(vehicle):
    return redirect("gateops:vehicle-detail", pk=vehicle.pk)


@login_required
def vehicle_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    vehicles = VehicleService.get_recent(society=society, limit=100)
    watchlisted = VehicleService.get_watchlisted(society=society)
    context = _base_context(
        request,
        society=society,
        vehicles=vehicles,
        watchlisted_count=watchlisted.count(),
    )
    return render(request, "gateops/vehicle_list.html", context)


@login_required
def vehicle_detail_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    vehicle = _gate_vehicle_or_404(society, pk)
    context = _base_context(request, society=society, vehicle=vehicle)
    return render(request, "gateops/vehicle_detail.html", context)


@login_required
def vehicle_register_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        # Normalize legacy POST keys (person_id / vehicle_category_id) to the
        # ModelForm field names (person / vehicle_category) so older callers
        # and the existing view test keep working alongside the crispy form.
        post = request.POST.copy()
        if "person" not in post and post.get("person_id"):
            post["person"] = post["person_id"]
        if "vehicle_category" not in post and post.get("vehicle_category_id"):
            post["vehicle_category"] = post["vehicle_category_id"]

        form = VehicleRegisterForm(post, society=society)
        if form.is_valid():
            try:
                vehicle = VehicleService.register_or_create(
                    society=society,
                    vehicle_number=form.cleaned_data["vehicle_number"],
                    person=form.cleaned_data["person"],
                    vehicle_category=form.cleaned_data["vehicle_category"],
                    notes=form.cleaned_data.get("notes", ""),
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request, f"Vehicle {vehicle.vehicle_number} registered."
                )
                return _vehicle_detail_url(vehicle)
        # Invalid form or service ValidationError: re-render with errors.
        context = _base_context(
            request, society=society, form=form
        )
        return render(request, "gateops/vehicle_form.html", context)

    # GET: render the crispy registration form scoped to this society.
    form = VehicleRegisterForm(society=society)
    context = _base_context(request, society=society, form=form)
    return render(request, "gateops/vehicle_form.html", context)


@login_required
def vehicle_watchlist_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    vehicle = _gate_vehicle_or_404(society, pk)
    reason = request.POST.get("reason", "")
    try:
        VehicleService.add_to_watchlist(
            gate_vehicle=vehicle,
            reason=reason,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Vehicle {vehicle.vehicle_number} watchlisted.")
    return _vehicle_detail_url(vehicle)


@login_required
def vehicle_unwatchlist_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    vehicle = _gate_vehicle_or_404(society, pk)
    try:
        VehicleService.remove_from_watchlist(
            gate_vehicle=vehicle,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Vehicle {vehicle.vehicle_number} removed from watchlist.")
    return _vehicle_detail_url(vehicle)


@login_required
def vehicle_search_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    query = request.GET.get("q", "")
    results = VehicleService.search(society=society, query=query)
    watchlisted = VehicleService.get_watchlisted(society=society)
    context = _base_context(
        request,
        society=society,
        vehicles=results,
        watchlisted_count=watchlisted.count(),
        search_query=query,
    )
    return render(request, "gateops/vehicle_list.html", context)


@login_required
def vehicle_anpr_lookup_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    plate_text = request.POST.get("plate_text", "")
    result = VehicleService.anpr_lookup(society=society, plate_text=plate_text)
    # API-like endpoint: return the result dict as a simple text response.
    # ``result["vehicle"]`` is a model instance (or None); serialize it safely.
    payload = {
        "found": result["found"],
        "watchlisted": result["watchlisted"],
        "category_code": result["category_code"],
    }
    if result["vehicle"] is not None:
        vehicle = result["vehicle"]
        payload["vehicle"] = {
            "id": vehicle.pk,
            "vehicle_number": vehicle.vehicle_number,
            "person": vehicle.person.name,
            "person_phone": vehicle.person.phone,
            "category": vehicle.vehicle_category.code,
            "is_repeat": vehicle.is_repeat,
            "last_seen_at": (
                vehicle.last_seen_at.isoformat() if vehicle.last_seen_at else None
            ),
        }
    else:
        payload["vehicle"] = None
    body = _json(payload)
    return HttpResponse(body, content_type="application/json")


# ---------------------------------------------------------------------------
# Phase 7: Material Movement
# ---------------------------------------------------------------------------
#
# These views are the thin HTTP layer over :class:`MaterialService`. They
# follow the established patterns in this module:
#
# - Society is resolved first via ``_selected_society_or_missing`` (multi-tenant
#   safety: every query is scoped by ``society``).
# - Single-object fetches use ``get_object_or_404`` scoped by society.
# - State-changing operations are POST-only and wrap the service call in a
#   ``try/except ValidationError`` block, surfacing errors via ``messages``.
# - Redirects target the named ``gateops:material-detail`` URL.
#
# Rendering is intentionally minimal (plain ``HttpResponse``) so that this
# phase stays within the admin/views/urls scope; templates can be layered on
# later without changing the view signatures or control flow.


def _material_movement_or_404(society, pk):
    """Return the society-scoped :class:`MaterialMovement` or raise Http404.

    Cross-society lookups raise ``Http404`` so a movement id from one society
    never leaks into another tenant's console.
    """
    return get_object_or_404(MaterialMovement, pk=pk, society=society, is_active=True)


def _material_detail_url(movement):
    return redirect("gateops:material-detail", pk=movement.pk)


@login_required
def material_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    movements = (
        MaterialMovement.objects.filter(society=society, is_active=True)
        .select_related("gate_event", "material_category")
        .order_by("-created_at")[:100]
    )
    lines = [f"Material movements for {society.name} ({movements.count()})"]
    for m in movements:
        expected_return = (
            f"{m.expected_return_at:%Y-%m-%d %H:%M}" if m.expected_return_at else "none"
        )
        lines.append(
            f"[{m.pk}] {m.quantity} {m.unit} | {m.material_category.code} | "
            f"{m.get_status_display()} | owner={m.owner or '(none)'} | "
            f"expected_return={expected_return}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def material_detail_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    movement = _material_movement_or_404(society, pk)
    expected_return = (
        movement.expected_return_at.strftime("%Y-%m-%d %H:%M")
        if movement.expected_return_at
        else "none"
    )
    returned_at = (
        movement.returned_at.strftime("%Y-%m-%d %H:%M")
        if movement.returned_at
        else "none"
    )
    info = (
        f"MaterialMovement {movement.pk}\n"
        f"Society: {movement.society.name}\n"
        f"Gate event: {movement.gate_event.event_uuid}\n"
        f"Category: {movement.material_category.name} ({movement.material_category.code})\n"
        f"Quantity: {movement.quantity} {movement.unit}\n"
        f"Owner: {movement.owner or '(none)'}\n"
        f"Purpose: {movement.purpose or '(none)'}\n"
        f"Status: {movement.get_status_display()} (is_overdue={movement.is_overdue})\n"
        f"Expected return: {expected_return}\n"
        f"Returned at: {returned_at}\n"
        f"Active: {movement.is_active}\n"
        f"Created: {movement.created_at:%Y-%m-%d %H:%M}\n"
    )
    return HttpResponse(info, content_type="text/plain")


@login_required
def material_record_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        gate_event_id = request.POST.get("gate_event_id")
        material_category_id = request.POST.get("material_category_id")
        quantity_raw = (request.POST.get("quantity") or "").strip()
        unit = request.POST.get("unit", "unit")
        owner = request.POST.get("owner", "")
        purpose = request.POST.get("purpose", "")
        expected_return_raw = (request.POST.get("expected_return_at") or "").strip()

        if not quantity_raw:
            messages.error(request, "Quantity is required.")
            return redirect("gateops:material-record")

        try:
            quantity = Decimal(quantity_raw)
        except InvalidOperation:
            messages.error(request, "Quantity must be a valid decimal number.")
            return redirect("gateops:material-record")

        try:
            gate_event = GateEvent.objects.get(pk=gate_event_id, society=society)
            material_category = MaterialCategory.objects.get(
                pk=material_category_id, society=society
            )
        except (GateEvent.DoesNotExist, MaterialCategory.DoesNotExist):
            messages.error(request, "Invalid gate event or material category for this society.")
            return redirect("gateops:material-record")

        expected_return_at = None
        if expected_return_raw:
            try:
                expected_return_at = timezone.datetime.fromisoformat(expected_return_raw)
                if timezone.is_naive(expected_return_at):
                    expected_return_at = timezone.make_aware(expected_return_at)
            except ValueError:
                messages.error(request, "Invalid expected_return_at datetime format.")
                return redirect("gateops:material-record")

        try:
            movement = MaterialService.record_movement(
                gate_event=gate_event,
                material_category=material_category,
                quantity=quantity,
                unit=unit,
                owner=owner,
                purpose=purpose,
                expected_return_at=expected_return_at,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("gateops:material-record")

        messages.success(request, f"Material movement recorded: {movement.quantity} {movement.unit}")
        return _material_detail_url(movement)

    # GET: render a minimal form context listing available material categories.
    material_categories = MaterialCategory.objects.filter(
        society=society, is_active=True
    ).order_by("name")
    lines = [f"Record a material movement for {society.name}", "", "Material categories:"]
    for mc in material_categories:
        lines.append(f"  [{mc.pk}] {mc.name} ({mc.code})")
    lines.append("")
    lines.append(
        "POST fields: gate_event_id, material_category_id, quantity (decimal), "
        "unit (optional), owner (optional), purpose (optional), "
        "expected_return_at (ISO 8601, optional)"
    )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def material_return_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    movement = _material_movement_or_404(society, pk)
    returned_at_raw = (request.POST.get("returned_at") or "").strip()
    returned_at = None
    if returned_at_raw:
        try:
            returned_at = timezone.datetime.fromisoformat(returned_at_raw)
            if timezone.is_naive(returned_at):
                returned_at = timezone.make_aware(returned_at)
        except ValueError:
            messages.error(request, "Invalid returned_at datetime format.")
            return _material_detail_url(movement)
    try:
        MaterialService.record_return(
            movement=movement,
            returned_at=returned_at,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Material movement {movement.pk} returned.")
    return _material_detail_url(movement)


@login_required
def material_cancel_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    movement = _material_movement_or_404(society, pk)
    reason = request.POST.get("reason", "")
    try:
        MaterialService.cancel_movement(
            movement=movement,
            actor=request.user,
            reason=reason,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Material movement {movement.pk} cancelled.")
    return _material_detail_url(movement)


@login_required
def material_gate_pass_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    movement = _material_movement_or_404(society, pk)
    gate_pass_code = MaterialService.generate_gate_pass(
        movement=movement,
        actor=request.user,
    )
    return HttpResponse(gate_pass_code, content_type="text/plain")


@login_required
def material_pending_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pending = MaterialService.get_pending_returns(society=society)
    lines = [f"Pending material returns for {society.name} ({pending.count()})"]
    for m in pending:
        expected_return = (
            m.expected_return_at.strftime("%Y-%m-%d %H:%M")
            if m.expected_return_at
            else "none"
        )
        lines.append(
            f"[{m.pk}] {m.quantity} {m.unit} | {m.material_category.code} | "
            f"{m.get_status_display()} | owner={m.owner or '(none)'} | "
            f"expected_return={expected_return}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def material_overdue_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    overdue = MaterialService.get_overdue(society=society)
    lines = [f"Overdue material movements for {society.name} ({overdue.count()})"]
    for m in overdue:
        expected_return = (
            m.expected_return_at.strftime("%Y-%m-%d %H:%M")
            if m.expected_return_at
            else "none"
        )
        lines.append(
            f"[{m.pk}] {m.quantity} {m.unit} | {m.material_category.code} | "
            f"owner={m.owner or '(none)'} | expected_return={expected_return}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


# ---------------------------------------------------------------------------
# Phase 8: Parcel Management
# ---------------------------------------------------------------------------
#
# These views are the thin HTTP layer over :class:`ParcelService`. They follow
# the established patterns in this module:
#
# - Society is resolved first via ``_selected_society_or_missing`` (multi-tenant
#   safety: every query is scoped by ``society``).
# - Single-object fetches use ``get_object_or_404`` scoped by society, excluding
#   soft-deleted parcels (``is_active=True``).
# - State-changing operations are POST-only and wrap the service call in a
#   ``try/except ValidationError`` block, surfacing errors via ``messages``.
# - Redirects target the named ``gateops:parcel-detail`` URL.
#
# Rendering is intentionally minimal (plain ``HttpResponse``) so that this
# phase stays within the admin/views/urls scope; templates can be layered on
# later without changing the view signatures or control flow.


def _parcel_or_404(society, pk):
    """Return the society-scoped :class:`Parcel` or raise Http404.

    Cross-society lookups raise ``Http404`` so a parcel id from one society
    never leaks into another tenant's console. Soft-deleted parcels
    (``is_active=False``) are excluded so they cannot be mutated.
    """
    return get_object_or_404(Parcel, pk=pk, society=society, is_active=True)


def _parcel_detail_url(parcel):
    return redirect("gateops:parcel-detail", pk=parcel.pk)


@login_required
def parcel_list_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    parcels = (
        Parcel.objects.filter(society=society, is_active=True)
        .select_related("gate_event")
        .order_by("-created_at")[:100]
    )
    lines = [f"Parcels for {society.name} ({parcels.count()})"]
    for p in parcels:
        stored_at = (
            p.stored_at.strftime("%Y-%m-%d %H:%M") if p.stored_at else "none"
        )
        lines.append(
            f"[{p.pk}] {p.tracking_number} | {p.courier or '(none)'} | "
            f"{p.get_status_display()} | cold={p.is_cold_storage} | "
            f"fragile={p.is_fragile} | cod={p.is_cod} | stored={stored_at}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def parcel_detail_view(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    parcel = _parcel_or_404(society, pk)
    stored_at = (
        parcel.stored_at.strftime("%Y-%m-%d %H:%M") if parcel.stored_at else "none"
    )
    collected_at = (
        parcel.collected_at.strftime("%Y-%m-%d %H:%M")
        if parcel.collected_at
        else "none"
    )
    info = (
        f"Parcel {parcel.tracking_number} (id={parcel.pk})\n"
        f"Society: {parcel.society.name}\n"
        f"Gate event: {parcel.gate_event.event_uuid}\n"
        f"Tracking number: {parcel.tracking_number}\n"
        f"Courier: {parcel.courier or '(none)'}\n"
        f"Status: {parcel.get_status_display()} (is_pending={parcel.is_pending})\n"
        f"Cold storage: {parcel.is_cold_storage}\n"
        f"Fragile: {parcel.is_fragile}\n"
        f"COD: {parcel.is_cod}"
        + (f" (amount={parcel.cod_amount})" if parcel.is_cod else "")
        + f"\n"
        f"Stored at: {stored_at}\n"
        f"Collected at: {collected_at}\n"
        f"Active: {parcel.is_active}\n"
        f"Created: {parcel.created_at:%Y-%m-%d %H:%M}\n"
    )
    return HttpResponse(info, content_type="text/plain")


@login_required
def parcel_receive_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        gate_event_id = request.POST.get("gate_event_id")
        tracking_number = (request.POST.get("tracking_number") or "").strip()
        courier = request.POST.get("courier", "")
        is_cold_storage = bool(request.POST.get("is_cold_storage"))
        is_fragile = bool(request.POST.get("is_fragile"))
        is_cod = bool(request.POST.get("is_cod"))
        cod_amount_raw = (request.POST.get("cod_amount") or "").strip()

        if not tracking_number:
            messages.error(request, "Tracking number is required.")
            return redirect("gateops:parcel-receive")

        try:
            gate_event = GateEvent.objects.get(pk=gate_event_id, society=society)
        except GateEvent.DoesNotExist:
            messages.error(request, "Invalid gate event for this society.")
            return redirect("gateops:parcel-receive")

        cod_amount = None
        if is_cod:
            try:
                cod_amount = Decimal(cod_amount_raw)
            except InvalidOperation:
                messages.error(request, "COD amount must be a valid decimal number.")
                return redirect("gateops:parcel-receive")

        try:
            parcel = ParcelService.receive_parcel(
                gate_event=gate_event,
                tracking_number=tracking_number,
                courier=courier,
                is_cold_storage=is_cold_storage,
                is_fragile=is_fragile,
                is_cod=is_cod,
                cod_amount=cod_amount,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("gateops:parcel-receive")

        messages.success(request, f"Parcel {parcel.tracking_number} received.")
        return _parcel_detail_url(parcel)

    # GET: render a minimal form context listing recent gate events.
    events = (
        GateEvent.objects.filter(society=society)
        .order_by("-created_at")[:20]
    )
    lines = [f"Receive a parcel for {society.name}", "", "Recent gate events:"]
    for ev in events:
        lines.append(f"  [{ev.pk}] {ev.event_uuid}")
    lines.append("")
    lines.append(
        "POST fields: gate_event_id, tracking_number, courier (optional), "
        "is_cold_storage (truthy), is_fragile (truthy), is_cod (truthy), "
        "cod_amount (decimal, required when is_cod is set)"
    )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def parcel_collect_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    parcel = _parcel_or_404(society, pk)
    otp_code = (request.POST.get("otp_code") or "").strip()
    try:
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=otp_code,
            collected_by=request.user,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Parcel {parcel.tracking_number} collected.")
    return _parcel_detail_url(parcel)


@login_required
def parcel_return_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    parcel = _parcel_or_404(society, pk)
    reason = request.POST.get("reason", "")
    try:
        ParcelService.return_parcel(
            parcel=parcel,
            actor=request.user,
            reason=reason,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Parcel {parcel.tracking_number} returned.")
    return _parcel_detail_url(parcel)


@login_required
def parcel_mark_lost_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    parcel = _parcel_or_404(society, pk)
    try:
        ParcelService.mark_lost(
            parcel=parcel,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Parcel {parcel.tracking_number} marked as lost.")
    return _parcel_detail_url(parcel)


@login_required
def parcel_pending_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    pending = ParcelService.get_pending(society=society)
    lines = [f"Pending parcels for {society.name} ({pending.count()})"]
    for p in pending:
        stored_at = (
            p.stored_at.strftime("%Y-%m-%d %H:%M") if p.stored_at else "none"
        )
        lines.append(
            f"[{p.pk}] {p.tracking_number} | {p.courier or '(none)'} | "
            f"{p.get_status_display()} | cold={p.is_cold_storage} | "
            f"fragile={p.is_fragile} | cod={p.is_cod} | stored={stored_at}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


@login_required
def parcel_overdue_view(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    overdue = ParcelService.get_overdue(society=society)
    lines = [f"Overdue parcels for {society.name} ({overdue.count()})"]
    for p in overdue:
        stored_at = (
            p.stored_at.strftime("%Y-%m-%d %H:%M") if p.stored_at else "none"
        )
        lines.append(
            f"[{p.pk}] {p.tracking_number} | {p.courier or '(none)'} | "
            f"stored={stored_at}"
        )
    return HttpResponse("\n".join(lines), content_type="text/plain")


# ---------------------------------------------------------------------------
# Phase 9: Contractor Management
# ---------------------------------------------------------------------------
#
# These views are the thin HTTP layer over :class:`ContractorService`. They
# follow the established patterns in this module:
#
# - Society is resolved first via ``_selected_society_or_missing`` (multi-tenant
#   safety: every query is scoped by ``society``).
# - Single-object fetches delegate to ``ContractorService.get_*`` (which uses
#   ``get_object_or_404`` scoped by society + ``is_active=True``).
# - State-changing operations are POST-only and wrap the service call in a
#   ``try/except ValidationError`` block, surfacing errors via ``messages``.
# - Create/edit views render crispy-forms templates on GET and redirect to the
#   detail view on success.
# - Redirects target the named ``gateops:contractor-detail`` /
#   ``gateops:contract-detail`` / ``gateops:worker-detail`` /
#   ``gateops:work-permit-detail`` URLs.


def _contractor_detail_url(contractor):
    return redirect("gateops:contractor-detail", pk=contractor.pk)


def _contract_detail_url(contract):
    return redirect("gateops:contract-detail", pk=contract.pk)


def _worker_detail_url(worker):
    return redirect("gateops:worker-detail", pk=worker.pk)


def _work_permit_detail_url(work_permit):
    return redirect("gateops:work-permit-detail", pk=work_permit.pk)


# ------------------------------------------------------------------ #
# Contractor views
# ------------------------------------------------------------------ #


@login_required
def contractor_list(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contractors = ContractorService.list_contractors(society=society)
    context = _base_context(request, society=society, contractors=contractors)
    return render(request, "gateops/contractor_list.html", context)


@login_required
def contractor_detail(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contractor = ContractorService.get_contractor(society=society, pk=pk)
    contracts = ContractorService.list_contracts(society=society, contractor=contractor)
    context = _base_context(
        request,
        society=society,
        contractor=contractor,
        contracts=contracts,
    )
    return render(request, "gateops/contractor_detail.html", context)


@login_required
def contractor_create(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    if request.method == "POST":
        form = ContractorForm(request.POST, society=society)
        if form.is_valid():
            try:
                contractor = ContractorService.create_contractor(
                    society=society,
                    company_name=form.cleaned_data["company_name"],
                    supervisor_name=form.cleaned_data.get("supervisor_name", ""),
                    supervisor_phone=form.cleaned_data.get("supervisor_phone", ""),
                    contact_person=form.cleaned_data.get("contact_person", ""),
                    contact_phone=form.cleaned_data.get("contact_phone", ""),
                    gst_number=form.cleaned_data.get("gst_number", ""),
                    pan_number=form.cleaned_data.get("pan_number", ""),
                    address=form.cleaned_data.get("address", ""),
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Contractor {contractor.company_name} created.",
                )
                return _contractor_detail_url(contractor)
        context = _base_context(request, society=society, form=form)
        return render(request, "gateops/contractor_form.html", context)

    form = ContractorForm(society=society)
    context = _base_context(request, society=society, form=form)
    return render(request, "gateops/contractor_form.html", context)


@login_required
def contractor_edit(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contractor = ContractorService.get_contractor(society=society, pk=pk)

    if request.method == "POST":
        form = ContractorForm(request.POST, instance=contractor, society=society)
        if form.is_valid():
            try:
                ContractorService.update_contractor(
                    contractor=contractor,
                    actor=request.user,
                    company_name=form.cleaned_data["company_name"],
                    supervisor_name=form.cleaned_data.get("supervisor_name", ""),
                    supervisor_phone=form.cleaned_data.get("supervisor_phone", ""),
                    contact_person=form.cleaned_data.get("contact_person", ""),
                    contact_phone=form.cleaned_data.get("contact_phone", ""),
                    gst_number=form.cleaned_data.get("gst_number", ""),
                    pan_number=form.cleaned_data.get("pan_number", ""),
                    address=form.cleaned_data.get("address", ""),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Contractor {contractor.company_name} updated.",
                )
                return _contractor_detail_url(contractor)
        context = _base_context(
            request, society=society, form=form, contractor=contractor
        )
        return render(request, "gateops/contractor_form.html", context)

    form = ContractorForm(instance=contractor, society=society)
    context = _base_context(
        request, society=society, form=form, contractor=contractor
    )
    return render(request, "gateops/contractor_form.html", context)


@login_required
def contractor_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contractor = ContractorService.get_contractor(society=society, pk=pk)
    try:
        ContractorService.deactivate_contractor(
            contractor=contractor, actor=request.user
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Contractor {contractor.company_name} deactivated.",
        )
    return redirect("gateops:contractor-list")


# ------------------------------------------------------------------ #
# Contract views
# ------------------------------------------------------------------ #


@login_required
def contract_list(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contractor_pk = request.GET.get("contractor")
    contractor = None
    if contractor_pk:
        contractor = ContractorService.get_contractor(
            society=society, pk=contractor_pk
        )
    contracts = ContractorService.list_contracts(
        society=society, contractor=contractor
    )
    context = _base_context(
        request,
        society=society,
        contracts=contracts,
        filter_contractor=contractor,
    )
    return render(request, "gateops/contract_list.html", context)


@login_required
def contract_detail(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contract = ContractorService.get_contract(society=society, pk=pk)
    workers = ContractorService.list_workers(society=society, contract=contract)
    work_permits = ContractorService.list_work_permits(
        society=society, contract=contract
    )
    labour_count = ContractorService.get_labour_count(contract=contract)
    context = _base_context(
        request,
        society=society,
        contract=contract,
        workers=workers,
        work_permits=work_permits,
        labour_count=labour_count,
    )
    return render(request, "gateops/contract_detail.html", context)


@login_required
def contract_create(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    initial = {}
    contractor_pk = request.GET.get("contractor")
    if contractor_pk:
        initial["contractor"] = contractor_pk

    if request.method == "POST":
        form = ContractForm(request.POST, society=society)
        if form.is_valid():
            try:
                contract = ContractorService.create_contract(
                    society=society,
                    contractor=form.cleaned_data["contractor"],
                    title=form.cleaned_data["title"],
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    max_workers=form.cleaned_data.get("max_workers", 10),
                    description=form.cleaned_data.get("description", ""),
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request, f"Contract {contract.title} created."
                )
                return _contract_detail_url(contract)
        context = _base_context(request, society=society, form=form)
        return render(request, "gateops/contract_form.html", context)

    form = ContractForm(society=society, initial=initial)
    context = _base_context(request, society=society, form=form)
    return render(request, "gateops/contract_form.html", context)


@login_required
def contract_edit(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contract = ContractorService.get_contract(society=society, pk=pk)

    if request.method == "POST":
        form = ContractForm(request.POST, instance=contract, society=society)
        if form.is_valid():
            try:
                ContractorService.update_contract(
                    contract=contract,
                    actor=request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    max_workers=form.cleaned_data["max_workers"],
                    status=form.cleaned_data["status"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request, f"Contract {contract.title} updated."
                )
                return _contract_detail_url(contract)
        context = _base_context(
            request, society=society, form=form, contract=contract
        )
        return render(request, "gateops/contract_form.html", context)

    form = ContractForm(instance=contract, society=society)
    context = _base_context(
        request, society=society, form=form, contract=contract
    )
    return render(request, "gateops/contract_form.html", context)


@login_required
def contract_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contract = ContractorService.get_contract(society=society, pk=pk)
    try:
        ContractorService.deactivate_contract(
            contract=contract, actor=request.user
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, f"Contract {contract.title} deactivated."
        )
    return redirect("gateops:contract-list")


# ------------------------------------------------------------------ #
# Worker views
# ------------------------------------------------------------------ #


@login_required
def worker_list(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contract_pk = request.GET.get("contract")
    contract = None
    if contract_pk:
        contract = ContractorService.get_contract(society=society, pk=contract_pk)
    workers = ContractorService.list_workers(society=society, contract=contract)
    context = _base_context(
        request,
        society=society,
        workers=workers,
        filter_contract=contract,
    )
    return render(request, "gateops/worker_list.html", context)


@login_required
def worker_detail(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    worker = ContractorService.get_worker(society=society, pk=pk)
    gate_events = (
        GateEvent.objects.filter(
            society=society,
            person=worker.person,
        )
        .select_related("contractor", "contract", "work_permit")
        .order_by("-created_at")[:20]
    )
    context = _base_context(
        request,
        society=society,
        worker=worker,
        gate_events=gate_events,
    )
    return render(request, "gateops/worker_detail.html", context)


@login_required
def worker_register(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    initial = {}
    contract_pk = request.GET.get("contract")
    if contract_pk:
        initial["contract"] = contract_pk

    if request.method == "POST":
        form = WorkerForm(request.POST, society=society)
        if form.is_valid():
            try:
                worker = ContractorService.register_worker(
                    society=society,
                    contract=form.cleaned_data["contract"],
                    person=form.cleaned_data["person"],
                    designation=form.cleaned_data.get("designation", ""),
                    id_type=form.cleaned_data.get("id_type", ""),
                    id_number=form.cleaned_data.get("id_number", ""),
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Worker {worker.person.name} registered.",
                )
                return _worker_detail_url(worker)
        context = _base_context(request, society=society, form=form)
        return render(request, "gateops/worker_form.html", context)

    form = WorkerForm(society=society, initial=initial)
    context = _base_context(request, society=society, form=form)
    return render(request, "gateops/worker_form.html", context)


@login_required
def worker_edit(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    worker = ContractorService.get_worker(society=society, pk=pk)

    if request.method == "POST":
        form = WorkerForm(request.POST, instance=worker, society=society)
        if form.is_valid():
            # The service layer has no update_worker method; persist via the
            # form (society-scoped) so the audit trail is consistent with the
            # model-level clean() validation.
            try:
                worker = form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Worker {worker.person.name} updated.",
                )
                return _worker_detail_url(worker)
        context = _base_context(
            request, society=society, form=form, worker=worker
        )
        return render(request, "gateops/worker_form.html", context)

    form = WorkerForm(instance=worker, society=society)
    context = _base_context(
        request, society=society, form=form, worker=worker
    )
    return render(request, "gateops/worker_form.html", context)


@login_required
def worker_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    worker = ContractorService.get_worker(society=society, pk=pk)
    try:
        ContractorService.deactivate_worker(
            worker=worker, actor=request.user
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Worker {worker.person.name} deactivated.",
        )
    return redirect("gateops:worker-list")


# ------------------------------------------------------------------ #
# Work Permit views
# ------------------------------------------------------------------ #


@login_required
def work_permit_list(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    contract_pk = request.GET.get("contract")
    contract = None
    if contract_pk:
        contract = ContractorService.get_contract(society=society, pk=contract_pk)
    work_permits = ContractorService.list_work_permits(
        society=society, contract=contract
    )
    context = _base_context(
        request,
        society=society,
        work_permits=work_permits,
        filter_contract=contract,
    )
    return render(request, "gateops/work_permit_list.html", context)


@login_required
def work_permit_detail(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    work_permit = ContractorService.get_work_permit(society=society, pk=pk)
    expiry_info = ContractorService.check_work_permit_expiry(
        work_permit=work_permit
    )
    context = _base_context(
        request,
        society=society,
        work_permit=work_permit,
        expiry_info=expiry_info,
    )
    return render(request, "gateops/work_permit_detail.html", context)


@login_required
def work_permit_issue(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing

    initial = {}
    contract_pk = request.GET.get("contract")
    if contract_pk:
        initial["contract"] = contract_pk

    if request.method == "POST":
        form = WorkPermitForm(request.POST, society=society)
        if form.is_valid():
            try:
                work_permit = ContractorService.issue_work_permit(
                    society=society,
                    contract=form.cleaned_data["contract"],
                    permit_number=form.cleaned_data["permit_number"],
                    issued_at=form.cleaned_data["issued_at"],
                    expires_at=form.cleaned_data["expires_at"],
                    safety_docs_verified=form.cleaned_data.get(
                        "safety_docs_verified", False
                    ),
                    safety_briefing_given=form.cleaned_data.get(
                        "safety_briefing_given", False
                    ),
                    work_area=form.cleaned_data.get("work_area", ""),
                    hazard_level=form.cleaned_data.get("hazard_level", "low"),
                    notes=form.cleaned_data.get("notes", ""),
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Work permit {work_permit.permit_number} issued.",
                )
                return _work_permit_detail_url(work_permit)
        context = _base_context(request, society=society, form=form)
        return render(request, "gateops/work_permit_form.html", context)

    form = WorkPermitForm(society=society, initial=initial)
    context = _base_context(request, society=society, form=form)
    return render(request, "gateops/work_permit_form.html", context)


@login_required
def work_permit_edit(request, pk):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    work_permit = ContractorService.get_work_permit(society=society, pk=pk)

    if request.method == "POST":
        form = WorkPermitForm(request.POST, instance=work_permit, society=society)
        if form.is_valid():
            # The service layer has no update_work_permit method; persist via
            # the form (society-scoped) so model-level clean() validation runs.
            try:
                work_permit = form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Work permit {work_permit.permit_number} updated.",
                )
                return _work_permit_detail_url(work_permit)
        context = _base_context(
            request, society=society, form=form, work_permit=work_permit
        )
        return render(request, "gateops/work_permit_form.html", context)

    form = WorkPermitForm(instance=work_permit, society=society)
    context = _base_context(
        request, society=society, form=form, work_permit=work_permit
    )
    return render(request, "gateops/work_permit_form.html", context)


@login_required
def work_permit_revoke(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    work_permit = ContractorService.get_work_permit(society=society, pk=pk)
    try:
        ContractorService.revoke_work_permit(
            work_permit=work_permit, actor=request.user
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Work permit {work_permit.permit_number} revoked.",
        )
    return _work_permit_detail_url(work_permit)


# ------------------------------------------------------------------ #
# Contractor dashboard (command center)
# ------------------------------------------------------------------ #


@login_required
def contractor_dashboard(request):
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    expired_contracts = ContractorService.get_expired_contracts(society=society)
    expired_permits = ContractorService.get_expired_work_permits(society=society)
    active_workers_on_site = ContractorService.get_active_workers_on_site(
        society=society
    )
    active_contracts = ContractorService.list_contracts(society=society)
    context = _base_context(
        request,
        society=society,
        expired_contracts_count=expired_contracts.count(),
        expired_permits_count=expired_permits.count(),
        active_workers_on_site_count=active_workers_on_site.count(),
        active_contracts_count=active_contracts.count(),
    )
    return render(request, "gateops/contractor_dashboard.html", context)


# ---------------------------------------------------------------------------
# Phase 12 — Exit Management views
# ---------------------------------------------------------------------------
#
# Thin HTTP layer over :class:`ExitManagementService` and
# :class:`ShiftHandoverService`. The exit transition itself is delegated to
# :meth:`GateEventLifecycleService.record_exit` — no view sets ``status=EXITED``
# directly.
#
# Patterns (matching the rest of this module):
# - Society resolved first via ``_selected_society_or_missing``.
# - POST-only mutation endpoints return ``HttpResponseNotAllowed(["POST"])`` on
#   GET.
# - ``ValidationError`` from services is surfaced via ``messages.error``.
# - Success redirects to the relevant list/detail page.


@login_required
def quick_exit_view(request):
    """POST-only one-tap exit by GateEvent UUID or PK."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    form = QuickExitForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Invalid input: {form.errors.as_text()}")
        return redirect("gateops:currently-inside")
    gate_event_id = form.cleaned_data["gate_event_id"]
    actor = request.user if request.user.is_authenticated else None
    try:
        event = ExitManagementService.process_quick_exit(
            society=society, gate_event_id=gate_event_id, guard=None, actor=actor
        )
    except (GateEvent.DoesNotExist, ValidationError) as exc:
        messages.error(request, f"Could not process exit: {exc}")
        return redirect("gateops:currently-inside")
    messages.success(request, f"Exit recorded for event {event.event_uuid}.")
    return redirect("gateops:currently-inside")


@login_required
def qr_exit_scan_view(request):
    """GET form page where the guard enters/scans a QR code."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    form = QrExitForm()
    return render(
        request,
        "gateops/qr_exit_scan.html",
        _base_context(request, society=society, active_tab="inside", form=form),
    )


@login_required
def qr_exit_view(request):
    """POST-only QR-code-based exit (Pass code or GateEvent UUID)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    form = QrExitForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Invalid input: {form.errors.as_text()}")
        return redirect("gateops:qr-exit-scan")
    qr_code = form.cleaned_data["qr_code"]
    actor = request.user if request.user.is_authenticated else None
    try:
        event = ExitManagementService.process_qr_exit(
            society=society, qr_code=qr_code, guard=None, actor=actor
        )
    except (GateEvent.DoesNotExist, ValidationError) as exc:
        messages.error(request, f"Could not process QR exit: {exc}")
        return redirect("gateops:qr-exit-scan")
    messages.success(request, f"Exit recorded via QR for event {event.event_uuid}.")
    return redirect("gateops:currently-inside")


@login_required
def handover_list_view(request):
    """List shift handovers for the society with optional status/gate filters."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    status = request.GET.get("status") or None
    gate_id = request.GET.get("gate") or None
    gate = None
    if gate_id:
        gate = get_object_or_404(Gate, pk=gate_id, society=society)
    handovers = ShiftHandoverService.list_handovers(
        society=society, status=status, gate=gate, include_inactive=False
    )
    return render(
        request,
        "gateops/handover_list.html",
        _base_context(
            request,
            society=society,
            active_tab="handovers",
            handovers=handovers,
            status_filter=status,
            gate_filter=gate_id,
            gates=Gate.objects.filter(society=society, is_active=True),
            status_choices=ShiftHandover.Status.choices,
        ),
    )


@login_required
def handover_create_view(request):
    """Create a shift handover (outgoing → incoming guard)."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if request.method == "POST":
        form = ShiftHandoverForm(request.POST, society=society)
        if form.is_valid():
            actor = request.user if request.user.is_authenticated else None
            try:
                handover = ShiftHandoverService.create_shift_handover(
                    society=society,
                    outgoing_guard=form.cleaned_data["outgoing_guard"],
                    incoming_guard=form.cleaned_data["incoming_guard"],
                    gate=form.cleaned_data["gate"],
                    shift=form.cleaned_data.get("shift"),
                    outgoing_notes=form.cleaned_data.get("outgoing_notes", ""),
                    actor=actor,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Shift handover created.")
                return redirect("gateops:handover-detail", uuid=handover.handover_uuid)
    else:
        form = ShiftHandoverForm(society=society)
    return render(
        request,
        "gateops/handover_form.html",
        _base_context(request, society=society, active_tab="handovers", form=form),
    )


@login_required
def handover_detail_view(request, uuid):
    """Render a handover with its snapshot items and acknowledge/dispute forms."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    handover = ShiftHandoverService.get_handover(society=society, handover_id=uuid)
    if handover is None:
        raise Http404("Shift handover not found.")
    items = ShiftHandoverService.get_handover_items(society=society, handover_id=uuid)
    acknowledge_form = HandoverAcknowledgeForm()
    dispute_form = HandoverDisputeForm()
    return render(
        request,
        "gateops/handover_detail.html",
        _base_context(
            request,
            society=society,
            active_tab="handovers",
            handover=handover,
            items=items,
            acknowledge_form=acknowledge_form,
            dispute_form=dispute_form,
        ),
    )


@login_required
def handover_acknowledge_view(request, uuid):
    """POST-only acknowledgement of a pending/disputed handover."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    handover = ShiftHandoverService.get_handover(society=society, handover_id=uuid)
    if handover is None:
        raise Http404("Shift handover not found.")
    form = HandoverAcknowledgeForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Invalid input: {form.errors.as_text()}")
        return redirect("gateops:handover-detail", uuid=uuid)
    actor = request.user if request.user.is_authenticated else None
    try:
        ShiftHandoverService.acknowledge_handover(
            society=society,
            handover_id=uuid,
            incoming_guard=handover.incoming_guard,
            notes=form.cleaned_data.get("notes", ""),
            actor=actor,
        )
    except ValidationError as exc:
        messages.error(request, f"Could not acknowledge handover: {exc}")
    else:
        messages.success(request, "Handover acknowledged.")
    return redirect("gateops:handover-detail", uuid=uuid)


@login_required
def handover_dispute_view(request, uuid):
    """POST-only dispute of a pending handover with a mandatory reason."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    handover = ShiftHandoverService.get_handover(society=society, handover_id=uuid)
    if handover is None:
        raise Http404("Shift handover not found.")
    form = HandoverDisputeForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Invalid input: {form.errors.as_text()}")
        return redirect("gateops:handover-detail", uuid=uuid)
    actor = request.user if request.user.is_authenticated else None
    try:
        ShiftHandoverService.dispute_handover(
            society=society,
            handover_id=uuid,
            incoming_guard=handover.incoming_guard,
            reason=form.cleaned_data["reason"],
            actor=actor,
        )
    except ValidationError as exc:
        messages.error(request, f"Could not dispute handover: {exc}")
    else:
        messages.success(request, "Handover disputed.")
    return redirect("gateops:handover-detail", uuid=uuid)


# --- Phase 13: Analytics -------------------------------------------------


def _check_analytics_permission(request):
    """Return True if the user can view analytics, False otherwise.

    Superusers / super-admins always pass.  Otherwise the user's
    :class:`GateOpsRole` for the selected society is consulted via its
    JSON ``permissions`` field.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_super_admin", False):
        return True
    society = getattr(request, "current_society", None)
    if society is None:
        society, _ = get_selected_scope(request, persist=True)
    if society is None:
        return False
    role = (
        GateOpsRole.objects.filter(
            society=society,
            is_active=True,
            deleted_at__isnull=True,
        )
        .first()
    )
    # GateOpsRole is society-scoped, not user-scoped; the JSON permissions
    # represent the active role configuration for the society.  When a
    # per-user role link is introduced, this lookup will be tightened.
    return bool(role and role.has_perm("can_view_analytics"))


@login_required
def analytics_dashboard_view(request):
    """Phase 13 analytics landing page with summary cards."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    today = timezone.localdate()
    live = AnalyticsService.get_live_visitors(society=society)
    anomaly_stats = AnalyticsService.get_anomaly_stats(
        society=society, date_from=today, date_to=today
    )
    peak = AnalyticsService.get_peak_hours(
        society=society, date_from=today, date_to=today
    )

    context = _base_context(
        request,
        society=society,
        active_tab="analytics",
        live_count=live["count"],
        anomaly_open=anomaly_stats["by_status"].get("OPEN", 0),
        peak_hour=peak["peak_hour"],
        peak_hour_count=peak["peak_hour_count"],
        today=today,
    )
    return render(request, "gateops/analytics_dashboard.html", context)


@login_required
def analytics_live_visitors_view(request):
    """AJAX endpoint returning JSON of visitors currently inside."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return JsonResponse({"error": "No society selected"}, status=400)
    if not _check_analytics_permission(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    filters = {}
    gate_id = request.GET.get("gate_id")
    if gate_id:
        filters["gate_id"] = gate_id
    visitor_category_id = request.GET.get("visitor_category_id")
    if visitor_category_id:
        filters["visitor_category_id"] = visitor_category_id

    data = AnalyticsService.get_live_visitors(society=society, filters=filters)
    return JsonResponse(data)


@login_required
def analytics_peak_hours_view(request):
    """Hourly traffic distribution chart with predicted overlay."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    form = AnalyticsDateRangeForm(request.GET or None, society=society)
    today = timezone.localdate()
    date_from = today - timedelta(days=7)
    date_to = today
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from") or date_from
        date_to = form.cleaned_data.get("date_to") or date_to

    data = AnalyticsService.get_peak_hours(
        society=society, date_from=date_from, date_to=date_to
    )
    context = _base_context(
        request, society=society, active_tab="analytics", data=data, form=form
    )
    return render(request, "gateops/analytics_peak_hours.html", context)


@login_required
def analytics_guard_performance_view(request):
    """Per-guard throughput metrics table and chart."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    form = AnalyticsDateRangeForm(request.GET or None, society=society)
    today = timezone.localdate()
    date_from = today - timedelta(days=7)
    date_to = today
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from") or date_from
        date_to = form.cleaned_data.get("date_to") or date_to

    data = AnalyticsService.get_guard_performance(
        society=society, date_from=date_from, date_to=date_to
    )
    context = _base_context(
        request, society=society, active_tab="analytics", data=data, form=form
    )
    return render(request, "gateops/analytics_guard_performance.html", context)


@login_required
def analytics_custom_report_view(request):
    """Filterable custom report of gate events with summary table."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    form = AnalyticsCustomReportForm(request.GET or None, society=society)
    today = timezone.localdate()
    date_from = today - timedelta(days=7)
    date_to = today
    metrics = ["total_events", "by_status", "by_visitor_category"]
    group_by = None
    filters = {}
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from") or date_from
        date_to = form.cleaned_data.get("date_to") or date_to
        metrics = form.cleaned_data.get("metrics") or metrics
        group_by = form.cleaned_data.get("group_by") or None
        gate = form.cleaned_data.get("gate")
        visitor_category = form.cleaned_data.get("visitor_category")
        event_type = form.cleaned_data.get("event_type")
        status = form.cleaned_data.get("status")
        if gate:
            filters["gate_id"] = gate.id
        if visitor_category:
            filters["visitor_category_id"] = visitor_category.id
        if event_type:
            filters["event_type"] = event_type
        if status:
            filters["status"] = status

    data = AnalyticsService.get_custom_report(
        society=society,
        metrics=metrics,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
        filters=filters,
    )
    context = _base_context(
        request, society=society, active_tab="analytics", data=data, form=form
    )
    return render(request, "gateops/analytics_custom_report.html", context)


@login_required
def analytics_rule_violations_view(request):
    """Rule violation statistics with action distribution and daily trend."""
    society, missing = _selected_society_or_missing(request)
    if missing:
        return missing
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    form = AnalyticsDateRangeForm(request.GET or None, society=society)
    today = timezone.localdate()
    date_from = today - timedelta(days=7)
    date_to = today
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from") or date_from
        date_to = form.cleaned_data.get("date_to") or date_to

    data = AnalyticsService.get_rule_violation_stats(
        society=society, date_from=date_from, date_to=date_to
    )
    anomaly_data = AnalyticsService.get_anomaly_stats(
        society=society, date_from=date_from, date_to=date_to
    )
    context = _base_context(
        request,
        society=society,
        active_tab="analytics",
        data=data,
        anomaly_data=anomaly_data,
        form=form,
    )
    return render(request, "gateops/analytics_rule_violations.html", context)


@login_required
def analytics_export_view(request):
    """POST-only CSV export of analytics data."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    society, missing = _selected_society_or_missing(request)
    if missing:
        return HttpResponseBadRequest("No society selected")
    if not _check_analytics_permission(request):
        return HttpResponseForbidden("You do not have permission to view analytics.")

    form = AnalyticsExportForm(request.POST, society=society)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid export parameters")

    export_type = form.cleaned_data["export_type"]
    date_from = form.cleaned_data["date_from"]
    date_to = form.cleaned_data["date_to"]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="gateops_{export_type}_{date_from}_{date_to}.csv"'
    )
    writer = csv.writer(response)

    if export_type == "events":
        data = AnalyticsService.get_custom_report(
            society=society,
            metrics=["total_events"],
            date_from=date_from,
            date_to=date_to,
        )
        writer.writerow([
            "Gate Event ID", "UUID", "Person", "Visitor Category", "Gate",
            "Event Type", "Status", "Arrived At", "Entered At", "Exited At",
            "Duration (min)", "Vehicle Number",
        ])
        for event in data["items"]:
            writer.writerow([
                event["gate_event_id"], event["gate_event_uuid"],
                event["person_name"], event["visitor_category"],
                event["gate_name"], event["event_type"], event["status"],
                event["arrived_at"], event["entered_at"], event["exited_at"],
                event["duration_minutes"], event["vehicle_number"],
            ])
    elif export_type == "guard_performance":
        data = AnalyticsService.get_guard_performance(
            society=society, date_from=date_from, date_to=date_to
        )
        writer.writerow([
            "Guard ID", "Guard Name", "Entries", "Exits", "Approvals",
            "Rejections", "Rule Violations", "Avg Processing Time (ms)",
        ])
        for guard in data["guards"]:
            writer.writerow([
                guard["guard_id"], guard["guard_name"],
                guard["entries_processed"], guard["exits_processed"],
                guard["approvals_given"], guard["rejections_given"],
                guard["rule_violations_triggered"],
                guard["avg_processing_time_ms"],
            ])
    elif export_type == "rule_violations":
        data = AnalyticsService.get_rule_violation_stats(
            society=society, date_from=date_from, date_to=date_to
        )
        writer.writerow(["Action", "Count"])
        for action, count in data["by_action"].items():
            writer.writerow([action, count])
        writer.writerow([])
        writer.writerow(["Rule ID", "Rule Name", "Violation Count"])
        for rule in data["top_violated_rules"]:
            writer.writerow([
                rule["rule_id"], rule["rule_name"], rule["violation_count"],
            ])
    elif export_type == "anomalies":
        data = AnalyticsService.get_anomaly_stats(
            society=society, date_from=date_from, date_to=date_to
        )
        writer.writerow(["Type", "Count"])
        for atype, count in data["by_type"].items():
            writer.writerow([atype, count])
        writer.writerow([])
        writer.writerow(["Severity", "Count"])
        for severity, count in data["by_severity"].items():
            writer.writerow([severity, count])

    _audit(
        request,
        society,
        "EXPORT",
        "AnalyticsExport",
        None,
        after_value={
            "export_type": export_type,
            "start": date_from.isoformat(),
            "end": date_to.isoformat(),
        },
    )
    return response

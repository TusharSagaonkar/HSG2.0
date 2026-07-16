from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import DecimalField
from django.db.models import Count
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django.views.generic import FormView
from django.views import View
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import JsonResponse

from housing.forms import SocietyConfigForm
from housing.forms import SocietyForm
from housing.forms import SocietyEmailSettingsForm
from housing.forms import SocietyProfileForm
from housing.forms import SocietyUserCreationForm
from housing.forms import StructureForm
from housing.forms import BulkUnitCreateForm
from housing.forms import UnitForm
from housing.forms import UnitOccupancyForm
from housing.forms import UnitOwnershipForm
from housing.forms import MemberForm
from housing.forms import ChargeTemplateForm
from housing.forms import BillingGenerationForm
from housing.forms import ReceiptPostingForm
from housing.forms import VoucherTemplateForm
from housing.forms import VoucherTemplateRowFormSet
from housing.services import sync_member_unit_lifecycle
from societies.models import Society
from societies.models import SocietyConfig
from societies.models import Membership
from onboarding.models import OnboardingWizard
from notifications.models import EmailVerificationToken
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from billing.models import Bill
from billing.models import ChargeTemplate
from billing.services import generate_bills_for_period
from billing.reports import build_member_outstanding
from receipts.models import PaymentReceipt
from receipts.services import post_receipt_for_bill
from accounting.models import Account
from accounting.models import VoucherTemplate
from notifications.services import schedule_payment_reminders
from notifications.models import GlobalEmailSettings
from notifications.models import SocietyEmailSettings
from housing_accounting.selection import get_selected_scope
from societies.services import create_society
from societies.permissions import has_role_or_above
from societies.permissions import has_permission
from societies.roles import ROLE_ADMIN
from societies.roles import ROLE_OWNER
from societies.utils import get_user_role
from auditlog.models import AuditLog



class StructureUnitDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "housing/structure_unit_dashboard.html"

    @staticmethod
    def _user_display_name(user):
        if user is None:
            return ""
        return user.name or user.email or str(user)

    def _unit_owner_display_name(self, unit, owner):
        if owner is None:
            return ""
        member = (
            Member.objects.filter(unit=unit, user=owner)
            .order_by("full_name", "id")
            .first()
        )
        if member and member.full_name:
            return member.full_name
        return self._user_display_name(owner)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)
        params = self.request.GET

        structures_qs = Structure.objects.select_related("society", "parent")
        units_qs = Unit.objects.select_related("structure", "structure__society").prefetch_related(
            Prefetch(
                "occupancies",
                queryset=UnitOccupancy.objects.filter(end_date__isnull=True).order_by(
                    "-start_date", "-id"
                ),
                to_attr="active_occupancies",
            )
        )
        active_occupancies_qs = UnitOccupancy.objects.filter(end_date__isnull=True)

        if selected_society:
            structures_qs = structures_qs.filter(society=selected_society)
            units_qs = units_qs.filter(structure__society=selected_society)
            active_occupancies_qs = active_occupancies_qs.filter(
                unit__structure__society=selected_society
            )

        search = params.get("q", "").strip()
        if search:
            units_qs = units_qs.filter(
                Q(identifier__icontains=search)
                | Q(structure__name__icontains=search)
                | Q(structure__society__name__icontains=search)
            )

        unit_type = params.get("unit_type", "").strip()
        if unit_type:
            units_qs = units_qs.filter(unit_type=unit_type)

        status = params.get("status", "").strip()
        if status == "active":
            units_qs = units_qs.filter(is_active=True)
        elif status == "inactive":
            units_qs = units_qs.filter(is_active=False)

        occupancy = params.get("occupancy", "").strip()
        if occupancy == "occupied":
            units_qs = units_qs.filter(
                pk__in=UnitOccupancy.objects.filter(
                    end_date__isnull=True
                ).exclude(
                    occupancy_type=UnitOccupancy.OccupancyType.VACANT
                ).values("unit_id")
            )
        elif occupancy == "vacant":
            units_qs = units_qs.filter(
                pk__in=UnitOccupancy.objects.filter(
                    end_date__isnull=True,
                    occupancy_type=UnitOccupancy.OccupancyType.VACANT,
                ).values("unit_id")
            )

        structure_id = params.get("structure", "").strip()
        if structure_id.isdigit():
            units_qs = units_qs.filter(structure_id=int(structure_id))

        society_id = params.get("society", "").strip()
        if society_id.isdigit() and not selected_society:
            units_qs = units_qs.filter(structure__society_id=int(society_id))

        sort = params.get("sort", "structure")
        sort_map = {
            "structure": ("structure__society__name", "structure__name", "identifier"),
            "identifier": ("identifier", "structure__name"),
            "created": ("-created_at",),
            "area": ("-chargeable_area_sqft", "-area_sqft", "identifier"),
            "active": ("-is_active", "structure__name", "identifier"),
        }
        units_qs = units_qs.order_by(*sort_map.get(sort, sort_map["structure"]))

        structures_qs = structures_qs.order_by("society__name", "name")
        societies_qs = Society.objects.order_by("name")
        if selected_society:
            societies_qs = societies_qs.filter(pk=selected_society.pk)
        elif society_id.isdigit():
            societies_qs = societies_qs.filter(pk=int(society_id))

        total_structures = structures_qs.count()
        root_structures = structures_qs.filter(parent__isnull=True).count()
        total_units = units_qs.count()
        active_units = units_qs.filter(is_active=True).count()
        occupied_units = active_occupancies_qs.exclude(
            occupancy_type=UnitOccupancy.OccupancyType.VACANT,
        ).count()

        context["total_structures"] = total_structures
        context["root_structures"] = root_structures
        context["total_units"] = total_units
        context["active_units"] = active_units
        context["occupied_units"] = occupied_units
        context["vacant_units"] = max(total_units - occupied_units, 0)
        context["recent_structures"] = structures_qs.order_by("-created_at")[:6]
        context["recent_units"] = units_qs.order_by("-created_at")[:8]
        unit_type_summary = (
            units_qs.values("unit_type")
            .annotate(total=Count("id"))
            .order_by("unit_type")
        )
        unit_type_labels = dict(Unit.UnitType.choices)
        context["unit_type_summary"] = [
            {
                "unit_type": unit_type_labels.get(row["unit_type"], row["unit_type"]),
                "total": row["total"],
            }
            for row in unit_type_summary
        ]
        units_list = list(units_qs)
        unit_ids = [unit.id for unit in units_list]

        active_ownerships = (
            UnitOwnership.objects.filter(
                unit_id__in=unit_ids,
                end_date__isnull=True,
            )
            .select_related("owner")
            .order_by("unit_id", "role", "-start_date", "-id")
        )
        primary_ownership_by_unit = {}
        for ownership in active_ownerships:
            if ownership.role != UnitOwnership.OwnershipRole.PRIMARY:
                continue
            primary_ownership_by_unit.setdefault(ownership.unit_id, ownership)

        active_owner_members = (
            Member.objects.filter(
                unit_id__in=unit_ids,
                role=Member.MemberRole.OWNER,
                status=Member.MemberStatus.ACTIVE,
            )
            .order_by("unit_id", "full_name", "id")
        )
        owner_member_by_unit = {}
        for member in active_owner_members:
            owner_member_by_unit.setdefault(member.unit_id, member)

        active_tenant_members = (
            Member.objects.filter(
                unit_id__in=unit_ids,
                role=Member.MemberRole.TENANT,
                status=Member.MemberStatus.ACTIVE,
            )
            .order_by("unit_id", "full_name", "id")
        )
        tenant_members_by_unit = {}
        for member in active_tenant_members:
            tenant_members_by_unit.setdefault(member.unit_id, member)

        for unit in units_list:
            ownership = primary_ownership_by_unit.get(unit.id)
            owner_member = owner_member_by_unit.get(unit.id)
            unit.primary_owner_name = self._unit_owner_display_name(
                unit,
                ownership.owner if ownership else None,
            )
            if not unit.primary_owner_name and owner_member:
                unit.primary_owner_name = owner_member.full_name
            unit.primary_tenant_member = tenant_members_by_unit.get(unit.id)
            unit.primary_owner_member = owner_member

        context["units"] = units_list
        context["search"] = search
        context["selected_unit_type"] = unit_type
        context["selected_status"] = status
        context["selected_occupancy"] = occupancy
        context["selected_sort"] = sort
        context["societies"] = societies_qs
        context["structures"] = structures_qs.order_by("name")
        context["unit_types"] = Unit.UnitType.choices
        context["selected_society_id"] = str(selected_society.pk) if selected_society else ""
        context["selected_structure_id"] = structure_id if structure_id.isdigit() else ""
        return context


structure_unit_dashboard_view = StructureUnitDashboardView.as_view()


class UnitDetailView(LoginRequiredMixin, DetailView):
    model = Unit
    template_name = "housing/unit_detail.html"
    context_object_name = "unit"

    @staticmethod
    def _user_display_name(user):
        if user is None:
            return ""
        return user.name or user.email or str(user)

    def _unit_member_display_name(self, unit, user):
        if user is None:
            return ""
        member = (
            Member.objects.filter(unit=unit, user=user)
            .order_by("full_name", "id")
            .first()
        )
        if member and member.full_name:
            return member.full_name
        return self._user_display_name(user)

    def get_object(self, queryset=None):
        unit = super().get_object(queryset)
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and unit.structure.society_id != selected_society.id:
            raise Http404(_("Unit not found in selected society."))
        return unit

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        unit = self.object

        context["society"] = unit.structure.society
        context["structure"] = unit.structure
        context["ownerships"] = (
            UnitOwnership.objects.filter(unit=unit)
            .select_related("owner")
            .order_by("-start_date", "-id")
        )
        context["occupancies"] = (
            UnitOccupancy.objects.filter(unit=unit)
            .select_related("occupant")
            .order_by("-start_date", "-id")
        )
        context["members"] = (
            Member.objects.filter(unit=unit)
            .select_related("society", "unit", "receivable_account")
            .order_by("full_name", "id")
        )
        context["primary_owner"] = (
            UnitOwnership.objects.filter(
                unit=unit,
                role=UnitOwnership.OwnershipRole.PRIMARY,
                end_date__isnull=True,
            )
            .select_related("owner")
            .order_by("-start_date", "-id")
            .first()
        )
        if context["primary_owner"]:
            context["primary_owner_display_name"] = self._user_display_name(
                context["primary_owner"].owner
            )
            context["primary_owner_display_name"] = self._unit_member_display_name(
                unit,
                context["primary_owner"].owner,
            )
        context["current_occupancy"] = (
            UnitOccupancy.objects.filter(
                unit=unit,
                end_date__isnull=True,
            )
            .select_related("occupant")
            .order_by("-start_date", "-id")
            .first()
        )
        if context["current_occupancy"]:
            context["current_occupant_display_name"] = self._unit_member_display_name(
                unit,
                context["current_occupancy"].occupant,
            )
        for ownership in context["ownerships"]:
            ownership.owner_display_name = self._unit_member_display_name(unit, ownership.owner)
        for occupancy in context["occupancies"]:
            occupancy.occupant_display_name = self._unit_member_display_name(unit, occupancy.occupant)
        context["active_members"] = context["members"].filter(status=Member.MemberStatus.ACTIVE)
        return context


unit_detail_view = UnitDetailView.as_view()


class SocietyListView(LoginRequiredMixin, ListView):
    model = Society
    template_name = "housing/society_list.html"
    context_object_name = "societies"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = Society.objects.annotate(
            structure_count=Count("structures", distinct=True),
            unit_count=Count("structures__units", distinct=True),
            membership_count=Count("memberships", filter=Q(memberships__is_active=True), distinct=True),
        ).order_by("name")
        if selected_society:
            queryset = queryset.filter(pk=selected_society.pk)
        societies = list(queryset.select_related("created_by"))
        for society in societies:
            society.current_user_role = get_user_role(self.request.user, society)
        return societies


society_list_view = SocietyListView.as_view()


class SocietyDetailView(LoginRequiredMixin, DetailView):
    model = Society
    template_name = "housing/society_detail.html"
    context_object_name = "society"

    @staticmethod
    def _user_display_name(user):
        if user is None:
            return ""
        return user.name or user.email

    def _unit_owner_display_name(self, unit, owner):
        if owner is None:
            return ""
        member = (
            Member.objects.filter(unit=unit, user=owner)
            .order_by("full_name", "id")
            .first()
        )
        if member and member.full_name:
            return member.full_name
        return self._user_display_name(owner)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society = self.object
        structures = list(society.structures.all().order_by("display_order", "id"))
        units = list(
            Unit.objects.filter(structure__society=society)
            .select_related("structure")
            .order_by("structure_id", "id")
        )
        unit_ids = [unit.id for unit in units]

        active_ownerships = (
            UnitOwnership.objects.filter(
                unit_id__in=unit_ids,
                end_date__isnull=True,
            )
            .select_related("owner")
            .order_by("unit_id", "role", "-start_date", "-id")
        )
        primary_ownership_by_unit = {}
        for ownership in active_ownerships:
            if ownership.role != UnitOwnership.OwnershipRole.PRIMARY:
                continue
            primary_ownership_by_unit.setdefault(ownership.unit_id, ownership)

        active_occupancies = (
            UnitOccupancy.objects.filter(
                unit_id__in=unit_ids,
                end_date__isnull=True,
            )
            .select_related("occupant")
            .order_by("unit_id", "-start_date", "-id")
        )
        current_occupancy_by_unit = {}
        for occupancy in active_occupancies:
            current_occupancy_by_unit.setdefault(occupancy.unit_id, occupancy)

        active_owner_members = (
            Member.objects.filter(
                society=society,
                unit_id__in=unit_ids,
                role=Member.MemberRole.OWNER,
                status=Member.MemberStatus.ACTIVE,
            )
            .order_by("unit_id", "full_name", "id")
        )
        owner_member_by_unit = {}
        for member in active_owner_members:
            owner_member_by_unit.setdefault(member.unit_id, member)

        active_members = (
            Member.objects.filter(
                society=society,
                unit_id__in=unit_ids,
                status=Member.MemberStatus.ACTIVE,
            )
            .order_by("unit_id", "full_name", "id")
        )
        owner_members_by_unit = {}
        tenant_members_by_unit = {}
        for member in active_members:
            if member.role == Member.MemberRole.OWNER:
                owner_members_by_unit.setdefault(member.unit_id, []).append(member)
            if member.role == Member.MemberRole.TENANT:
                tenant_members_by_unit.setdefault(member.unit_id, []).append(member)

        children_map = {}
        for structure in structures:
            children_map.setdefault(structure.parent_id, []).append(structure)

        units_map = {}
        for unit in units:
            ownership = primary_ownership_by_unit.get(unit.id)
            occupancy = current_occupancy_by_unit.get(unit.id)
            owner_member = owner_member_by_unit.get(unit.id)
            unit.primary_owner_record = ownership
            unit.current_occupancy_record = occupancy
            unit.primary_owner_name = self._unit_owner_display_name(
                unit,
                ownership.owner if ownership else None,
            )
            if not unit.primary_owner_name and owner_member:
                unit.primary_owner_name = owner_member.full_name
            unit.current_occupant_name = self._user_display_name(
                occupancy.occupant if occupancy else None,
            )
            unit.active_owner_members = owner_members_by_unit.get(unit.id, [])
            unit.active_tenant_members = tenant_members_by_unit.get(unit.id, [])
            units_map.setdefault(unit.structure_id, []).append(unit)

        for structure in structures:
            structure.tree_children = children_map.get(structure.id, [])
            structure.tree_units = units_map.get(structure.id, [])
            structure.total_unit_count = len(structure.tree_units)
            structure.active_unit_count = sum(
                1 for unit in structure.tree_units if unit.is_active
            )
            structure.inactive_unit_count = (
                structure.total_unit_count - structure.active_unit_count
            )
            structure.occupied_unit_count = sum(
                1
                for unit in structure.tree_units
                if unit.current_occupancy_record
                and unit.current_occupancy_record.occupancy_type
                != UnitOccupancy.OccupancyType.VACANT
            )
            structure.vacant_unit_count = max(
                structure.total_unit_count - structure.occupied_unit_count,
                0,
            )
            structure.child_structure_count = len(structure.tree_children)

        context["root_structures"] = children_map.get(None, [])
        context["total_units"] = len(units)
        context["active_membership_count"] = society.memberships.filter(is_active=True).count()
        context["current_user_role"] = get_user_role(self.request.user, society)
        context["role_summary"] = [
            {"key": "owner", "label": _("Owner"), "description": _("Full control, ownership transfer, and admin governance.")},
            {"key": "admin", "label": _("Admin"), "description": _("Manage society users, operations, and day-to-day administration.")},
            {"key": "accountant", "label": _("Accountant"), "description": _("Handle accounting workflows, billing, and receipts.")},
            {"key": "member", "label": _("Member"), "description": _("Participate in society operations with limited change access.")},
            {"key": "viewer", "label": _("Viewer"), "description": _("Read-only access to society data and reports.")},
        ]
        return context


society_detail_view = SocietyDetailView.as_view()


class SocietyEmailSettingsView(LoginRequiredMixin, FormView):
    form_class = SocietyEmailSettingsForm
    template_name = "housing/society_email_settings.html"

    def get_society(self):
        return Society.objects.get(pk=self.kwargs["pk"])

    def get_email_settings(self):
        return SocietyEmailSettings.objects.filter(
            society=self.get_society(),
        ).first()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        existing_settings = self.get_email_settings()
        kwargs["instance"] = existing_settings or SocietyEmailSettings(
            society=self.get_society(),
            smtp_port=587,
            use_tls=True,
            provider_type="SMTP",
        )
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society = self.get_society()
        context["society"] = society
        context["form_title"] = _("Society Email Settings")
        context["form_subtitle"] = _(
            "Configure whether this society uses its own SMTP credentials or inherits the platform default."
        )
        context["cancel_url"] = reverse("housing:society-detail", kwargs={"pk": society.pk})
        context["cancel_label"] = _("Back to Society")
        context["global_email_settings"] = GlobalEmailSettings.objects.filter(active=True).first()
        context["society_email_settings"] = self.get_email_settings()
        return context

    def form_valid(self, form):
        society = self.get_society()
        existing_settings = self.get_email_settings()
        if existing_settings is None and not form.cleaned_data.get("is_active") and not form.has_override_data():
            messages.success(
                self.request,
                _("Society email override remains disabled. Global email settings will be used."),
            )
            return redirect(self.get_success_url())

        settings_record = form.save(commit=False)
        settings_record.society = society
        settings_record.save()
        messages.success(self.request, _("Society email settings saved successfully."))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("housing:society-email-settings", kwargs={"pk": self.get_society().pk})


society_email_settings_view = SocietyEmailSettingsView.as_view()


class SocietyCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = SocietyForm
    model = Society
    template_name = "housing/form.html"
    success_message = _("Society created successfully.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Society")
        context["form_subtitle"] = _("Create a new housing society record.")
        context["cancel_url"] = reverse("housing:society-list")
        context["cancel_label"] = _("Back to Societies")
        return context

    def get_success_url(self):
        return reverse("housing:society-detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        self.object = create_society(
            user=self.request.user,
            name=form.cleaned_data["name"],
            registration_number=form.cleaned_data.get("registration_number") or "",
            address=form.cleaned_data.get("address") or "",
        )
        return redirect(self.get_success_url())


society_create_view = SocietyCreateView.as_view()


class StructureCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = StructureForm
    model = Structure
    template_name = "housing/form.html"
    success_message = _("Structure created successfully.")

    def get_initial(self):
        initial = super().get_initial()
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if society_id:
            initial["society"] = society_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Structure")
        context["form_subtitle"] = _("Add building/wing/block hierarchy.")
        context["cancel_url"] = reverse("housing:structure-unit-dashboard")
        context["cancel_label"] = _("Back to Structure & Units")
        return context

    def get_success_url(self):
        return reverse("housing:society-detail", kwargs={"pk": self.object.society_id})


structure_create_view = StructureCreateView.as_view()


class UnitCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = UnitForm
    model = Unit
    template_name = "housing/form.html"
    success_message = _("Unit created successfully.")

    def get_initial(self):
        initial = super().get_initial()
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        structure_id = self.request.GET.get("structure")
        if society_id:
            initial["society"] = society_id
        if structure_id:
            initial["structure"] = structure_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Unit")
        context["form_subtitle"] = _("Create a flat, shop, office, or other unit.")
        context["cancel_url"] = reverse("housing:structure-unit-dashboard")
        context["cancel_label"] = _("Back to Structure & Units")
        return context

    def get_success_url(self):
        return reverse(
            "housing:society-detail",
            kwargs={"pk": self.object.structure.society_id},
        )


unit_create_view = UnitCreateView.as_view()


class BulkUnitCreateView(LoginRequiredMixin, FormView):
    form_class = BulkUnitCreateForm
    template_name = "housing/unit_bulk_form.html"
    success_message = _("Units created successfully.")

    def get_initial(self):
        initial = super().get_initial()
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        structure_id = self.request.GET.get("structure")
        if society_id:
            initial["society"] = society_id
        if structure_id:
            initial["structure"] = structure_id
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["initial"] = self.get_initial()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Bulk Add Units")
        context["form_subtitle"] = _(
            "Design a floor-by-floor grid, edit cells inline, and save everything in one go."
        )
        context["cancel_url"] = reverse("housing:structure-unit-dashboard")
        context["cancel_label"] = _("Back to Structure & Units")
        context["submit_label"] = _("Save Units")
        context["submit_icon"] = "fas fa-layer-group"
        context["unit_type_choices"] = Unit.UnitType.choices
        return context

    def form_valid(self, form):
        structure = form.cleaned_data["structure"]
        grid_units = form.cleaned_data["grid_units"]
        self.created_structure = structure
        units = [
            Unit(
                structure=structure,
                identifier=row["identifier"],
                unit_type=row["unit_type"],
                area_sqft=row["area_sqft"],
                chargeable_area_sqft=row["chargeable_area_sqft"],
                is_active=row["is_active"],
            )
            for row in grid_units
        ]

        with transaction.atomic():
            Unit.objects.bulk_create(units)

        messages.success(self.request, self.success_message)
        return super().form_valid(form)

    def get_success_url(self):
        structure = self.created_structure
        return reverse("housing:society-detail", kwargs={"pk": structure.society_id})

    def post(self, request, *args, **kwargs):
        self.form = self.get_form()
        if self.form.is_valid():
            return self.form_valid(self.form)
        return self.form_invalid(self.form)


bulk_unit_create_view = BulkUnitCreateView.as_view()


class UnitOwnershipCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = UnitOwnershipForm
    model = UnitOwnership
    template_name = "housing/form.html"
    success_message = _("Unit ownership saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        unit_id = self.request.GET.get("unit")
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if unit_id:
            initial["unit"] = unit_id
        if society_id:
            initial["society"] = society_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Unit Ownership")
        context["form_subtitle"] = _("Assign primary or secondary owner to a unit.")
        context["cancel_url"] = reverse("housing:structure-unit-dashboard")
        context["cancel_label"] = _("Back to Structure & Units")
        return context

    def get_success_url(self):
        return reverse(
            "housing:society-detail",
            kwargs={"pk": self.object.unit.structure.society_id},
        )


unit_ownership_create_view = UnitOwnershipCreateView.as_view()


class UnitOccupancyCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = UnitOccupancyForm
    model = UnitOccupancy
    template_name = "housing/form.html"
    success_message = _("Unit occupancy saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        unit_id = self.request.GET.get("unit")
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if unit_id:
            initial["unit"] = unit_id
        if society_id:
            initial["society"] = society_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Unit Occupancy")
        context["form_subtitle"] = _("Set owner/tenant/vacant occupancy details.")
        context["cancel_url"] = reverse("housing:structure-unit-dashboard")
        context["cancel_label"] = _("Back to Structure & Units")
        return context

    def get_success_url(self):
        return reverse(
            "housing:society-detail",
            kwargs={"pk": self.object.unit.structure.society_id},
        )


unit_occupancy_create_view = UnitOccupancyCreateView.as_view()


class MemberListView(LoginRequiredMixin, ListView):
    model = Member
    template_name = "housing/member_list.html"
    context_object_name = "members"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = Member.objects.select_related(
            "society",
            "unit",
            "unit__structure",
            "receivable_account",
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)

        q = (self.request.GET.get("q") or "").strip()
        structure = (self.request.GET.get("structure") or "").strip()
        role = (self.request.GET.get("role") or "").strip()
        status = (self.request.GET.get("status") or "").strip()

        if q:
            queryset = queryset.filter(
                Q(full_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
                | Q(unit__identifier__icontains=q)
                | Q(unit__structure__name__icontains=q)
                | Q(society__name__icontains=q)
            )

        if structure:
            try:
                queryset = queryset.filter(unit__structure_id=int(structure))
            except ValueError:
                pass

        if role in Member.MemberRole.values:
            queryset = queryset.filter(role=role)

        if status in Member.MemberStatus.values:
            queryset = queryset.filter(status=status)

        self.filter_values = {
            "q": q,
            "structure": structure,
            "role": role,
            "status": status,
        }
        return queryset.order_by("society__name", "unit__structure__name", "full_name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)
        structures = Structure.objects.select_related("society").order_by(
            "society__name",
            "name",
        )
        if selected_society:
            structures = structures.filter(society=selected_society)

        context["structure_options"] = structures
        context["filter_values"] = getattr(
            self,
            "filter_values",
            {"q": "", "structure": "", "role": "", "status": ""},
        )
        context["role_options"] = Member.MemberRole.choices
        context["status_options"] = Member.MemberStatus.choices
        return context


member_list_view = MemberListView.as_view()


class MemberCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Member
    form_class = MemberForm
    template_name = "housing/member_form.html"
    success_message = _("Member saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        society_id = self.request.GET.get("society")
        unit_id = self.request.GET.get("unit")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if society_id:
            initial["society"] = society_id
        if unit_id:
            initial["unit"] = unit_id
            unit = (
                Unit.objects.select_related("structure")
                .filter(pk=unit_id)
                .first()
            )
            if unit:
                initial["unit_search"] = unit.identifier
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        selected_society, _ = get_selected_scope(self.request)
        society_id = (
            self.request.POST.get("society")
            or self.request.GET.get("society")
            or (selected_society.pk if selected_society else None)
        )
        if society_id:
            kwargs["society"] = Society.objects.filter(pk=society_id).first()
        kwargs["current_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            sync_member_unit_lifecycle(self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # OPTIMIZATION: Pass society and unit IDs to template for modal usage
        society_id = self.request.GET.get("society") or self.request.POST.get("society")
        unit_id = self.request.GET.get("unit") or self.request.POST.get("unit")
        context["form_title"] = _("Add Member")
        context["form_subtitle"] = _("Create owner, tenant, or nominee membership.")
        context["cancel_url"] = reverse("housing:member-list")
        context["cancel_label"] = _("Back to Members")
        context["society_id"] = society_id
        context["unit_id"] = unit_id
        context["is_modal"] = self.request.GET.get("modal") or self.request.POST.get("modal")
        context["unit_search_url"] = reverse("housing:unit-search-api")
        return context

    def get_success_url(self):
        return reverse("housing:member-list")


member_create_view = MemberCreateView.as_view()


class MemberUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Member
    form_class = MemberForm
    template_name = "housing/member_form.html"
    success_message = _("Member updated successfully.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["society"] = self.get_object().society
        kwargs["current_user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Update Member")
        context["form_subtitle"] = _("Update membership details and status.")
        context["cancel_url"] = reverse("housing:member-list")
        context["cancel_label"] = _("Back to Members")
        context["unit_search_url"] = reverse("housing:unit-search-api")
        context["society_id"] = self.get_object().society_id
        return context

    def get_success_url(self):
        return reverse("housing:member-list")


member_update_view = MemberUpdateView.as_view()


class MemberFormOptionsAPIView(LoginRequiredMixin, View):
    """
    API endpoint that returns member form options in JSON format.
    Used by modal dialogs to load accounts and other options without a page reload.
    
    OPTIMIZATION: Minimal query - only loads necessary fields via select_related and only()
    """
    def get(self, request):
        society_id = request.GET.get("society_id")
        unit_id = request.GET.get("unit_id")
        
        if not society_id:
            return JsonResponse({"error": "society_id required"}, status=400)
        
        # OPTIMIZATION: Load only necessary fields
        accounts = list(
            Account.objects.filter(society_id=society_id)
            .only("id", "name")
            .order_by("name")
            .values("id", "name")
        )
        
        unit_data = None
        if unit_id:
            # OPTIMIZATION: Minimal query with select_related
            unit = Unit.objects.select_related("structure").only(
                "id", "identifier", "structure__name", "structure_id"
            ).get(pk=unit_id, structure__society_id=society_id)
            unit_data = {
                "id": unit.id,
                "identifier": unit.identifier,
                "structure_name": unit.structure.name,
            }
        
        return JsonResponse({
            "success": True,
            "accounts": accounts,
            "unit": unit_data,
            "member_roles": list(Member.MemberRole.choices),
            "member_statuses": list(Member.MemberStatus.choices),
        })


class UnitSearchAPIView(LoginRequiredMixin, View):
    def get(self, request):
        society_id = request.GET.get("society_id")
        query = (request.GET.get("q") or "").strip()
        if not society_id:
            return JsonResponse({"success": False, "error": "society_id required"}, status=400)

        units = Unit.objects.filter(structure__society_id=society_id)
        if query:
            units = units.filter(
                Q(identifier__icontains=query) | Q(structure__name__icontains=query)
            )

        units = units.select_related("structure").order_by("identifier")[:20]
        return JsonResponse(
            {
                "success": True,
                "units": [
                    {
                        "id": unit.id,
                        "identifier": unit.identifier,
                        "structure_name": unit.structure.name,
                        "label": f"{unit.identifier} / {unit.structure.name}",
                    }
                    for unit in units
                ],
            }
        )


member_form_options_api_view = MemberFormOptionsAPIView.as_view()
unit_search_api_view = UnitSearchAPIView.as_view()


class ChargeTemplateCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = ChargeTemplate
    form_class = ChargeTemplateForm
    template_name = "housing/form.html"
    success_message = _("Charge template saved successfully.")

    def _get_clone_source(self):
        clone_from = self.request.GET.get("clone_from")
        if not clone_from:
            return None
        return ChargeTemplate.objects.filter(pk=clone_from).first()

    def get_initial(self):
        initial = super().get_initial()
        society_id = self.request.GET.get("society")
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if society_id:
            initial["society"] = society_id
        clone_source = self._get_clone_source()
        if clone_source:
            initial.update(
                {
                    "society": clone_source.society_id,
                    "name": clone_source.name,
                    "description": clone_source.description,
                    "charge_type": clone_source.charge_type,
                    "rate": clone_source.rate,
                    "frequency": clone_source.frequency,
                    "due_days": clone_source.due_days,
                    "late_fee_percent": clone_source.late_fee_percent,
                    "income_account": clone_source.income_account_id,
                    "receivable_account": clone_source.receivable_account_id,
                    "is_active": True,
                }
            )
            initial["effective_from"] = (
                self.request.GET.get("effective_from")
                or timezone.localdate().isoformat()
            )
            initial["effective_to"] = None
        return initial

    def form_valid(self, form):
        clone_source = self._get_clone_source()
        if (
            clone_source
            and form.instance.society_id == clone_source.society_id
            and form.instance.name == clone_source.name
        ):
            form.instance.previous_version = clone_source
        return super().form_valid(form)

    def _get_return_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return reverse("billing:charge-template-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Charge Template")
        context["form_subtitle"] = _("Create a reusable billing charge with accounts, timing, and rates in one place.")
        context["cancel_url"] = self._get_return_url()
        context["cancel_label"] = _("Back")
        context["compact_charge_template_form"] = True
        context["return_url"] = self._get_return_url()
        context["submit_label"] = _("Save Template")
        return context

    def get_success_url(self):
        return self._get_return_url()


charge_template_create_view = ChargeTemplateCreateView.as_view()


class BillingGenerateView(LoginRequiredMixin, FormView):
    template_name = "housing/form.html"
    form_class = BillingGenerationForm

    def get_initial(self):
        initial = super().get_initial()
        query_society_id = self.request.GET.get("society")
        if query_society_id:
            query_society = Society.objects.filter(pk=query_society_id).first()
            if query_society:
                initial["society"] = query_society
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and "society" not in initial:
            initial["society"] = selected_society
        today = timezone.localdate()
        initial.setdefault("bill_date", today)
        initial.setdefault("period_start", today.replace(day=1))
        initial.setdefault("period_end", today)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Generate Bills")
        context["form_subtitle"] = _("Generate recurring bills and auto-post accounting vouchers.")
        context["cancel_url"] = reverse("billing:bill-list")
        context["cancel_label"] = _("Back to Bills")
        context["submit_label"] = _("Generate Bills")
        context["submit_icon"] = "fas fa-cogs"
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            created = generate_bills_for_period(
                society=data["society"],
                period_start=data["period_start"],
                period_end=data["period_end"],
                bill_date=data["bill_date"],
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Generated %(count)s bill(s).") % {"count": len(created)},
        )
        return redirect("housing:dashboard")


billing_generate_view = BillingGenerateView.as_view()


class ReceiptPostView(LoginRequiredMixin, FormView):
    template_name = "housing/form.html"
    form_class = ReceiptPostingForm

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        query_society_id = self.request.GET.get("society")
        if query_society_id:
            query_society = Society.objects.filter(pk=query_society_id).first()
            if query_society and (
                selected_society is None or query_society.pk == selected_society.pk
            ):
                initial["society"] = query_society
        if selected_society and "society" not in initial:
            initial["society"] = selected_society

        query_bill_id = self.request.GET.get("bill")
        if query_bill_id:
            bill = (
                Bill.objects.select_related("society", "member", "unit")
                .filter(pk=query_bill_id)
                .first()
            )
            if bill and (selected_society is None or bill.society_id == selected_society.pk):
                outstanding_amount = bill.outstanding_amount
                if outstanding_amount > 0:
                    initial["society"] = bill.society
                    initial["member"] = bill.member
                    initial["bill"] = bill
                    initial["amount"] = outstanding_amount

        initial.setdefault("receipt_date", timezone.localdate())
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Post Receipt")
        context["form_subtitle"] = _("Post member payment and auto-create receipt voucher.")
        context["cancel_url"] = reverse("receipts:receipt-list")
        context["cancel_label"] = _("Back to Receipts")
        context["submit_label"] = _("Post Receipt")
        context["submit_icon"] = "fas fa-money-check-alt"
        context["auto_reload_society"] = True
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            receipt = post_receipt_for_bill(
                society=data["society"],
                member=data["member"],
                bill=data["bill"],
                amount=data["amount"],
                receipt_date=data["receipt_date"],
                payment_mode=data["payment_mode"],
                deposited_account=data["deposited_account"],
                reference_number=data["reference_number"],
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Receipt %(id)s posted successfully.") % {"id": receipt.id},
        )
        return redirect("housing:dashboard")


receipt_post_view = ReceiptPostView.as_view()


class OutstandingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "housing/outstanding_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)
        as_of_date = timezone.localdate()
        if self.request.GET.get("as_of_date"):
            try:
                as_of_date = timezone.datetime.fromisoformat(
                    self.request.GET["as_of_date"]
                ).date()
            except ValueError:
                as_of_date = timezone.localdate()
        context["as_of_date"] = as_of_date
        if not selected_society:
            context["outstanding"] = None
            return context
        context["outstanding"] = build_member_outstanding(
            society=selected_society,
            as_of_date=as_of_date,
        )
        return context


outstanding_dashboard_view = OutstandingDashboardView.as_view()


class FinanceDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "housing/finance_dashboard.html"

    @staticmethod
    def _sum_amount(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(
                    Decimal("0.00"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
        )["total"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)
        today = timezone.localdate()
        money_field = DecimalField(max_digits=12, decimal_places=2)
        zero = Value(Decimal("0.00"), output_field=money_field)
        allocated_amount = Coalesce(Sum("receipt_allocations__amount"), zero)
        outstanding_amount = ExpressionWrapper(
            F("total_amount") - allocated_amount,
            output_field=money_field,
        )

        bills_qs = Bill.objects.select_related(
            "society",
            "member",
            "unit",
            "voucher",
            "receivable_account",
        )
        receipts_qs = PaymentReceipt.objects.select_related(
            "society",
            "member",
            "unit",
            "deposited_account",
            "voucher",
        ).prefetch_related("allocations__bill")

        if selected_society:
            bills_qs = bills_qs.filter(society=selected_society)
            receipts_qs = receipts_qs.filter(society=selected_society)

        bills_with_balance = bills_qs.annotate(
            allocated_amount_value=allocated_amount,
            outstanding_amount_value=outstanding_amount,
        )
        receipts_with_allocations = receipts_qs.annotate(
            allocation_count=Count("allocations"),
        )
        context.update(
            {
                "selected_society": selected_society,
                "today": today,
                "bill_count": bills_qs.count(),
                "open_bill_count": bills_qs.filter(status=Bill.BillStatus.OPEN).count(),
                "partial_bill_count": bills_qs.filter(status=Bill.BillStatus.PARTIAL).count(),
                "overdue_bill_count": bills_qs.filter(status=Bill.BillStatus.OVERDUE).count(),
                "total_billed": self._sum_amount(bills_qs, "total_amount"),
                "total_bill_outstanding": bills_with_balance.aggregate(
                    total=Coalesce(Sum("outstanding_amount_value"), zero)
                )["total"],
                "recent_bills": bills_with_balance.order_by("-bill_date", "-id")[:6],
                "receipt_count": receipts_qs.count(),
                "posted_receipt_count": receipts_qs.filter(
                    status=PaymentReceipt.ReceiptStatus.POSTED,
                ).count(),
                "void_receipt_count": receipts_qs.filter(
                    status=PaymentReceipt.ReceiptStatus.VOID,
                ).count(),
                "unallocated_receipt_count": receipts_with_allocations.filter(
                    allocation_count=0,
                ).count(),
                "total_collected": self._sum_amount(
                    receipts_qs.filter(status=PaymentReceipt.ReceiptStatus.POSTED),
                    "amount",
                ),
                "recent_receipts": receipts_qs.order_by("-receipt_date", "-id")[:6],
                "overdue_bills_today": bills_qs.exclude(status=Bill.BillStatus.PAID).filter(due_date__lt=today).count(),
                "due_today_bills": bills_qs.exclude(status=Bill.BillStatus.PAID).filter(due_date=today).count(),
            }
        )
        return context


finance_dashboard_view = FinanceDashboardView.as_view()


class SocietyAdminView(LoginRequiredMixin, DetailView):
    """View for managing society memberships and user roles."""
    model = Society
    template_name = "housing/society_admin.html"
    context_object_name = "society"

    def dispatch(self, request, *args, **kwargs):
        society = self.get_object()
        if not has_permission(request.user, "societies.membership.view", society):
            raise PermissionDenied("You do not have permission to manage this society.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society = self.object

        # Get all memberships for this society with user details
        memberships = Membership.objects.filter(
            society=society
        ).select_related('user').order_by('-is_active', '-joined_at')

        # Role labels mapping
        role_labels = {
            "owner": _("Owner"),
            "admin": _("Admin"),
            "accountant": _("Accountant"),
            "member": _("Member"),
            "viewer": _("Viewer"),
        }

        # Enrich memberships with role info and computed status
        for membership in memberships:
            membership.role_label = role_labels.get(membership.role, membership.role)

            # Compute combined status
            if not membership.is_active:
                membership.status_display = "Inactive"
                membership.status_badge_class = "bg-secondary"
                membership.status_icon = "fas fa-times-circle"
            elif not membership.user.email_verified:
                membership.status_display = "Pending Email Verification"
                membership.status_badge_class = "bg-warning"
                membership.status_icon = "fas fa-envelope"
            else:
                membership.status_display = "Active & Verified"
                membership.status_badge_class = "bg-success"
                membership.status_icon = "fas fa-check-circle"

        context['memberships'] = memberships
        context['role_summary'] = [
            {"key": "owner", "label": _("Owner"), "description": _("Full control, ownership transfer, and admin governance.")},
            {"key": "admin", "label": _("Admin"), "description": _("Manage society users, operations, and day-to-day administration.")},
            {"key": "accountant", "label": _("Accountant"), "description": _("Handle accounting workflows, billing, and receipts.")},
            {"key": "member", "label": _("Member"), "description": _("Participate in society operations with limited change access.")},
            {"key": "viewer", "label": _("Viewer"), "description": _("Read-only access to society data and reports.")},
        ]
        context['current_user_role'] = get_user_role(self.request.user, society)

        # --- Society configuration & profile forms ---
        # share_config is a get_or_create property on Society; safe to call.
        society_config = society.share_config
        context['society_config'] = society_config
        context['society_config_form'] = SocietyConfigForm(instance=society_config)
        context['society_profile_form'] = SocietyProfileForm(initial={
            "name": society.name,
            "registration_number": society.registration_number or "",
            "address": society.address or "",
        })

        # --- Onboarding wizard (latest for this society) ---
        # OnboardingWizard uses TenantManager which auto-filters by the current
        # tenant contextvar; use .unscoped() to query across tenants since the
        # request's current tenant may not match this society.
        onboarding_wizard = (
            OnboardingWizard.objects.unscoped()
            .filter(society=society)
            .order_by("-started_at")
            .first()
        )
        context['onboarding_wizard'] = onboarding_wizard
        if onboarding_wizard:
            # 28 total steps in the wizard; clamp progress to [0, 100].
            current_step = max(onboarding_wizard.current_step or 0, 0)
            context['onboarding_progress_percent'] = min(
                int((current_step / 28) * 100), 100
            )
        else:
            context['onboarding_progress_percent'] = 0

        # --- Quick stats ---
        context['total_members'] = Member.objects.filter(society=society).count()
        context['total_users'] = memberships.count()
        context['active_users'] = memberships.filter(is_active=True).count()
        # Pending verifications: memberships whose linked user has not verified email.
        context['pending_verifications'] = memberships.filter(
            user__email_verified=False
        ).count()

        return context


society_admin_view = SocietyAdminView.as_view()


class SocietySettingsUpdateView(LoginRequiredMixin, View):
    """POST-only view for updating a society's SocietyConfig (share/fee settings).

    Requires both the ``societies.membership.edit`` permission and an
    admin-or-above role. On success or failure, redirects back to the society
    admin page with a flash message.
    """

    def dispatch(self, request, *args, **kwargs):
        society = get_object_or_404(Society, pk=kwargs["pk"])
        if not has_permission(request.user, "societies.membership.edit", society):
            raise PermissionDenied(
                _("You do not have permission to edit society settings.")
            )
        user_role = get_user_role(request.user, society)
        if not has_role_or_above(user_role, ROLE_ADMIN):
            raise PermissionDenied(
                _("You must be an admin or owner to edit society settings.")
            )
        self.society = society
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        society = self.society
        config = society.share_config
        form = SocietyConfigForm(request.POST, instance=config)
        if form.is_valid():
            with transaction.atomic():
                saved = form.save(commit=False)
                saved.society = society
                saved.save()
                AuditLog.log(
                    society=society,
                    action=AuditLog.Action.UPDATE,
                    entity_type="society_config",
                    entity_id=saved.pk,
                    actor=request.user,
                    after_value={
                        "share_value": str(saved.share_value),
                        "default_share_count": saved.default_share_count,
                        "entrance_fee": str(saved.entrance_fee),
                        "transfer_fee": str(saved.transfer_fee),
                        "premium_amount": str(saved.premium_amount),
                        "allow_multiple_nominees": saved.allow_multiple_nominees,
                        "require_approval": saved.require_approval,
                        "auto_generate_vouchers": saved.auto_generate_vouchers,
                    },
                    module="societies",
                )
            messages.success(request, _("Society settings saved successfully."))
        else:
            messages.error(
                request,
                _("Please correct the errors in the settings form and try again."),
            )
        return redirect("housing:society-admin", pk=society.pk)


society_settings_update_view = SocietySettingsUpdateView.as_view()


class SocietyProfileUpdateView(LoginRequiredMixin, View):
    """POST-only view for updating a society's profile (name, registration, address).

    Uses a plain Form (not ModelForm) so only whitelisted fields are ever
    written back to the Society instance. Requires admin-or-above role.
    """

    def dispatch(self, request, *args, **kwargs):
        society = get_object_or_404(Society, pk=kwargs["pk"])
        if not has_permission(request.user, "societies.membership.edit", society):
            raise PermissionDenied(
                _("You do not have permission to edit the society profile.")
            )
        user_role = get_user_role(request.user, society)
        if not has_role_or_above(user_role, ROLE_ADMIN):
            raise PermissionDenied(
                _("You must be an admin or owner to edit the society profile.")
            )
        self.society = society
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        society = self.society
        form = SocietyProfileForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                society.name = form.cleaned_data["name"]
                society.registration_number = form.cleaned_data.get(
                    "registration_number", ""
                )
                society.address = form.cleaned_data.get("address", "")
                society.save()
                AuditLog.log(
                    society=society,
                    action=AuditLog.Action.UPDATE,
                    entity_type="society",
                    entity_id=society.pk,
                    actor=request.user,
                    after_value={
                        "name": society.name,
                        "registration_number": society.registration_number or "",
                        "address": society.address or "",
                    },
                    module="societies",
                )
            messages.success(request, _("Society profile updated successfully."))
        else:
            messages.error(
                request,
                _("Please correct the errors in the profile form and try again."),
            )
        return redirect("housing:society-admin", pk=society.pk)


society_profile_update_view = SocietyProfileUpdateView.as_view()


class SocietyUserCreateView(LoginRequiredMixin, FormView):
    """View for creating a new user and granting them access to a society."""
    form_class = SocietyUserCreationForm
    template_name = "housing/form.html"

    def dispatch(self, request, *args, **kwargs):
        society = self.get_society()
        if not has_permission(request.user, "societies.membership.create", society):
            raise PermissionDenied("You do not have permission to create users in this society.")
        return super().dispatch(request, *args, **kwargs)

    def get_society(self):
        return Society.objects.get(pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.get_society()
        kwargs['current_user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society = self.get_society()
        context["society"] = society
        context["form_title"] = _("Create User & Grant Access")
        context["form_subtitle"] = _("Create a new user account and assign their role in this society.")
        context["cancel_url"] = reverse("housing:society-admin", kwargs={"pk": society.pk})
        context["cancel_label"] = _("Back to Admin")
        context["submit_label"] = _("Create User")
        context["submit_icon"] = "fas fa-user-plus"
        return context

    def form_valid(self, form):
        from societies.services import create_user_by_admin
        from notifications.services import queue_email
        from django.core.exceptions import PermissionDenied
        from django.urls import reverse
        
        society = self.get_society()
        try:
            # Construct full name from first and last name
            first_name = form.cleaned_data.get('first_name', '').strip()
            last_name = form.cleaned_data.get('last_name', '').strip()
            full_name = f"{first_name} {last_name}".strip() if first_name or last_name else form.cleaned_data['email']
            
            user = create_user_by_admin(
                admin_user=self.request.user,
                society=society,
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password'],
                role=form.cleaned_data['role'],
                name=full_name,
            )
            
            # Create email verification token
            verification_token = EmailVerificationToken.create_token(user, expires_in_hours=24)
            
            # Build verification link
            verification_link = self.request.build_absolute_uri(
                reverse("housing:email-verify", kwargs={"token": verification_token.token})
            )
            
            # Queue verification email
            queue_email(
                recipient_email=user.email,
                society=society,
                template_name="authentication.user_created",
                template_subject_template="Welcome to Housing Accounting System",
                template_body_template=(
                    "Hello {{ user_name }},\n\n"
                    "Your account has been created in Housing Accounting System.\n\n"
                    "Society: {{ society_name }}\n"
                    "Email: {{ user_email }}\n"
                    "Role: {{ user_role }}\n\n"
                    "Please verify your email by clicking the link below:\n"
                    "{{ verification_link }}\n\n"
                    "This link will expire in 24 hours.\n\n"
                    "You can then login with your email and password.\n\n"
                    "Regards,\n"
                    "{{ society_name }}\n"
                ),
                template_variables=[
                    "user_name",
                    "society_name",
                    "user_email",
                    "user_role",
                    "verification_link",
                ],
                context={
                    "user_name": user.name or user.email,
                    "society_name": society.name,
                    "user_email": user.email,
                    "user_role": form.cleaned_data['role'].title(),
                    "verification_link": verification_link,
                },
                email_type="AUTHENTICATION",
            )
            
            messages.success(
                self.request,
                _("User %(email)s created successfully. Verification email sent.") % {
                    "email": form.cleaned_data['email'],
                },
            )
            return redirect("housing:society-admin", pk=society.pk)
        except PermissionDenied:
            form.add_error(None, _("You do not have permission to assign this role."))
            return self.form_invalid(form)


society_user_create_view = SocietyUserCreateView.as_view()


class EmailVerificationView(View):
    """View for verifying email addresses."""
    
    def get(self, request, token):
        """Verify email using token."""
        try:
            verification_token = EmailVerificationToken.objects.select_related('user').get(
                token=token,
                is_used=False,
            )
        except EmailVerificationToken.DoesNotExist:
            messages.error(request, _("Invalid or expired verification link."))
            return redirect("account_login")
        
        if verification_token.is_expired():
            messages.error(request, _("Verification link has expired. Please contact the administrator."))
            return redirect("account_login")
        
        if verification_token.verify():
            messages.success(
                request,
                _("Email verified successfully! You can now login."),
            )
            return redirect("account_login")
        else:
            messages.error(request, _("Email verification failed. Please try again."))
            return redirect("account_login")


email_verification_view = EmailVerificationView.as_view()


class ResendVerificationEmailView(LoginRequiredMixin, View):
    """View for resending verification email to users who haven't verified yet."""
    
    def post(self, request, society_pk, user_id):
        """Resend verification email to user."""
        from notifications.services import queue_email
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        from housing_accounting.users.models import User
        
        try:
            society = Society.objects.get(pk=society_pk)
            
            if not has_permission(request.user, "societies.membership.edit", society):
                raise PermissionDenied(_("You don't have permission to resend verification emails."))
            
            # Get the user to resend email to (must be member of the society)
            user = User.objects.get(id=user_id)
            try:
                membership = Membership.objects.get(user=user, society=society)
            except Membership.DoesNotExist:
                raise Http404(_("User is not a member of this society."))
            
            # Check if user's email is not already verified
            if user.email_verified:
                messages.warning(request, _("This user's email is already verified."))
                return redirect("housing:society-admin", pk=society_pk)
            
            # Create new verification token
            verification_token = EmailVerificationToken.create_token(user, expires_in_hours=24)
            
            # Build verification link
            verification_link = request.build_absolute_uri(
                reverse("housing:email-verify", kwargs={"token": verification_token.token})
            )
            
            # Get user role display name
            user_role_display = dict(Membership.Role.choices).get(membership.role, membership.role)
            
            # Queue verification email
            queue_email(
                recipient_email=user.email,
                society=society,
                template_name="authentication.user_created",
                template_subject_template="Email Verification - Housing Accounting System",
                template_body_template=(
                    "Hello {{ user_name }},\n\n"
                    "Please verify your email to activate your account.\n\n"
                    "Society: {{ society_name }}\n"
                    "Email: {{ user_email }}\n"
                    "Role: {{ user_role }}\n\n"
                    "Click the link below to verify your email:\n"
                    "{{ verification_link }}\n\n"
                    "This link will expire in 24 hours.\n\n"
                    "Regards,\n"
                    "{{ society_name }}\n"
                ),
                template_variables=[
                    "user_name",
                    "society_name",
                    "user_email",
                    "user_role",
                    "verification_link",
                ],
                context={
                    "user_name": user.name or user.email,
                    "society_name": society.name,
                    "user_email": user.email,
                    "user_role": user_role_display,
                    "verification_link": verification_link,
                },
                email_type="AUTHENTICATION",
            )
            
            messages.success(
                request,
                _("Verification email resent to %(email)s.") % {
                    "email": user.email,
                },
            )
            return redirect("housing:society-admin", pk=society_pk)
            
        except Society.DoesNotExist:
            raise Http404(_("Society not found."))
        except User.DoesNotExist:
            raise Http404(_("User not found."))
        except PermissionDenied as e:
            messages.error(request, str(e))
            return redirect("housing:society-admin", pk=society_pk)


resend_verification_email_view = ResendVerificationEmailView.as_view()


class UpdateMembershipView(LoginRequiredMixin, View):
    """View for updating membership role and status."""
    
    def post(self, request, society_pk, user_id):
        """Update membership role and/or status."""
        from housing.forms import UpdateMembershipForm
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        from housing_accounting.users.models import User
        
        try:
            society = Society.objects.get(pk=society_pk)
            
            if not has_permission(request.user, "societies.membership.edit", society):
                raise PermissionDenied(_("You don't have permission to update memberships."))
            
            # Get the user and their membership
            user = User.objects.get(id=user_id)
            try:
                membership = Membership.objects.get(user=user, society=society)
            except Membership.DoesNotExist:
                raise Http404(_("User is not a member of this society."))
            
            # Process form
            form = UpdateMembershipForm(request.POST, society=society, current_user=request.user, membership=membership)
            
            if form.is_valid():
                # Prevent deactivating the only owner
                new_role = form.cleaned_data['role']
                new_is_active = form.cleaned_data['is_active']
                
                if not new_is_active and membership.role == 'owner':
                    # Check if there are other active owners
                    other_active_owners = Membership.objects.filter(
                        society=society,
                        role='owner',
                        is_active=True,
                    ).exclude(id=membership.id).exists()
                    
                    if not other_active_owners:
                        messages.error(request, _("Cannot deactivate the only active owner."))
                        return redirect("housing:society-admin", pk=society_pk)
                
                # Update membership
                membership.role = new_role
                membership.is_active = new_is_active
                membership.save()
                AuditLog.log(
                    society=society,
                    action=AuditLog.Action.ROLE_CHANGE if 'role' in form.changed_data else AuditLog.Action.UPDATE,
                    entity_type="membership",
                    entity_id=membership.pk,
                    actor=request.user,
                    after_value={"role": membership.role, "is_active": membership.is_active},
                    module="societies",
                )
                
                messages.success(
                    request,
                    _("%(name)s updated successfully.") % {
                        "name": user.name or user.email,
                    },
                )
                return redirect("housing:society-admin", pk=society_pk)
            else:
                # Return form errors
                messages.error(request, _("Please correct the errors in the form."))
                return redirect("housing:society-admin", pk=society_pk)
            
        except Society.DoesNotExist:
            raise Http404(_("Society not found."))
        except User.DoesNotExist:
            raise Http404(_("User not found."))
        except PermissionDenied as e:
            messages.error(request, str(e))
            return redirect("housing:society-admin", pk=society_pk)


update_membership_view = UpdateMembershipView.as_view()


class ReminderScheduleView(LoginRequiredMixin, View):
    def post(self, request):
        selected_society = get_selected_scope(request)[0]
        if not selected_society:
            messages.error(request, _("Select a society before scheduling reminders."))
            return redirect("housing:dashboard")
        count = schedule_payment_reminders(
            society=selected_society,
            as_of_date=timezone.localdate(),
        )
        messages.success(
            request,
            _("Scheduled %(count)s reminder(s).") % {"count": count},
        )
        return redirect("housing:outstanding-dashboard")


class SocietyVoucherTemplatesView(LoginRequiredMixin, DetailView):
    """
    View for managing voucher templates for a specific society.
    """
    model = Society
    template_name = "housing/society_voucher_templates.html"
    context_object_name = "society"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not (request.user.is_superuser or getattr(request.user, "is_super_admin", False)):
            user_role = get_user_role(request.user, self.object)
            if not has_role_or_above(user_role, ROLE_ADMIN):
                raise PermissionDenied(_("You do not have permission to manage voucher templates."))
        return super().dispatch(request, *args, **kwargs)

    def _get_templates_queryset(self, society):
        return (
            VoucherTemplate.objects.filter(society=society)
            .select_related("society")
            .prefetch_related("rows__account", "rows__unit")
            .order_by(
                "-is_pinned",
                "-usage_count",
                "sort_order",
                "voucher_type",
                "name",
                "id",
            )
        )

    def _resolve_editor_template(self, society):
        edit_id = self.request.GET.get("edit")
        if not edit_id:
            return None
        try:
            return VoucherTemplate.objects.get(pk=int(edit_id), society=society)
        except (VoucherTemplate.DoesNotExist, TypeError, ValueError):
            return None

    def _resolve_copy_source(self, society):
        copy_id = self.request.GET.get("copy")
        if not copy_id:
            return None
        try:
            return VoucherTemplate.objects.prefetch_related("rows__account", "rows__unit").get(
                pk=int(copy_id),
                society=society,
            )
        except (VoucherTemplate.DoesNotExist, TypeError, ValueError):
            return None

    def _copy_template_initial(self, source_template):
        source_name = source_template.name or source_template.get_voucher_type_display()
        return {
            "voucher_type": source_template.voucher_type,
            "name": f"Copy of {source_name}",
            "narration": source_template.narration,
            "payment_mode": source_template.payment_mode,
            "reference_number_pattern": source_template.reference_number_pattern,
            "is_active": source_template.is_active,
            "is_pinned": False,
            "sort_order": source_template.sort_order,
        }

    def _copy_template_rows_initial(self, source_template):
        rows = []
        for row in source_template.rows.all().order_by("order", "id"):
            rows.append(
                {
                    "account": row.account_id,
                    "unit": row.unit_id if row.unit else None,
                    "side": row.side,
                    "default_amount": row.default_amount,
                    "order": row.order,
                }
            )
        return rows

    def _build_editor_forms(self, society, template=None, data=None, initial=None, row_initial_data=None):
        template_instance = template or VoucherTemplate(society=society)
        template_form = VoucherTemplateForm(data=data, instance=template_instance, initial=initial)
        row_formset = VoucherTemplateRowFormSet(
            data=data,
            instance=template_instance,
            prefix="rows",
            form_kwargs={"society": society},
            initial=row_initial_data,
        )
        return template_form, row_formset, template_instance

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society = self.get_object()

        voucher_templates = self._get_templates_queryset(society)
        templates_by_type = {}
        for template in voucher_templates:
            templates_by_type.setdefault(template.voucher_type, []).append(template)

        template_form = kwargs.get("template_form")
        row_formset = kwargs.get("row_formset")
        editor_template = kwargs.get("editor_template")
        copy_source_template = kwargs.get("copy_source_template")
        if template_form is None or row_formset is None:
            editor_template = editor_template or self._resolve_editor_template(society)
            if editor_template:
                template_form, row_formset, editor_template = self._build_editor_forms(
                    society,
                    template=editor_template,
                )
            else:
                copy_source_template = copy_source_template or self._resolve_copy_source(society)
                if copy_source_template:
                    template_form, row_formset, _ = self._build_editor_forms(
                        society,
                        template=VoucherTemplate(society=society),
                        initial=self._copy_template_initial(copy_source_template),
                        row_initial_data=self._copy_template_rows_initial(copy_source_template),
                    )
                else:
                    template_form, row_formset, _ = self._build_editor_forms(
                        society,
                        template=VoucherTemplate(society=society),
                        initial={"is_active": True, "is_pinned": False, "sort_order": 0},
                    )

        context["voucher_templates"] = voucher_templates
        context["templates_by_type"] = templates_by_type
        context["voucher_type_choices"] = VoucherTemplate._meta.get_field("voucher_type").choices
        context["template_form"] = template_form
        context["row_formset"] = row_formset
        context["editor_template"] = editor_template
        context["copy_source_template"] = copy_source_template
        return context

    def post(self, request, *args, **kwargs):
        society = self.get_object()
        action = request.POST.get("action")
        template_id = request.POST.get("template_id")

        if action == "save":
            editor_template = None
            if template_id:
                editor_template = get_object_or_404(
                    VoucherTemplate,
                    pk=int(template_id),
                    society=society,
                )

            template_form, row_formset, template_instance = self._build_editor_forms(
                society,
                template=editor_template,
                data=request.POST,
            )

            if template_form.is_valid() and row_formset.is_valid():
                with transaction.atomic():
                    saved_template = template_form.save(commit=False)
                    saved_template.society = society
                    saved_template.save()
                    row_formset.instance = saved_template
                    row_formset.save()
                messages.success(
                    request,
                    _('Template "%(name)s" saved.') % {
                        "name": saved_template.name or saved_template.get_voucher_type_display(),
                    },
                )
                return redirect("housing:society-voucher-templates", pk=society.pk)

            messages.warning(
                request,
                _("Please fix the highlighted template and row fields."),
            )
            return self.render_to_response(
                self.get_context_data(
                    template_form=template_form,
                    row_formset=row_formset,
                    editor_template=editor_template,
                    copy_source_template=self._resolve_copy_source(society),
                )
            )

        if action == "toggle_active" and template_id:
            try:
                template = VoucherTemplate.objects.get(
                    id=template_id,
                    society=society,
                )
                template.is_active = not template.is_active
                template.save()
                messages.success(
                    request,
                    _('Template "%(name)s" %(status)s.') % {
                        "name": template.name or template.get_voucher_type_display(),
                        "status": _("activated") if template.is_active else _("deactivated"),
                    },
                )
            except VoucherTemplate.DoesNotExist:
                messages.error(request, _("Template not found."))

        elif action == "delete" and template_id:
            try:
                template = VoucherTemplate.objects.get(
                    id=template_id,
                    society=society,
                )
                template_name = template.name or template.get_voucher_type_display()
                template.delete()
                messages.success(
                    request,
                    _('Template "%(name)s" deleted.') % {"name": template_name},
                )
            except VoucherTemplate.DoesNotExist:
                messages.error(request, _("Template not found."))

        return redirect("housing:society-voucher-templates", pk=society.pk)


society_voucher_templates_view = SocietyVoucherTemplatesView.as_view()

reminder_schedule_view = ReminderScheduleView.as_view()

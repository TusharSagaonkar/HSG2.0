from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django.views.generic import FormView
from django.views import View
from django.shortcuts import redirect
from django.utils import timezone
from django.http import JsonResponse

from housing.forms import SocietyForm
from housing.forms import SocietyEmailSettingsForm
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
from societies.models import Society
from societies.models import Membership
from notifications.models import EmailVerificationToken
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from billing.models import ChargeTemplate
from billing.services import generate_bills_for_period
from billing.reports import build_member_outstanding
from receipts.services import post_receipt_for_bill
from accounting.models import Account
from notifications.services import schedule_payment_reminders
from notifications.models import GlobalEmailSettings
from notifications.models import SocietyEmailSettings
from housing_accounting.selection import get_selected_scope
from societies.services import create_society
from societies.utils import get_user_role


class HousingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "housing/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)
        societies_qs = Society.objects.all()
        units_qs = Unit.objects.all()
        occupancies_qs = UnitOccupancy.objects.all()
        recent_societies_qs = Society.objects.order_by("-created_at")
        recent_units_qs = Unit.objects.select_related(
            "structure",
            "structure__society",
        ).order_by("-created_at")

        if selected_society:
            societies_qs = societies_qs.filter(pk=selected_society.pk)
            units_qs = units_qs.filter(structure__society=selected_society)
            occupancies_qs = occupancies_qs.filter(
                unit__structure__society=selected_society
            )
            recent_societies_qs = recent_societies_qs.filter(pk=selected_society.pk)
            recent_units_qs = recent_units_qs.filter(
                structure__society=selected_society
            )

        context["total_societies"] = societies_qs.count()
        context["total_units"] = units_qs.count()
        context["active_units"] = units_qs.filter(is_active=True).count()
        context["active_occupancies"] = occupancies_qs.filter(
            end_date__isnull=True
        ).count()
        members_qs = Member.objects.all()
        if selected_society:
            members_qs = members_qs.filter(society=selected_society)
        context["active_members"] = members_qs.filter(
            status=Member.MemberStatus.ACTIVE
        ).count()
        context["recent_societies"] = recent_societies_qs[:5]
        context["recent_units"] = recent_units_qs[:8]
        return context


housing_dashboard_view = HousingDashboardView.as_view()


class StructureUnitDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "housing/structure_unit_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)

        structures_qs = Structure.objects.select_related("society", "parent")
        units_qs = Unit.objects.select_related("structure", "structure__society")
        active_occupancies_qs = UnitOccupancy.objects.filter(end_date__isnull=True)

        if selected_society:
            structures_qs = structures_qs.filter(society=selected_society)
            units_qs = units_qs.filter(structure__society=selected_society)
            active_occupancies_qs = active_occupancies_qs.filter(
                unit__structure__society=selected_society
            )

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
        return context


structure_unit_dashboard_view = StructureUnitDashboardView.as_view()


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
            unit.primary_owner_record = ownership
            unit.current_occupancy_record = occupancy
            unit.primary_owner_name = self._user_display_name(
                ownership.owner if ownership else None,
            )
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
    template_name = "housing/form.html"
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
        return initial

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
        return context

    def get_success_url(self):
        return reverse("housing:member-list")


member_create_view = MemberCreateView.as_view()


class MemberUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Member
    form_class = MemberForm
    template_name = "housing/form.html"
    success_message = _("Member updated successfully.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Update Member")
        context["form_subtitle"] = _("Update membership details and status.")
        context["cancel_url"] = reverse("housing:member-list")
        context["cancel_label"] = _("Back to Members")
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


member_form_options_api_view = MemberFormOptionsAPIView.as_view()


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Charge Template")
        context["form_subtitle"] = _("Configure recurring maintenance or utility charges.")
        context["cancel_url"] = reverse("billing:charge-template-list")
        context["cancel_label"] = _("Back to Templates")
        return context

    def get_success_url(self):
        return reverse("housing:dashboard")


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
        query_society_id = self.request.GET.get("society")
        if query_society_id:
            query_society = Society.objects.filter(pk=query_society_id).first()
            if query_society:
                initial["society"] = query_society
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and "society" not in initial:
            initial["society"] = selected_society
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


class SocietyAdminView(LoginRequiredMixin, DetailView):
    """View for managing society memberships and user roles."""
    model = Society
    template_name = "housing/society_admin.html"
    context_object_name = "society"

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
        return context


society_admin_view = SocietyAdminView.as_view()


class SocietyUserCreateView(LoginRequiredMixin, FormView):
    """View for creating a new user and granting them access to a society."""
    form_class = SocietyUserCreationForm
    template_name = "housing/form.html"

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
            
            # Check if user has permission to resend (Owner or Admin)
            if not request.user.is_superuser:
                try:
                    membership = Membership.objects.get(user=request.user, society=society)
                    if membership.role not in ['owner', 'admin']:
                        raise PermissionDenied(_("You don't have permission to resend verification emails."))
                except Membership.DoesNotExist:
                    raise PermissionDenied(_("You don't have access to this society."))
            
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
            
            # Check if user has permission to update (Owner or Admin)
            if not request.user.is_superuser:
                try:
                    updater_membership = Membership.objects.get(user=request.user, society=society)
                    if updater_membership.role not in ['owner', 'admin']:
                        raise PermissionDenied(_("You don't have permission to update memberships."))
                except Membership.DoesNotExist:
                    raise PermissionDenied(_("You don't have access to this society."))
            
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
        selected_society, _ = get_selected_scope(request)
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


reminder_schedule_view = ReminderScheduleView.as_view()

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
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

from housing.forms import SocietyForm
from housing.forms import SocietyEmailSettingsForm
from housing.forms import StructureForm
from housing.forms import UnitForm
from housing.forms import UnitOccupancyForm
from housing.forms import UnitOwnershipForm
from housing.forms import MemberForm
from housing.forms import ChargeTemplateForm
from housing.forms import BillingGenerationForm
from housing.forms import ReceiptPostingForm
from societies.models import Society
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from billing.models import ChargeTemplate
from billing.services import generate_bills_for_period
from billing.reports import build_member_outstanding
from receipts.services import post_receipt_for_bill
from notifications.services import schedule_payment_reminders
from notifications.models import GlobalEmailSettings
from notifications.models import SocietyEmailSettings
from housing_accounting.selection import get_selected_scope


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
        ).order_by("name")
        if selected_society:
            queryset = queryset.filter(pk=selected_society.pk)
        return queryset


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
            .order_by("unit_id", "-start_date", "-id")
        )
        current_ownership_by_unit = {}
        for ownership in active_ownerships:
            current_ownership_by_unit.setdefault(ownership.unit_id, ownership)

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
            ownership = current_ownership_by_unit.get(unit.id)
            occupancy = current_occupancy_by_unit.get(unit.id)
            unit.current_owner_record = ownership
            unit.current_occupancy_record = occupancy
            unit.current_owner_name = self._user_display_name(
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

        context["root_structures"] = children_map.get(None, [])
        context["total_units"] = len(units)
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
        if not society_id:
            selected_society, _ = get_selected_scope(self.request)
            if selected_society:
                society_id = selected_society.pk
        if society_id:
            initial["society"] = society_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Member")
        context["form_subtitle"] = _("Create owner, tenant, or nominee membership.")
        context["cancel_url"] = reverse("housing:member-list")
        context["cancel_label"] = _("Back to Members")
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

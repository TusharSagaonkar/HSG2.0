from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView
from django.views.generic import ListView
from django.views.generic import TemplateView

from housing_accounting.selection import get_selected_scope
from members.models import Member
from members.models import Unit
from parking.forms import ParkingSlotForm
from parking.forms import ParkingRotationPolicyForm
from parking.forms import ParkingVehicleLimitForm
from parking.forms import VehicleForm
from parking.models import ParkingSlot
from parking.models import ParkingPermit
from parking.models import ParkingRotationAllocation
from parking.models import ParkingRotationApplication
from parking.models import ParkingRotationCycle
from parking.models import ParkingRotationPolicy
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle
from parking.services import create_sold_parking_permit
from parking.services import allocate_rotation_cycle
from parking.services import complete_rotation_cycle
from parking.services import generate_next_rotation_cycle
from parking.services import submit_rotation_application
from parking.services import validate_rotation_application


class ParkingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "parking/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, _ = get_selected_scope(self.request)

        slots_qs = ParkingSlot.objects.all()
        vehicles_qs = Vehicle.objects.select_related("unit", "member")
        limits_qs = ParkingVehicleLimit.objects.all()
        if selected_society:
            slots_qs = slots_qs.filter(society=selected_society)
            vehicles_qs = vehicles_qs.filter(society=selected_society)
            limits_qs = limits_qs.filter(society=selected_society)

        context["total_slots"] = slots_qs.count()
        context["active_slots"] = slots_qs.filter(is_active=True).count()
        context["total_vehicles"] = vehicles_qs.count()
        context["active_vehicles"] = vehicles_qs.filter(is_active=True).count()
        context["total_limits"] = limits_qs.count()
        permits_qs = ParkingPermit.objects.all()
        if selected_society:
            permits_qs = permits_qs.filter(society=selected_society)
        context["total_permits"] = permits_qs.count()
        context["active_permits"] = permits_qs.filter(status=ParkingPermit.Status.ACTIVE).count()
        context["recent_slots"] = slots_qs.order_by("-created_at")[:6]
        context["recent_vehicles"] = vehicles_qs.order_by("-created_at")[:8]
        context["recent_permits"] = permits_qs.select_related("vehicle", "slot", "unit").order_by(
            "-issued_at",
            "-id",
        )[:8]
        return context


parking_dashboard_view = ParkingDashboardView.as_view()


class ParkingSlotListView(LoginRequiredMixin, ListView):
    model = ParkingSlot
    template_name = "parking/slot_list.html"
    context_object_name = "parking_slots"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingSlot.objects.select_related("society").order_by("slot_number")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


parking_slot_list_view = ParkingSlotListView.as_view()


class ParkingSlotCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = ParkingSlotForm
    model = ParkingSlot
    template_name = "housing/form.html"
    success_message = _("Parking slot created successfully.")

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        if selected_society:
            initial["society"] = selected_society
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Parking Slot")
        context["form_subtitle"] = _("Create a parking slot for the selected society.")
        context["cancel_url"] = reverse("parking:slot-list")
        context["cancel_label"] = _("Back to Parking Slots")
        return context

    def get_success_url(self):
        return reverse("parking:slot-list")


parking_slot_create_view = ParkingSlotCreateView.as_view()


class FlatParkingDashboardListView(LoginRequiredMixin, ListView):
    model = Unit
    template_name = "parking/flat_dashboard_list.html"
    context_object_name = "flats"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = Unit.objects.select_related("structure", "structure__society").annotate(
            total_vehicles=Count("vehicles", distinct=True),
            active_vehicles=Count("vehicles", filter=Q(vehicles__is_active=True), distinct=True),
            sold_slots=Count(
                "owned_parking_slots",
                filter=Q(owned_parking_slots__parking_model=ParkingSlot.ParkingModel.SOLD),
                distinct=True,
            ),
            active_sold_permits=Count(
                "parking_permits",
                filter=Q(
                    parking_permits__permit_type=ParkingPermit.PermitType.SOLD,
                    parking_permits__status=ParkingPermit.Status.ACTIVE,
                ),
                distinct=True,
            ),
        ).order_by("structure__name", "identifier")
        if selected_society:
            queryset = queryset.filter(structure__society=selected_society)
        return queryset


flat_parking_dashboard_list_view = FlatParkingDashboardListView.as_view()


class FlatParkingDashboardDetailView(LoginRequiredMixin, TemplateView):
    template_name = "parking/flat_dashboard_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        unit = get_object_or_404(
            Unit.objects.select_related("structure", "structure__society"),
            pk=self.kwargs["pk"],
        )
        vehicles = list(
            Vehicle.objects.select_related("member").filter(unit=unit).prefetch_related(
                Prefetch(
                    "parking_permits",
                    queryset=ParkingPermit.objects.select_related("slot").order_by("-issued_at", "-id"),
                )
            ).order_by("vehicle_number")
        )
        permits = list(
            ParkingPermit.objects.select_related("vehicle", "slot")
            .filter(unit=unit, permit_type=ParkingPermit.PermitType.SOLD)
            .order_by("-issued_at", "-id")
        )
        today = timezone.localdate()

        for vehicle in vehicles:
            history = list(vehicle.parking_permits.all())
            active = next((permit for permit in history if permit.status == ParkingPermit.Status.ACTIVE), None)
            vehicle.current_sold_permit = active or (history[0] if history else None)
            vehicle.sold_permit_history = history
            vehicle.effective_is_active = (
                vehicle.current_sold_permit is not None
                and vehicle.current_sold_permit.status == ParkingPermit.Status.ACTIVE
                and vehicle.current_sold_permit.slot.owned_unit_id == vehicle.unit_id
            ) or vehicle.is_active

        rotation_cycle = (
            ParkingRotationCycle.objects.select_related("policy")
            .filter(
                society_id=unit.structure.society_id,
                allocation_status=ParkingRotationCycle.AllocationStatus.DRAFT,
                cycle_start_date__lte=today,
                cycle_end_date__gte=today,
            )
            .order_by("-cycle_start_date", "-id")
            .first()
        )
        rotation_window_open = False
        rotation_window_end = None
        rotation_existing_application = None
        rotation_can_apply = False
        rotation_apply_reason = ""
        rotation_vehicle_options = []

        if rotation_cycle:
            rotation_window_end = rotation_cycle.cycle_start_date + timedelta(
                days=max(rotation_cycle.policy.application_window_days - 1, 0),
            )
            rotation_window_open = today <= rotation_window_end
            rotation_existing_application = (
                ParkingRotationApplication.objects.filter(
                    cycle=rotation_cycle,
                    unit=unit,
                )
                .order_by("-applied_at", "-id")
                .first()
            )

            if rotation_window_open and rotation_existing_application is None:
                if rotation_cycle.policy.vehicle_required_before_apply:
                    for vehicle in vehicles:
                        is_allowed, _reason = validate_rotation_application(
                            cycle=rotation_cycle,
                            unit=unit,
                            vehicle=vehicle,
                        )
                        if is_allowed:
                            rotation_vehicle_options.append(vehicle)
                    rotation_can_apply = len(rotation_vehicle_options) > 0
                    if not rotation_can_apply:
                        rotation_apply_reason = _("No eligible vehicle found for this unit.")
                else:
                    is_allowed, reason = validate_rotation_application(
                        cycle=rotation_cycle,
                        unit=unit,
                    )
                    rotation_can_apply = is_allowed
                    rotation_apply_reason = reason if not is_allowed else ""
                    rotation_vehicle_options = vehicles
            elif not rotation_window_open:
                rotation_apply_reason = _("Application window is closed for this cycle.")
            elif rotation_existing_application is not None:
                rotation_apply_reason = _("An application already exists for this cycle.")

        context.update(
            {
                "unit": unit,
                "society_name": unit.structure.society.name,
                "building_name": unit.structure.name,
                "flat_number": unit.identifier,
                "vehicles": vehicles,
                "permits": permits,
                "total_vehicles": len(vehicles),
                "active_vehicles": sum(1 for vehicle in vehicles if vehicle.effective_is_active),
                "active_permits": sum(1 for permit in permits if permit.status == ParkingPermit.Status.ACTIVE),
                "rotation_cycle": rotation_cycle,
                "rotation_window_open": rotation_window_open,
                "rotation_window_end": rotation_window_end,
                "rotation_existing_application": rotation_existing_application,
                "rotation_can_apply": rotation_can_apply,
                "rotation_apply_reason": rotation_apply_reason,
                "rotation_vehicle_options": rotation_vehicle_options,
            }
        )
        return context


flat_parking_dashboard_detail_view = FlatParkingDashboardDetailView.as_view()


class FlatRotationApplicationCreateView(LoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        del args, kwargs
        unit = get_object_or_404(
            Unit.objects.select_related("structure", "structure__society"),
            pk=pk,
        )
        today = timezone.localdate()
        cycle = (
            ParkingRotationCycle.objects.select_related("policy")
            .filter(
                society_id=unit.structure.society_id,
                allocation_status=ParkingRotationCycle.AllocationStatus.DRAFT,
                cycle_start_date__lte=today,
                cycle_end_date__gte=today,
            )
            .order_by("-cycle_start_date", "-id")
            .first()
        )
        if not cycle:
            messages.warning(request, _("No open rotational cycle is available for this flat."))
            return redirect("parking:flat-dashboard-detail", pk=unit.pk)

        window_end = cycle.cycle_start_date + timedelta(days=max(cycle.policy.application_window_days - 1, 0))
        if today > window_end:
            messages.warning(request, _("Application window is closed for the current rotational cycle."))
            return redirect("parking:flat-dashboard-detail", pk=unit.pk)

        existing = ParkingRotationApplication.objects.filter(cycle=cycle, unit=unit).exists()
        if existing:
            messages.info(request, _("Application already submitted for this cycle."))
            return redirect("parking:flat-dashboard-detail", pk=unit.pk)

        vehicle = None
        vehicle_id = request.POST.get("vehicle_id")
        if vehicle_id:
            vehicle = get_object_or_404(
                Vehicle,
                pk=vehicle_id,
                society_id=unit.structure.society_id,
                unit_id=unit.id,
            )

        is_allowed, reason = validate_rotation_application(cycle=cycle, unit=unit, vehicle=vehicle)
        if not is_allowed:
            messages.warning(request, reason or _("This flat is not eligible for rotational parking."))
            return redirect("parking:flat-dashboard-detail", pk=unit.pk)

        application = submit_rotation_application(cycle=cycle, unit=unit, vehicle=vehicle)
        if application.application_status == ParkingRotationApplication.ApplicationStatus.APPROVED:
            messages.success(request, _("Rotational parking application submitted successfully."))
        else:
            messages.warning(request, application.rejection_reason or _("Application was rejected."))
        return redirect("parking:flat-dashboard-detail", pk=unit.pk)


flat_rotation_application_create_view = FlatRotationApplicationCreateView.as_view()


class FlatParkingSummaryModalView(LoginRequiredMixin, TemplateView):
    template_name = "parking/partials/flat_summary_modal_content.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        unit = get_object_or_404(
            Unit.objects.select_related("structure", "structure__society"),
            pk=self.kwargs["pk"],
        )
        vehicles = list(
            Vehicle.objects.select_related("member").filter(unit=unit).prefetch_related(
                Prefetch(
                    "parking_permits",
                    queryset=ParkingPermit.objects.select_related("slot").order_by("-issued_at", "-id"),
                )
            ).order_by("vehicle_number")
        )
        for vehicle in vehicles:
            history = list(vehicle.parking_permits.all())
            active = next((permit for permit in history if permit.status == ParkingPermit.Status.ACTIVE), None)
            vehicle.current_sold_permit = active or (history[0] if history else None)
            vehicle.effective_is_active = (
                vehicle.current_sold_permit is not None
                and vehicle.current_sold_permit.status == ParkingPermit.Status.ACTIVE
                and vehicle.current_sold_permit.slot.owned_unit_id == vehicle.unit_id
            ) or vehicle.is_active

        context.update(
            {
                "unit": unit,
                "vehicles": vehicles,
                "active_vehicles": sum(1 for vehicle in vehicles if vehicle.effective_is_active),
                "active_sold_permits": sum(
                    1
                    for vehicle in vehicles
                    if vehicle.current_sold_permit
                    and vehicle.current_sold_permit.status == ParkingPermit.Status.ACTIVE
                ),
                "flat_has_owned_sold_slot": ParkingSlot.objects.filter(
                    society_id=unit.structure.society_id,
                    parking_model=ParkingSlot.ParkingModel.SOLD,
                    owned_unit_id=unit.id,
                    is_active=True,
                ).exists(),
            }
        )
        return context


flat_parking_summary_modal_view = FlatParkingSummaryModalView.as_view()


class VehicleListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = "parking/vehicle_list.html"
    context_object_name = "vehicles"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        sold_slot_exists = ParkingSlot.objects.filter(
            society_id=OuterRef("society_id"),
            parking_model=ParkingSlot.ParkingModel.SOLD,
            owned_unit_id=OuterRef("unit_id"),
            is_active=True,
        )
        queryset = Vehicle.objects.select_related(
            "society",
            "unit",
            "unit__structure",
            "member",
        ).annotate(
            has_owned_sold_slot=Exists(sold_slot_exists),
        ).order_by("vehicle_number")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset.prefetch_related(
            Prefetch(
                "parking_permits",
                queryset=ParkingPermit.objects.select_related("slot").order_by("-issued_at", "-id"),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vehicles = context.get("vehicles") or []
        for vehicle in vehicles:
            permits = list(vehicle.parking_permits.all())
            active = next((permit for permit in permits if permit.status == ParkingPermit.Status.ACTIVE), None)
            vehicle.current_sold_permit = active or (permits[0] if permits else None)
            has_active_sold_permit = (
                vehicle.current_sold_permit is not None
                and vehicle.current_sold_permit.status == ParkingPermit.Status.ACTIVE
                and vehicle.current_sold_permit.slot.owned_unit_id == vehicle.unit_id
            )
            vehicle.effective_is_valid = vehicle.is_valid() or has_active_sold_permit
            vehicle.effective_rule_status = (
                Vehicle.RuleStatus.ACTIVE if has_active_sold_permit else vehicle.rule_status
            )
        return context


vehicle_list_view = VehicleListView.as_view()


class ParkingPermitListView(LoginRequiredMixin, ListView):
    model = ParkingPermit
    template_name = "parking/permit_list.html"
    context_object_name = "permits"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingPermit.objects.select_related(
            "society",
            "vehicle",
            "unit",
            "slot",
        ).order_by("-issued_at", "-id")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


parking_permit_list_view = ParkingPermitListView.as_view()


class VehicleCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = VehicleForm
    model = Vehicle
    template_name = "housing/form.html"
    success_message = _("Vehicle added successfully.")

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        society_id = self.request.GET.get("society")
        if society_id:
            initial["society"] = society_id
        elif selected_society:
            initial["society"] = selected_society
        unit_id = self.request.GET.get("unit")
        if unit_id:
            initial["unit"] = unit_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Vehicle")
        context["form_subtitle"] = _("Register a unit/member vehicle.")
        context["cancel_url"] = reverse("parking:vehicle-list")
        context["cancel_label"] = _("Back to Vehicles")
        context["auto_reload_society"] = True
        context["auto_reload_unit"] = True
        context["member_lookup_url"] = reverse("parking:vehicle-members")
        return context

    def get_success_url(self):
        return reverse("parking:vehicle-list")


vehicle_create_view = VehicleCreateView.as_view()


class CreateSoldPermitForVehicleView(LoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        del args, kwargs
        vehicle = get_object_or_404(Vehicle, pk=pk)
        try:
            permit = create_sold_parking_permit(
                vehicle_id=vehicle.id,
                created_by=request.user if request.user.is_authenticated else None,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("parking:vehicle-list")

        if permit.status == ParkingPermit.Status.ACTIVE:
            messages.success(
                request,
                f"Sold parking permit activated for {vehicle.vehicle_number} on slot {permit.slot.slot_number}.",
            )
        else:
            messages.warning(
                request,
                f"Permit created in standby for {vehicle.vehicle_number} (all owned sold slots currently active).",
            )
        return redirect("parking:permit-list")


create_sold_permit_for_vehicle_view = CreateSoldPermitForVehicleView.as_view()


class VehicleMembersByUnitView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        society_id = request.GET.get("society")
        unit_id = request.GET.get("unit")
        members = []
        if society_id and unit_id:
            members = list(
                Member.objects.filter(
                    society_id=society_id,
                    unit_id=unit_id,
                )
                .order_by("full_name")
                .values("id", "full_name")
            )
        return JsonResponse({"members": members})


vehicle_members_by_unit_view = VehicleMembersByUnitView.as_view()


class VehicleQRCodeView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        vehicle = get_object_or_404(Vehicle, pk=pk)
        qr_image = vehicle.generate_qr_image()
        response = HttpResponse(qr_image.read(), content_type="image/png")
        response["Content-Disposition"] = (
            f'inline; filename="vehicle-verification-{vehicle.verification_token}.png"'
        )
        return response


vehicle_qr_code_view = VehicleQRCodeView.as_view()


class ParkingPermitQRCodeView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        permit = get_object_or_404(ParkingPermit, pk=pk)
        qr_image = permit.generate_qr_image()
        response = HttpResponse(qr_image.read(), content_type="image/png")
        response["Content-Disposition"] = (
            f'inline; filename="parking-permit-verification-{permit.qr_token}.png"'
        )
        return response


parking_permit_qr_code_view = ParkingPermitQRCodeView.as_view()


class VehicleStickerView(LoginRequiredMixin, TemplateView):
    template_name = "parking/vehicle_sticker.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vehicle = get_object_or_404(
            Vehicle.objects.select_related(
                "society",
                "unit",
                "unit__structure",
                "member",
            ),
            pk=self.kwargs["pk"],
        )
        member_role = vehicle.member.role if vehicle.member else None

        if member_role == Member.MemberRole.OWNER:
            status_class = "status-owner"
            member_type = "Owner"
            sticker_status = "Owner Living"
        elif member_role == Member.MemberRole.TENANT:
            status_class = "status-tenant"
            member_type = "Tenant"
            sticker_status = "Tenant"
        else:
            status_class = "status-notliving"
            member_type = "Unknown"
            sticker_status = "Not Living"

        if vehicle.vehicle_type == Vehicle.VehicleType.CAR:
            layout_class = "layout-car"
            vehicle_symbol = "🚗"
        elif vehicle.vehicle_type == Vehicle.VehicleType.BIKE:
            layout_class = "layout-bike"
            vehicle_symbol = "🏍"
        else:
            layout_class = "layout-other"
            vehicle_symbol = "🚘"

        context.update(
            {
                "status_class": status_class,
                "layout_class": layout_class,
                "member_type": member_type,
                "sticker_status": sticker_status,
                "society_name": vehicle.society.name,
                "owner_name": vehicle.member.full_name if vehicle.member else "Unassigned",
                "vehicle_number": vehicle.vehicle_number,
                "vehicle_type_code": vehicle.vehicle_type,
                "vehicle_type_label": vehicle.get_vehicle_type_display(),
                "vehicle_symbol": vehicle_symbol,
                "valid_until": vehicle.valid_until,
                "building_name": vehicle.unit.structure.name,
                "flat_number": vehicle.unit.identifier,
                "qr_image_url": reverse("parking:vehicle-qr", kwargs={"pk": vehicle.pk}),
            }
        )
        return context


vehicle_sticker_view = VehicleStickerView.as_view()


class ParkingPermitStickerView(LoginRequiredMixin, TemplateView):
    template_name = "parking/vehicle_sticker.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        permit = get_object_or_404(
            ParkingPermit.objects.select_related(
                "society",
                "unit",
                "unit__structure",
                "slot",
                "vehicle",
                "vehicle__member",
            ),
            pk=self.kwargs["pk"],
        )
        member_role = permit.vehicle.member.role if permit.vehicle.member else None
        if member_role == Member.MemberRole.OWNER:
            status_class = "status-owner"
            member_type = "Owner"
        elif member_role == Member.MemberRole.TENANT:
            status_class = "status-tenant"
            member_type = "Tenant"
        else:
            status_class = "status-notliving"
            member_type = "Unknown"

        if permit.vehicle.vehicle_type == Vehicle.VehicleType.CAR:
            layout_class = "layout-car"
            vehicle_symbol = "🚗"
        elif permit.vehicle.vehicle_type == Vehicle.VehicleType.BIKE:
            layout_class = "layout-bike"
            vehicle_symbol = "🏍"
        else:
            layout_class = "layout-other"
            vehicle_symbol = "🚘"

        context.update(
            {
                "status_class": status_class,
                "layout_class": layout_class,
                "member_type": member_type,
                "sticker_status": f"Permit {permit.get_status_display()}",
                "society_name": permit.society.name,
                "owner_name": permit.vehicle.member.full_name if permit.vehicle.member else "Unassigned",
                "unit_number": permit.unit.identifier,
                "slot_number": permit.slot.slot_number,
                "vehicle_number": permit.vehicle.vehicle_number,
                "vehicle_type_code": permit.vehicle.vehicle_type,
                "vehicle_type_label": permit.vehicle.get_vehicle_type_display(),
                "vehicle_symbol": vehicle_symbol,
                "valid_until": permit.expires_at.date() if permit.expires_at else permit.vehicle.valid_until,
                "building_name": permit.unit.structure.name,
                "flat_number": f"{permit.unit.identifier} | Slot {permit.slot.slot_number}",
                "permit_status": permit.status,
                "qr_image_url": reverse("parking:permit-qr", kwargs={"pk": permit.pk}),
            }
        )
        return context


parking_permit_sticker_view = ParkingPermitStickerView.as_view()


class ParkingVehicleLimitListView(LoginRequiredMixin, ListView):
    model = ParkingVehicleLimit
    template_name = "parking/limit_list.html"
    context_object_name = "vehicle_limits"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingVehicleLimit.objects.select_related("society").order_by(
            "-status",
            "member_role",
            "vehicle_type",
            "-start_date",
            "-id",
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


parking_vehicle_limit_list_view = ParkingVehicleLimitListView.as_view()


class ParkingVehicleLimitCreateView(
    LoginRequiredMixin,
    SuccessMessageMixin,
    CreateView,
):
    form_class = ParkingVehicleLimitForm
    model = ParkingVehicleLimit
    template_name = "housing/form.html"
    success_message = _("Open parking vehicle limit version saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        if selected_society:
            initial["society"] = selected_society
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Open Parking Vehicle Limit")
        context["form_subtitle"] = _("Set allowed open parking vehicle count by role and type.")
        context["cancel_url"] = reverse("parking:limit-list")
        context["cancel_label"] = _("Back to Open Parking Vehicle Limits")
        return context

    def get_success_url(self):
        return reverse("parking:limit-list")

    def form_valid(self, form):
        self.object = ParkingVehicleLimit.create_new_version(
            society=form.cleaned_data["society"],
            member_role=form.cleaned_data["member_role"],
            vehicle_type=form.cleaned_data["vehicle_type"],
            max_allowed=form.cleaned_data["max_allowed"],
            changed_reason=form.cleaned_data.get("changed_reason", ""),
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        return HttpResponseRedirect(self.get_success_url())


parking_vehicle_limit_create_view = ParkingVehicleLimitCreateView.as_view()


class ParkingRotationPolicyListView(LoginRequiredMixin, ListView):
    model = ParkingRotationPolicy
    template_name = "parking/rotation_policy_list.html"
    context_object_name = "policies"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingRotationPolicy.objects.select_related("society").order_by(
            "-is_active",
            "-effective_from",
            "-id",
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


parking_rotation_policy_list_view = ParkingRotationPolicyListView.as_view()


class ParkingRotationPolicyCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    form_class = ParkingRotationPolicyForm
    model = ParkingRotationPolicy
    template_name = "housing/form.html"
    success_message = _("Rotation policy version saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        if selected_society:
            initial["society"] = selected_society
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Rotation Policy Version")
        context["form_subtitle"] = _("Create a new version; existing policy is automatically closed.")
        context["cancel_url"] = reverse("parking:rotation-policy-list")
        context["cancel_label"] = _("Back to Rotation Policies")
        return context

    def get_success_url(self):
        return reverse("parking:rotation-policy-list")

    def form_valid(self, form):
        self.object = ParkingRotationPolicy.create_new_version(
            society=form.cleaned_data["society"],
            policy_name=form.cleaned_data["policy_name"],
            rotation_period_months=form.cleaned_data["rotation_period_months"],
            rotation_method=form.cleaned_data["rotation_method"],
            vehicle_required_before_apply=form.cleaned_data["vehicle_required_before_apply"],
            allow_sold_parking_owner=form.cleaned_data["allow_sold_parking_owner"],
            allow_tenant_application=form.cleaned_data["allow_tenant_application"],
            max_rotational_slots_per_unit=form.cleaned_data["max_rotational_slots_per_unit"],
            max_total_parking_per_unit=form.cleaned_data["max_total_parking_per_unit"],
            skip_units_with_outstanding_dues=form.cleaned_data["skip_units_with_outstanding_dues"],
            skip_units_with_parking_violation=form.cleaned_data["skip_units_with_parking_violation"],
            unused_parking_reassignment_days=form.cleaned_data["unused_parking_reassignment_days"],
            application_window_days=form.cleaned_data["application_window_days"],
            priority_rule=form.cleaned_data["priority_rule"],
            effective_from=form.cleaned_data["effective_from"],
            changed_by=self.request.user if self.request.user.is_authenticated else None,
            change_reason="Created from frontend form",
        )
        return HttpResponseRedirect(self.get_success_url())


parking_rotation_policy_create_view = ParkingRotationPolicyCreateView.as_view()


class ParkingRotationCycleListView(LoginRequiredMixin, ListView):
    model = ParkingRotationCycle
    template_name = "parking/rotation_cycle_list.html"
    context_object_name = "cycles"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingRotationCycle.objects.select_related("society", "policy").order_by(
            "-cycle_number",
            "-id",
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


parking_rotation_cycle_list_view = ParkingRotationCycleListView.as_view()


class ParkingRotationCycleGenerateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        del args, kwargs
        selected_society, selected_financial_year = get_selected_scope(request)
        del selected_financial_year
        if not selected_society:
            messages.error(request, _("Please select a society first."))
            return redirect("parking:rotation-cycle-list")
        cycle = generate_next_rotation_cycle(society_id=selected_society.id)
        if cycle is None:
            messages.error(request, _("No active rotational parking policy found for selected society."))
        else:
            messages.success(request, _("Generated cycle #%s.") % cycle.cycle_number)
        return redirect("parking:rotation-cycle-list")


parking_rotation_cycle_generate_view = ParkingRotationCycleGenerateView.as_view()


class ParkingRotationCycleDetailView(LoginRequiredMixin, TemplateView):
    template_name = "parking/rotation_cycle_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cycle = get_object_or_404(
            ParkingRotationCycle.objects.select_related("society", "policy"),
            pk=self.kwargs["pk"],
        )
        applications = list(
            ParkingRotationApplication.objects.select_related("unit", "vehicle")
            .filter(cycle=cycle)
            .order_by("applied_at", "id")
        )
        allocations = list(
            ParkingRotationAllocation.objects.select_related("unit", "vehicle", "parking_spot")
            .filter(cycle=cycle)
            .order_by("-assigned_at", "-id")
        )
        selected_society, _ = get_selected_scope(self.request)
        units = Unit.objects.none()
        vehicles = Vehicle.objects.none()
        if selected_society and selected_society.id == cycle.society_id:
            units = Unit.objects.filter(structure__society=selected_society).order_by("identifier")
            vehicles = Vehicle.objects.filter(society=selected_society).order_by("vehicle_number")

        context.update(
            {
                "cycle": cycle,
                "applications": applications,
                "allocations": allocations,
                "units": units,
                "vehicles": vehicles,
                "approved_count": sum(
                    1
                    for app in applications
                    if app.application_status == ParkingRotationApplication.ApplicationStatus.APPROVED
                ),
            }
        )
        return context


parking_rotation_cycle_detail_view = ParkingRotationCycleDetailView.as_view()


class ParkingRotationApplicationCreateView(LoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        del args, kwargs
        cycle = get_object_or_404(ParkingRotationCycle.objects.select_related("society"), pk=pk)
        unit_id = request.POST.get("unit")
        vehicle_id = request.POST.get("vehicle")
        unit = get_object_or_404(Unit, pk=unit_id, structure__society_id=cycle.society_id)
        vehicle = None
        if vehicle_id:
            vehicle = get_object_or_404(Vehicle, pk=vehicle_id, society_id=cycle.society_id)
        app = submit_rotation_application(cycle=cycle, unit=unit, vehicle=vehicle)
        if app.application_status == ParkingRotationApplication.ApplicationStatus.APPROVED:
            messages.success(request, _("Application approved for unit %s.") % unit.identifier)
        else:
            messages.warning(
                request,
                _("Application rejected for unit %s: %s") % (unit.identifier, app.rejection_reason),
            )
        return redirect("parking:rotation-cycle-detail", pk=cycle.pk)


parking_rotation_application_create_view = ParkingRotationApplicationCreateView.as_view()


class ParkingRotationCycleAllocateView(LoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        del args, kwargs
        cycle = get_object_or_404(ParkingRotationCycle, pk=pk)
        allocations = allocate_rotation_cycle(
            cycle=cycle,
            assigned_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, _("Allocated %s rotational spots.") % len(allocations))
        return redirect("parking:rotation-cycle-detail", pk=cycle.pk)


parking_rotation_cycle_allocate_view = ParkingRotationCycleAllocateView.as_view()


class ParkingRotationCycleCompleteView(LoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        del args, kwargs
        cycle = get_object_or_404(ParkingRotationCycle, pk=pk)
        next_cycle = complete_rotation_cycle(cycle=cycle, generate_next=True)
        if next_cycle:
            messages.success(
                request,
                _("Cycle completed and next cycle #%s generated.") % next_cycle.cycle_number,
            )
        else:
            messages.success(request, _("Cycle completed."))
        return redirect("parking:rotation-cycle-list")


parking_rotation_cycle_complete_view = ParkingRotationCycleCompleteView.as_view()

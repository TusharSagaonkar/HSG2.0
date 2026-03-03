from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView
from django.views.generic import ListView
from django.views.generic import TemplateView

from housing_accounting.selection import get_selected_scope
from members.models import Member
from parking.forms import ParkingSlotForm
from parking.forms import ParkingVehicleLimitForm
from parking.forms import VehicleForm
from parking.models import ParkingSlot
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle


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
        context["recent_slots"] = slots_qs.order_by("-created_at")[:6]
        context["recent_vehicles"] = vehicles_qs.order_by("-created_at")[:8]
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


class VehicleListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = "parking/vehicle_list.html"
    context_object_name = "vehicles"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = Vehicle.objects.select_related(
            "society",
            "unit",
            "unit__structure",
            "member",
        ).order_by("vehicle_number")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


vehicle_list_view = VehicleListView.as_view()


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


class ParkingVehicleLimitListView(LoginRequiredMixin, ListView):
    model = ParkingVehicleLimit
    template_name = "parking/limit_list.html"
    context_object_name = "vehicle_limits"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ParkingVehicleLimit.objects.select_related("society").order_by(
            "member_role",
            "vehicle_type",
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
    success_message = _("Parking vehicle limit saved successfully.")

    def get_initial(self):
        initial = super().get_initial()
        selected_society, _ = get_selected_scope(self.request)
        if selected_society:
            initial["society"] = selected_society
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add Parking Vehicle Limit")
        context["form_subtitle"] = _("Set allowed vehicle count by role and type.")
        context["cancel_url"] = reverse("parking:limit-list")
        context["cancel_label"] = _("Back to Vehicle Limits")
        return context

    def get_success_url(self):
        return reverse("parking:limit-list")


parking_vehicle_limit_create_view = ParkingVehicleLimitCreateView.as_view()

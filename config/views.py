from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import DecimalField
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.generic import TemplateView

from accounting.models import AccountingPeriod
from accounting.models import Voucher
from billing.models import Bill
from billing.models import ChargeTemplate
from housing_accounting.selection import get_selected_scope
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from notifications.models import ReminderLog
from parking.models import ParkingPermit
from parking.models import ParkingRotationApplication
from parking.models import Vehicle
from receipts.models import PaymentReceipt
from societies.models import Society


class HomeDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "pages/home.html"

    @staticmethod
    def _sum_amount(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            )
        )["total"]

    @staticmethod
    def _filter_date_range(queryset, field_name, *, start_date, end_date):
        return queryset.filter(
            **{
                f"{field_name}__gte": start_date,
                f"{field_name}__lte": end_date,
            }
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, selected_financial_year = get_selected_scope(self.request)
        today = timezone.localdate()

        societies_qs = Society.objects.all()
        structures_qs = Structure.objects.select_related("society")
        units_qs = Unit.objects.select_related("structure", "structure__society")
        occupancies_qs = UnitOccupancy.objects.select_related("unit", "unit__structure")
        members_qs = Member.objects.select_related("society", "unit", "unit__structure")
        templates_qs = ChargeTemplate.objects.select_related("society")
        bills_qs = Bill.objects.select_related("member", "unit", "society")
        receipts_qs = PaymentReceipt.objects.select_related("member", "unit", "society")
        reminders_qs = ReminderLog.objects.select_related("member", "bill", "society")
        vouchers_qs = Voucher.objects.select_related("society")
        periods_qs = AccountingPeriod.objects.select_related("society", "financial_year")
        vehicles_qs = Vehicle.objects.select_related("society", "unit")
        permits_qs = ParkingPermit.objects.select_related("society", "vehicle", "slot", "unit")
        rotation_apps_qs = ParkingRotationApplication.objects.select_related("cycle", "unit", "vehicle")

        if selected_society:
            societies_qs = societies_qs.filter(pk=selected_society.pk)
            structures_qs = structures_qs.filter(society=selected_society)
            units_qs = units_qs.filter(structure__society=selected_society)
            occupancies_qs = occupancies_qs.filter(unit__structure__society=selected_society)
            members_qs = members_qs.filter(society=selected_society)
            templates_qs = templates_qs.filter(society=selected_society)
            bills_qs = bills_qs.filter(society=selected_society)
            receipts_qs = receipts_qs.filter(society=selected_society)
            reminders_qs = reminders_qs.filter(society=selected_society)
            vouchers_qs = vouchers_qs.filter(society=selected_society)
            periods_qs = periods_qs.filter(society=selected_society)
            vehicles_qs = vehicles_qs.filter(society=selected_society)
            permits_qs = permits_qs.filter(society=selected_society)
            rotation_apps_qs = rotation_apps_qs.filter(cycle__society=selected_society)

        if selected_financial_year:
            bills_qs = self._filter_date_range(
                bills_qs,
                "bill_date",
                start_date=selected_financial_year.start_date,
                end_date=selected_financial_year.end_date,
            )
            receipts_qs = self._filter_date_range(
                receipts_qs,
                "receipt_date",
                start_date=selected_financial_year.start_date,
                end_date=selected_financial_year.end_date,
            )
            vouchers_qs = self._filter_date_range(
                vouchers_qs,
                "voucher_date",
                start_date=selected_financial_year.start_date,
                end_date=selected_financial_year.end_date,
            )
            reminders_qs = reminders_qs.filter(
                created_at__date__gte=selected_financial_year.start_date,
                created_at__date__lte=selected_financial_year.end_date,
            )
            periods_qs = periods_qs.filter(financial_year=selected_financial_year)

        total_units = units_qs.count()
        occupied_units = occupancies_qs.filter(
            end_date__isnull=True,
        ).exclude(
            occupancy_type=UnitOccupancy.OccupancyType.VACANT,
        ).count()
        vacant_units = max(total_units - occupied_units, 0)

        total_billed = self._sum_amount(bills_qs, "total_amount")
        total_collected = self._sum_amount(
            receipts_qs.filter(status=PaymentReceipt.ReceiptStatus.POSTED),
            "amount",
        )
        collection_rate = Decimal("0.00")
        if total_billed > 0:
            collection_rate = (total_collected * Decimal("100.00")) / total_billed

        context.update(
            {
                "today": today,
                "total_societies": societies_qs.count(),
                "total_structures": structures_qs.count(),
                "total_units": total_units,
                "active_units": units_qs.filter(is_active=True).count(),
                "occupied_units": occupied_units,
                "vacant_units": vacant_units,
                "total_members": members_qs.count(),
                "active_members": members_qs.filter(status=Member.MemberStatus.ACTIVE).count(),
                "owner_members": members_qs.filter(role=Member.MemberRole.OWNER).count(),
                "tenant_members": members_qs.filter(role=Member.MemberRole.TENANT).count(),
                "active_charge_templates": templates_qs.filter(is_active=True).count(),
                "open_bills": bills_qs.filter(status=Bill.BillStatus.OPEN).count(),
                "partial_bills": bills_qs.filter(status=Bill.BillStatus.PARTIAL).count(),
                "overdue_bills": bills_qs.filter(status=Bill.BillStatus.OVERDUE).count(),
                "due_today_bills": bills_qs.exclude(status=Bill.BillStatus.PAID).filter(due_date=today).count(),
                "total_billed": total_billed,
                "total_collected": total_collected,
                "collection_rate": collection_rate.quantize(Decimal("0.01")),
                "posted_receipts": receipts_qs.filter(status=PaymentReceipt.ReceiptStatus.POSTED).count(),
                "void_receipts": receipts_qs.filter(status=PaymentReceipt.ReceiptStatus.VOID).count(),
                "draft_vouchers": vouchers_qs.filter(posted_at__isnull=True).count(),
                "posted_vouchers": vouchers_qs.filter(posted_at__isnull=False).count(),
                "open_periods": periods_qs.filter(is_open=True).count(),
                "closed_periods": periods_qs.filter(is_open=False).count(),
                "active_vehicles": vehicles_qs.filter(is_active=True).count(),
                "vehicle_rule_violations": vehicles_qs.exclude(rule_status=Vehicle.RuleStatus.ACTIVE).count(),
                "active_permits": permits_qs.filter(status=ParkingPermit.Status.ACTIVE).count(),
                "pending_rotation_applications": rotation_apps_qs.filter(
                    application_status=ParkingRotationApplication.ApplicationStatus.PENDING
                ).count(),
                "queued_reminders": reminders_qs.filter(status=ReminderLog.ReminderStatus.QUEUED).count(),
                "failed_reminders": reminders_qs.filter(status=ReminderLog.ReminderStatus.FAILED).count(),
                "recent_bills": bills_qs.order_by("-bill_date", "-id")[:6],
                "recent_receipts": receipts_qs.order_by("-receipt_date", "-id")[:6],
                "recent_vouchers": vouchers_qs.order_by("-voucher_date", "-id")[:6],
            }
        )
        return context

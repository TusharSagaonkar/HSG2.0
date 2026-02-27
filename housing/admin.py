from django.contrib import admin
from .models import Society, Structure, Unit
from .models import Member, ChargeTemplate, Bill, BillLine, PaymentReceipt, ReceiptAllocation, ReminderLog
############ SOCIETY, STRUCTURE, UNIT ##############

@admin.register(Society)
class SocietyAdmin(admin.ModelAdmin):
    list_display = ("name", "registration_number", "created_at")
    search_fields = ("name",)


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "structure_type",
        "society",
        "parent",
        "display_order",
    )
    list_filter = ("society", "structure_type")
    search_fields = ("name",)
    ordering = ("society", "display_order")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = (
        "identifier",
        "unit_type",
        "structure",
        "is_active",
    )
    list_filter = ("unit_type", "is_active", "structure__society")
    search_fields = ("identifier",)



############ OWNERSHIP, OCCUPANCY ##############
from .models import UnitOwnership, UnitOccupancy

@admin.register(UnitOwnership)
class UnitOwnershipAdmin(admin.ModelAdmin):
    list_display = ("unit", "owner", "start_date", "end_date")
    list_filter = ("unit__structure__society",)
    search_fields = ("unit__identifier", "owner__username")


@admin.register(UnitOccupancy)
class UnitOccupancyAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "occupancy_type",
        "occupant",
        "start_date",
        "end_date",
    )
    list_filter = ("occupancy_type", "unit__structure__society")
    search_fields = ("unit__identifier",)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("full_name", "society", "unit", "role", "status")
    list_filter = ("society", "role", "status")
    search_fields = ("full_name", "email", "phone", "unit__identifier")


@admin.register(ChargeTemplate)
class ChargeTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "version_no",
        "society",
        "charge_type",
        "rate",
        "frequency",
        "effective_from",
        "effective_to",
        "is_active",
    )
    list_filter = ("society", "charge_type", "frequency", "is_active")
    search_fields = ("name",)
    readonly_fields = ("version_no",)


class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 0


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("bill_number", "society", "member", "bill_date", "due_date", "total_amount", "status")
    list_filter = ("society", "status")
    search_fields = ("member__full_name", "unit__identifier")
    inlines = [BillLineInline]


class ReceiptAllocationInline(admin.TabularInline):
    model = ReceiptAllocation
    extra = 0


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "society", "member", "receipt_date", "amount", "payment_mode", "status")
    list_filter = ("society", "payment_mode", "status")
    search_fields = ("member__full_name", "reference_number")
    inlines = [ReceiptAllocationInline]


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ("member", "bill", "channel", "status", "scheduled_for", "created_at")
    list_filter = ("society", "channel", "status")
    search_fields = ("member__full_name", "bill__bill_number")

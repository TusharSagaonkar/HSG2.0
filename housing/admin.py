from django.contrib import admin
from .models import Society, Structure, Unit
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

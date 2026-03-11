from django.db import migrations
from django.db import models


def forward_map_rule_statuses(apps, schema_editor):
    del schema_editor
    Vehicle = apps.get_model("parking", "Vehicle")
    mapping = {
        "VALID": "ACTIVE",
        "EXCEEDS_LIMIT": "RULE_VIOLATION",
        "EXPIRED": "PERMIT_EXPIRED",
        "INACTIVE": "VEHICLE_INACTIVE",
    }
    for old_value, new_value in mapping.items():
        Vehicle.objects.filter(rule_status=old_value).update(rule_status=new_value)


def reverse_map_rule_statuses(apps, schema_editor):
    del schema_editor
    Vehicle = apps.get_model("parking", "Vehicle")
    mapping = {
        "ACTIVE": "VALID",
        "RULE_VIOLATION": "EXCEEDS_LIMIT",
        "PERMIT_EXPIRED": "EXPIRED",
        "VEHICLE_INACTIVE": "INACTIVE",
    }
    for old_value, new_value in mapping.items():
        Vehicle.objects.filter(rule_status=old_value).update(rule_status=new_value)


class Migration(migrations.Migration):
    dependencies = [
        ("parking", "0005_versioned_parking_rules_and_vehicle_status"),
    ]

    operations = [
        migrations.RunPython(
            code=forward_map_rule_statuses,
            reverse_code=reverse_map_rule_statuses,
        ),
        migrations.AlterField(
            model_name="vehicle",
            name="rule_status",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("RULE_VIOLATION", "Rule Violation"),
                    ("RESIDENT_MISMATCH", "Resident Mismatch"),
                    ("UNIT_VACANT", "Unit Vacant"),
                    ("PERMIT_EXPIRED", "Permit Expired"),
                    ("VEHICLE_INACTIVE", "Vehicle Inactive"),
                    ("ADMIN_BLOCKED", "Admin Blocked"),
                    ("DATA_INCONSISTENT", "Data Inconsistent"),
                ],
                default="ACTIVE",
                max_length=20,
            ),
        ),
    ]

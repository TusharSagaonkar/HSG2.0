from django.conf import settings
from django.db import migrations
from django.db import models
from django.db.models import Q
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("parking", "0004_vehicle_verification_fields"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="parkingvehiclelimit",
            unique_together=set(),
        ),
        migrations.AddField(
            model_name="parkingvehiclelimit",
            name="changed_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="parkingvehiclelimit",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_parking_vehicle_limits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="parkingvehiclelimit",
            name="end_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="parkingvehiclelimit",
            name="start_date",
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AddField(
            model_name="parkingvehiclelimit",
            name="status",
            field=models.CharField(
                choices=[("ACTIVE", "Active"), ("INACTIVE", "Inactive")],
                default="ACTIVE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="vehicle",
            name="rule_status",
            field=models.CharField(
                choices=[
                    ("VALID", "Valid"),
                    ("EXCEEDS_LIMIT", "Exceeds Limit"),
                    ("EXPIRED", "Expired"),
                    ("INACTIVE", "Inactive"),
                ],
                default="VALID",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="parkingvehiclelimit",
            constraint=models.UniqueConstraint(
                condition=Q(("status", "ACTIVE")),
                fields=("society", "member_role", "vehicle_type"),
                name="uniq_active_parking_limit_per_scope",
            ),
        ),
        migrations.AlterModelOptions(
            name="parkingvehiclelimit",
            options={
                "ordering": (
                    "society",
                    "member_role",
                    "vehicle_type",
                    "-start_date",
                    "-id",
                )
            },
        ),
    ]

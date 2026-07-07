# Generated for Phase 8 — Parcel Management.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gateops", "0006_material_movement"),
        ("housing", "0010_add_share_fields_to_member"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Parcel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tracking_number", models.CharField(max_length=100, verbose_name="tracking number")),
                ("courier", models.CharField(blank=True, max_length=100, verbose_name="courier")),
                ("is_cold_storage", models.BooleanField(default=False, verbose_name="cold storage")),
                ("is_fragile", models.BooleanField(default=False, verbose_name="fragile")),
                ("is_cod", models.BooleanField(default=False, verbose_name="cash on delivery")),
                ("cod_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="COD amount")),
                ("otp_code", models.CharField(blank=True, max_length=10, verbose_name="OTP code")),
                ("status", models.CharField(choices=[("received", "Received"), ("collected", "Collected"), ("returned", "Returned"), ("lost", "Lost")], db_index=True, default="received", max_length=20, verbose_name="status")),
                ("stored_at", models.DateTimeField(blank=True, null=True, verbose_name="stored at")),
                ("collected_at", models.DateTimeField(blank=True, null=True, verbose_name="collected at")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("gate_event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parcels", to="gateops.gateevent", verbose_name="gate event")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parcels", to="housing.society", verbose_name="society")),
                ("collected_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="collected_parcels", to=settings.AUTH_USER_MODEL, verbose_name="collected by")),
            ],
            options={
                "verbose_name": "Parcel",
                "verbose_name_plural": "Parcels",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "status"], name="parcel_soc_status_idx"),
                    models.Index(fields=["society", "gate_event"], name="parcel_soc_event_idx"),
                    models.Index(fields=["society", "tracking_number"], name="parcel_soc_tracking_idx"),
                    models.Index(fields=["society", "stored_at"], name="parcel_soc_stored_idx"),
                ],
            },
        ),
    ]

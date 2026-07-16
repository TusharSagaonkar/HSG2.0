# Generated for Phase 9: Contractor Management.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("gateops", "0007_parcel_management"),
        ("housing", "0010_add_share_fields_to_member"),
    ]

    operations = [
        migrations.CreateModel(
            name="Contractor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_name", models.CharField(max_length=200)),
                ("supervisor_name", models.CharField(blank=True, max_length=200)),
                ("supervisor_phone", models.CharField(blank=True, max_length=20)),
                ("contact_person", models.CharField(blank=True, max_length=200)),
                ("contact_phone", models.CharField(blank=True, max_length=20)),
                ("gst_number", models.CharField(blank=True, max_length=20)),
                ("pan_number", models.CharField(blank=True, max_length=20)),
                ("address", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contractors", to="housing.society")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "is_active"], name="contractor_soc_active_idx"),
                    models.Index(fields=["society", "company_name"], name="contractor_soc_name_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=["society", "company_name"],
                        name="unique_active_contractor_name_per_society",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Contract",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("max_workers", models.PositiveIntegerField(default=10)),
                ("status", models.CharField(choices=[("active", "Active"), ("completed", "Completed"), ("suspended", "Suspended")], default="active", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contracts", to="housing.society")),
                ("contractor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contracts", to="gateops.contractor")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "is_active"], name="contract_soc_active_idx"),
                    models.Index(fields=["society", "contractor", "is_active"], name="contract_soc_ctr_active_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=["society", "contractor", "title"],
                        name="unique_active_contract_title_per_society",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Worker",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("designation", models.CharField(blank=True, max_length=100)),
                ("id_type", models.CharField(blank=True, choices=[("aadhaar", "Aadhaar"), ("pan", "PAN"), ("voter_id", "Voter ID"), ("driving_license", "Driving License"), ("other", "Other")], max_length=20)),
                ("id_number", models.CharField(blank=True, max_length=50)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gate_workers", to="housing.society")),
                ("contract", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workers", to="gateops.contract")),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="worker_profiles", to="gateops.person")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "is_active"], name="worker_soc_active_idx"),
                    models.Index(fields=["society", "contract", "is_active"], name="worker_soc_ctr_active_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=["society", "contract", "person"],
                        name="unique_active_worker_per_contract",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="WorkPermit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("permit_number", models.CharField(max_length=50)),
                ("issued_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("safety_docs_verified", models.BooleanField(default=False)),
                ("safety_briefing_given", models.BooleanField(default=False)),
                ("work_area", models.CharField(blank=True, max_length=200)),
                ("hazard_level", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], default="low", max_length=10)),
                ("status", models.CharField(choices=[("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked")], default="active", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_permits", to="housing.society")),
                ("contract", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_permits", to="gateops.contract")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "is_active"], name="workpermit_soc_active_idx"),
                    models.Index(fields=["society", "contract", "is_active"], name="workpermit_soc_ctr_active_idx"),
                    models.Index(fields=["society", "expires_at"], name="workpermit_soc_expiry_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=["society", "permit_number"],
                        name="unique_active_workpermit_number_per_society",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="gateevent",
            name="contractor",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="gate_events", to="gateops.contractor"),
        ),
        migrations.AddField(
            model_name="gateevent",
            name="contract",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="gate_events", to="gateops.contract"),
        ),
        migrations.AddField(
            model_name="gateevent",
            name="work_permit",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="gate_events", to="gateops.workpermit"),
        ),
    ]

# Generated for Phase 13: Analytics.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("gateops", "0011_exit_management"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalyticsSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(db_index=True, verbose_name="date")),
                ("snapshot_type", models.CharField(choices=[("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("custom", "Custom Range")], default="daily", max_length=10, verbose_name="snapshot type")),
                ("metrics", models.JSONField(default=dict, verbose_name="metrics")),
                ("generated_at", models.DateTimeField(auto_now_add=True, verbose_name="generated at")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gateops_analytics_snapshots", to="housing.society", verbose_name="society")),
            ],
            options={
                "verbose_name": "Analytics Snapshot",
                "verbose_name_plural": "Analytics Snapshots",
                "ordering": ["-date", "-generated_at"],
                "indexes": [
                    models.Index(fields=["society", "-date"], name="analytics_snap_soc_date_idx"),
                    models.Index(fields=["society", "snapshot_type", "-date"], name="anlsnap_soc_type_date_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=["society", "date", "snapshot_type"],
                        name="uniq_analytics_snapshot_per_day",
                    ),
                ],
            },
        ),
    ]

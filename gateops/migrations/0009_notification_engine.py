# Generated for Phase 10: Smart Notification Engine.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("gateops", "0008_contractor_management"),
        ("housing", "0010_add_share_fields_to_member"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trigger", models.CharField(choices=[("arrival", "On Arrival"), ("entry", "On Entry"), ("exit", "On Exit"), ("never", "Never")], help_text="The trigger this bundle was created for.", max_length=20, verbose_name="trigger")),
                ("recipient_email", models.EmailField(blank=True, max_length=254, verbose_name="recipient email")),
                ("channel", models.CharField(choices=[("push", "Push"), ("sms", "SMS"), ("whatsapp", "WhatsApp"), ("email", "Email"), ("voice", "Voice Call"), ("none", "None")], max_length=20, verbose_name="channel")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("skipped", "Skipped")], db_index=True, default="pending", max_length=20, verbose_name="status")),
                ("bundle_window_minutes", models.PositiveIntegerField(default=0, help_text="The bundling window (in minutes) that was used for this bundle.", verbose_name="bundle window minutes")),
                ("dispatched_at", models.DateTimeField(blank=True, null=True, verbose_name="dispatched at")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notification_bundles", to="housing.society", verbose_name="society")),
                ("visitor_category", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notification_bundles", to="gateops.visitorcategory", verbose_name="visitor category")),
                ("host_unit", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="notification_bundles", to="housing.unit", verbose_name="host unit")),
                ("email_queue", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="notification_bundles", to="housing.emailqueue", verbose_name="email queue")),
            ],
            options={
                "verbose_name": "Notification Bundle",
                "verbose_name_plural": "Notification Bundles",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["society", "is_active"], name="notifbundle_soc_active_idx"),
                    models.Index(fields=["society", "host_unit", "is_active"], name="nb_soc_unit_active_idx"),
                    models.Index(fields=["society", "status", "is_active"], name="notifbundle_soc_status_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="notificationbundle",
            name="gate_events",
            field=models.ManyToManyField(blank=True, related_name="notification_bundles", to="gateops.gateevent", verbose_name="gate events"),
        ),
        migrations.AddField(
            model_name="gateevent",
            name="host_unit",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="hosted_gate_events",
                to="housing.unit",
            ),
        ),
    ]

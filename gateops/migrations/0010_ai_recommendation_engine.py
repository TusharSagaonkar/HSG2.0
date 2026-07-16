# Generated for Phase 11: AI Recommendation Engine.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("gateops", "0009_notification_engine"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Create VisitorPattern
        migrations.CreateModel(
            name="VisitorPattern",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("visit_count", models.PositiveIntegerField(default=0, verbose_name="visit count")),
                ("first_visit_at", models.DateTimeField(blank=True, null=True, verbose_name="first visit at")),
                ("last_visit_at", models.DateTimeField(blank=True, null=True, verbose_name="last visit at")),
                ("avg_visit_duration_minutes", models.PositiveIntegerField(blank=True, null=True, verbose_name="average visit duration (minutes)")),
                ("typical_visit_days", models.JSONField(default=list, verbose_name="typical visit days")),
                ("typical_time_window", models.JSONField(default=dict, verbose_name="typical time window")),
                ("is_frequent", models.BooleanField(default=False, verbose_name="is frequent")),
                ("frequency_score", models.FloatField(default=0.0, verbose_name="frequency score")),
                ("risk_score", models.FloatField(default=0.0, verbose_name="risk score")),
                ("risk_level", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], default="low", max_length=10, verbose_name="risk level")),
                ("last_analyzed_at", models.DateTimeField(blank=True, null=True, verbose_name="last analyzed at")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="visitor_patterns", to="housing.society", verbose_name="society")),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="visitor_patterns", to="gateops.person", verbose_name="person")),
                ("gate_vehicle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitor_patterns", to="gateops.gatevehicle", verbose_name="gate vehicle")),
                ("visitor_category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="visitor_patterns", to="gateops.visitorcategory", verbose_name="visitor category")),
                ("suggested_category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="suggested_in_patterns", to="gateops.visitorcategory", verbose_name="suggested category")),
                ("last_event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitor_patterns", to="gateops.gateevent", verbose_name="last event")),
            ],
            options={
                "verbose_name": "Visitor Pattern",
                "verbose_name_plural": "Visitor Patterns",
                "ordering": ("-last_visit_at",),
                "indexes": [
                    models.Index(fields=["society", "is_frequent"], name="vpat_soc_freq_idx"),
                    models.Index(fields=["society", "risk_level"], name="vpat_soc_risk_idx"),
                    models.Index(fields=["society", "last_visit_at"], name="vpat_soc_last_idx"),
                    models.Index(fields=["society", "is_active"], name="vpat_soc_active_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(is_active=True), fields=["society", "person"], name="unique_active_visitor_pattern_per_society"),
                ],
            },
        ),
        # 2. Create AnomalyDetection
        migrations.CreateModel(
            name="AnomalyDetection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("anomaly_type", models.CharField(choices=[("forgotten_exit", "Forgotten Exit"), ("after_hours_entry", "After-Hours Entry"), ("unusual_frequency", "Unusual Frequency Spike"), ("blacklist_bypass", "Blacklist Bypass Attempt"), ("off_pattern_visit", "Off-Pattern Visit"), ("duplicate_entry", "Duplicate Entry"), ("long_stay", "Abnormally Long Stay"), ("suspicious_pattern", "Suspicious Pattern")], max_length=30, verbose_name="anomaly type")),
                ("severity", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], default="medium", max_length=10, verbose_name="severity")),
                ("description", models.TextField(verbose_name="description")),
                ("context", models.JSONField(default=dict, verbose_name="context")),
                ("status", models.CharField(choices=[("open", "Open"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved"), ("false_positive", "False Positive")], db_index=True, default="open", max_length=20, verbose_name="status")),
                ("detected_at", models.DateTimeField(auto_now_add=True, verbose_name="detected at")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="resolved at")),
                ("resolution_notes", models.TextField(blank=True, verbose_name="resolution notes")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="anomaly_detections", to="housing.society", verbose_name="society")),
                ("gate_event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="anomaly_detections", to="gateops.gateevent", verbose_name="gate event")),
                ("person", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="anomaly_detections", to="gateops.person", verbose_name="person")),
                ("gate_vehicle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="anomaly_detections", to="gateops.gatevehicle", verbose_name="gate vehicle")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_anomalies", to=settings.AUTH_USER_MODEL, verbose_name="resolved by")),
            ],
            options={
                "verbose_name": "Anomaly Detection",
                "verbose_name_plural": "Anomaly Detections",
                "ordering": ("-detected_at",),
                "indexes": [
                    models.Index(fields=["society", "status"], name="anom_soc_status_idx"),
                    models.Index(fields=["society", "anomaly_type"], name="anom_soc_type_idx"),
                    models.Index(fields=["society", "severity"], name="anom_soc_sev_idx"),
                    models.Index(fields=["society", "detected_at"], name="anom_soc_detected_idx"),
                    models.Index(fields=["society", "is_active"], name="anom_soc_active_idx"),
                ],
            },
        ),
        # 3. Create PeakHourPrediction
        migrations.CreateModel(
            name="PeakHourPrediction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day_of_week", models.PositiveSmallIntegerField(help_text="ISO weekday: 0=Monday … 6=Sunday.", verbose_name="day of week")),
                ("hour", models.PositiveSmallIntegerField(help_text="Hour of day, 24-hour clock (0–23).", verbose_name="hour")),
                ("predicted_count", models.PositiveIntegerField(default=0, verbose_name="predicted count")),
                ("confidence_score", models.FloatField(default=0.0, verbose_name="confidence score")),
                ("actual_count", models.PositiveIntegerField(blank=True, null=True, verbose_name="actual count")),
                ("analysis_date", models.DateField(verbose_name="analysis date")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="deleted at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("society", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="peak_hour_predictions", to="housing.society", verbose_name="society")),
            ],
            options={
                "verbose_name": "Peak Hour Prediction",
                "verbose_name_plural": "Peak Hour Predictions",
                "ordering": ("day_of_week", "hour"),
                "indexes": [
                    models.Index(fields=["society", "day_of_week", "hour"], name="peak_soc_dow_hr_idx"),
                    models.Index(fields=["society", "analysis_date"], name="peak_soc_date_idx"),
                    models.Index(fields=["society", "is_active"], name="peak_soc_active_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(is_active=True), fields=["society", "day_of_week", "hour", "analysis_date"], name="unique_active_peak_hour_per_slot"),
                ],
            },
        ),
        # 4. Add RISK_SCORE to RuleCondition.field choices
        migrations.AlterField(
            model_name="rulecondition",
            name="field",
            field=models.CharField(choices=[("visitor_type", "Visitor Type"), ("time", "Time of Day"), ("date", "Date"), ("tower", "Tower"), ("wing", "Wing"), ("flat", "Flat"), ("resident", "Resident"), ("vehicle", "Vehicle"), ("guard", "Guard"), ("gate", "Gate"), ("max_visitors", "Max Visitors Inside"), ("max_stay", "Maximum Stay Hours"), ("holiday", "Holiday"), ("blacklist", "Blacklist Status"), ("contractor_expiry", "Contractor Expiry"), ("pass_valid", "Pass Validity"), ("visitor_category", "Visitor Category"), ("vehicle_category", "Vehicle Category"), ("is_emergency", "Is Emergency"), ("is_vip", "Is VIP"), ("risk_score", "Risk Score")], max_length=30),
        ),
        # 5. Add new actions to GateOpsAuditLog.action choices
        migrations.AlterField(
            model_name="gateopsauditlog",
            name="action",
            field=models.CharField(choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete"), ("approve", "Approve"), ("reject", "Reject"), ("entry", "Entry"), ("exit", "Exit"), ("rule_evaluated", "Rule Evaluated"), ("state_transition", "State Transition"), ("blacklist", "Blacklist"), ("escalate", "Escalate"), ("anomaly_detected", "Anomaly Detected"), ("pattern_updated", "Pattern Updated"), ("prediction_generated", "Prediction Generated")], max_length=30),
        ),
        # 6. Add ANOMALY trigger to NotificationPreference.trigger choices
        migrations.AlterField(
            model_name="notificationpreference",
            name="trigger",
            field=models.CharField(choices=[("arrival", "On Arrival"), ("entry", "On Entry"), ("exit", "On Exit"), ("never", "Never"), ("anomaly", "On Anomaly")], default="arrival", max_length=20),
        ),
        # 7. Add ANOMALY trigger to NotificationBundle.trigger choices
        #    (NotificationBundle.trigger reuses NotificationPreference.Trigger.choices)
        migrations.AlterField(
            model_name="notificationbundle",
            name="trigger",
            field=models.CharField(choices=[("arrival", "On Arrival"), ("entry", "On Entry"), ("exit", "On Exit"), ("never", "Never"), ("anomaly", "On Anomaly")], help_text="The trigger this bundle was created for.", max_length=20, verbose_name="trigger"),
        ),
    ]

# Generated manually to add parser profile support for Phase 2.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("housing", "0010_add_share_fields_to_member"),
        ("reconciliation", "0002_reconciliationlink_exception_type_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BankParserProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("bank_name", models.CharField(max_length=100)),
                ("format_name", models.CharField(max_length=120)),
                ("file_type", models.CharField(max_length=20)),
                ("header_signature", models.JSONField(blank=True, default=dict)),
                ("parser_class", models.CharField(max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("priority", models.IntegerField(default=100)),
                ("confidence_floor", models.IntegerField(default=70)),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "society",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bank_parser_profiles",
                        to="housing.society",
                    ),
                ),
            ],
            options={
                "ordering": ("-priority", "bank_name", "format_name"),
            },
        ),
        migrations.AddIndex(
            model_name="bankparserprofile",
            index=models.Index(
                fields=["society", "is_active"],
                name="reconciliat_societ_3d86d1_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="bankparserprofile",
            index=models.Index(
                fields=["society", "bank_name", "file_type"],
                name="reconciliat_societ_93fa1f_idx",
            ),
        ),
    ]

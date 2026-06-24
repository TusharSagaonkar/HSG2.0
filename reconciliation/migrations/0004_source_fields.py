from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0003_bankparserprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="bankstatementimport",
            name="source_type",
            field=models.CharField(
                default="FILE",
                help_text="FILE / MANUAL / COPY_PASTE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="banktransaction",
            name="source_row_index",
            field=models.IntegerField(
                blank=True,
                help_text="Row number from source or manual grid",
                null=True,
            ),
        ),
    ]

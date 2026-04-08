from django.db import migrations, models


def set_accumulated_depreciation_contra_flag(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(name__iexact="Accumulated Depreciation").update(
        is_contra=True,
        account_type="ASSET",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0017_reclassify_investment_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="is_contra",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            set_accumulated_depreciation_contra_flag,
            migrations.RunPython.noop,
        ),
    ]

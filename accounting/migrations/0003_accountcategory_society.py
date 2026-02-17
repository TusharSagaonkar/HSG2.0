import django.db.models.deletion
from django.db import migrations, models


def backfill_category_society(apps, schema_editor):
    AccountCategory = apps.get_model("accounting", "AccountCategory")

    for category in AccountCategory.objects.all():
        account = category.accounts.order_by("id").first()
        if account:
            category.society_id = account.society_id
            category.save(update_fields=["society"])
        else:
            category.delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("housing", "0003_unitoccupancy"),
        ("accounting", "0002_alter_account_unique_together"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountcategory",
            name="society",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="account_categories",
                to="housing.society",
            ),
        ),
        migrations.RunPython(backfill_category_society, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="accountcategory",
            name="society",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="account_categories",
                to="housing.society",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="accountcategory",
            unique_together={("society", "name", "account_type")},
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion


def mark_parent_accounts_non_posting(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    parent_ids = Account.objects.filter(children__isnull=False).values_list("pk", flat=True).distinct()
    Account.objects.exclude(pk__in=parent_ids).update(is_posting=True)
    Account.objects.filter(pk__in=parent_ids).update(is_posting=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0026_create_account_mapping"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="is_posting",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(mark_parent_accounts_non_posting, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="account",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="children",
                to="accounting.account",
            ),
        ),
    ]

from django.db import migrations


def reclassify_investment_accounts(apps, schema_editor):
    Society = apps.get_model("housing", "Society")
    Account = apps.get_model("accounting", "Account")
    AccountCategory = apps.get_model("accounting", "AccountCategory")

    target_names = {"Sinking Fund Investment", "Repair Fund Investment"}

    for society in Society.objects.all():
        investments_category, _ = AccountCategory.objects.get_or_create(
            society=society,
            name="Investments",
            account_type="ASSET",
        )
        accounts = Account.objects.filter(
            society=society,
            name__in=target_names,
            parent=None,
        )
        for account in accounts:
            account.category = investments_category
            account.account_type = "ASSET"
            account.save(update_fields=["category", "account_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0016_reclassify_fund_transfer_account"),
    ]

    operations = [
        migrations.RunPython(reclassify_investment_accounts, migrations.RunPython.noop),
    ]

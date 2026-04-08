from django.db import migrations


def reclassify_fund_transfer_account(apps, schema_editor):
    Society = apps.get_model("housing", "Society")
    Account = apps.get_model("accounting", "Account")
    AccountCategory = apps.get_model("accounting", "AccountCategory")

    for society in Society.objects.all():
        bank_cash_category, _ = AccountCategory.objects.get_or_create(
            society=society,
            name="Bank & Cash",
            account_type="ASSET",
        )
        account = Account.objects.filter(
            society=society,
            name="Fund Transfer Account",
            parent=None,
        ).first()
        if not account:
            continue
        account.category = bank_cash_category
        account.account_type = "ASSET"
        account.sub_type = "BANK"
        account.is_bank = True
        account.save(update_fields=["category", "account_type", "sub_type", "is_bank"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0015_deactivate_gst_payable"),
    ]

    operations = [
        migrations.RunPython(reclassify_fund_transfer_account, migrations.RunPython.noop),
    ]

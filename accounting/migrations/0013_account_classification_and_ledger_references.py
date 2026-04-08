from django.db import migrations, models


def _derive_metadata(name, account_type):
    name_l = (name or "").lower()
    metadata = {
        "account_type": account_type,
        "sub_type": "GENERAL",
        "is_gst": False,
        "gst_type": "NONE",
        "is_bank": False,
        "is_member_related": False,
        "is_vendor_related": False,
    }

    if "gst" in name_l:
        metadata["sub_type"] = "GST"
        metadata["is_gst"] = True
        if "input" in name_l or "receivable" in name_l:
            metadata["gst_type"] = "INPUT"
        elif "output" in name_l:
            metadata["gst_type"] = "OUTPUT"

    if any(keyword in name_l for keyword in ("bank", "cash", "cheque", "clearing")):
        metadata["sub_type"] = "BANK"
        metadata["is_bank"] = True

    if any(keyword in name_l for keyword in ("member", "maintenance receivable", "maintenance charges receivable")):
        metadata["sub_type"] = "MEMBER"
        metadata["is_member_related"] = True

    if any(keyword in name_l for keyword in ("vendor", "payable", "accrued expense")) and "member" not in name_l:
        metadata["is_vendor_related"] = True
        if metadata["sub_type"] == "GENERAL":
            metadata["sub_type"] = "EXPENSE"

    if "fund" in name_l:
        metadata["sub_type"] = "FUND"

    if account_type == "EXPENSE" and metadata["sub_type"] == "GENERAL":
        metadata["sub_type"] = "EXPENSE"
    if account_type == "INCOME" and metadata["sub_type"] == "GENERAL":
        metadata["sub_type"] = "INCOME"
    return metadata


def forwards(apps, schema_editor):
    Society = apps.get_model("housing", "Society")
    Account = apps.get_model("accounting", "Account")
    AccountCategory = apps.get_model("accounting", "AccountCategory")
    LedgerEntry = apps.get_model("accounting", "LedgerEntry")

    default_accounts = [
        ("Output CGST", "LIABILITY", "Statutory Liabilities"),
        ("Output SGST", "LIABILITY", "Statutory Liabilities"),
        ("Output IGST", "LIABILITY", "Statutory Liabilities"),
        ("Input CGST", "ASSET", "Current Assets"),
        ("Input SGST", "ASSET", "Current Assets"),
        ("Input IGST", "ASSET", "Current Assets"),
        ("Bank Clearing Account", "ASSET", "Bank & Cash"),
        ("Cheque in Hand", "ASSET", "Bank & Cash"),
        ("Cheque Issued but Not Cleared", "LIABILITY", "Current Liabilities"),
        ("Member Advance", "LIABILITY", "Member Deposits"),
        ("Member Refund Payable", "LIABILITY", "Current Liabilities"),
        ("Vendor Advance", "ASSET", "Deposits Given"),
        ("Expense Payable", "LIABILITY", "Current Liabilities"),
        ("Sinking Fund Investment", "ASSET", "Fixed Assets"),
        ("Repair Fund Investment", "ASSET", "Fixed Assets"),
        ("Depreciation Expense", "EXPENSE", "Miscellaneous"),
        ("Accumulated Depreciation", "ASSET", "Fixed Assets"),
        ("TDS Receivable", "ASSET", "Current Assets"),
        ("GST Receivable", "ASSET", "Current Assets"),
        ("Suspense Account", "LIABILITY", "Current Liabilities"),
        ("Rounding Off Account", "EXPENSE", "Miscellaneous"),
        ("Fund Transfer Account", "LIABILITY", "Current Liabilities"),
        ("Prepaid Expenses", "ASSET", "Current Assets"),
    ]

    for society in Society.objects.all():
        for name, account_type, category_name in default_accounts:
            category = AccountCategory.objects.filter(
                society=society,
                name=category_name,
                account_type=account_type,
            ).first()
            if not category:
                continue
            metadata = _derive_metadata(name, account_type)
            Account.objects.get_or_create(
                society=society,
                name=name,
                parent=None,
                defaults={
                    "category": category,
                    "system_protected": True,
                    **metadata,
                },
            )

        for account in Account.objects.filter(society=society).select_related("category"):
            metadata = _derive_metadata(account.name, account.category.account_type)
            for field_name, value in metadata.items():
                setattr(account, field_name, value)
            account.save(
                update_fields=[
                    "account_type",
                    "sub_type",
                    "is_gst",
                    "gst_type",
                    "is_bank",
                    "is_member_related",
                    "is_vendor_related",
                ]
            )

        gst_payable = Account.objects.filter(society=society, name="GST Payable").first()
        output_cgst = Account.objects.filter(society=society, name="Output CGST").first()
        if gst_payable and output_cgst:
            LedgerEntry.objects.filter(account=gst_payable).update(account=output_cgst)
            gst_payable.is_active = False
            gst_payable.save(update_fields=["is_active"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0012_drop_single_open_period_constraint_if_exists"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="account_type",
            field=models.CharField(
                choices=[
                    ("ASSET", "Asset"),
                    ("LIABILITY", "Liability"),
                    ("INCOME", "Income"),
                    ("EXPENSE", "Expense"),
                    ("EQUITY", "Equity"),
                ],
                default="ASSET",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="account",
            name="sub_type",
            field=models.CharField(
                choices=[
                    ("GST", "GST"),
                    ("BANK", "Bank"),
                    ("MEMBER", "Member"),
                    ("FUND", "Fund"),
                    ("EXPENSE", "Expense"),
                    ("INCOME", "Income"),
                    ("GENERAL", "General"),
                ],
                default="GENERAL",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="account",
            name="is_gst",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="account",
            name="gst_type",
            field=models.CharField(
                choices=[("INPUT", "Input"), ("OUTPUT", "Output"), ("NONE", "None")],
                default="NONE",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="account",
            name="is_bank",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="account",
            name="is_member_related",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="account",
            name="is_vendor_related",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="reference_type",
            field=models.CharField(
                choices=[
                    ("NONE", "None"),
                    ("MEMBER", "Member"),
                    ("VENDOR", "Vendor"),
                    ("UNIT", "Unit"),
                    ("OTHER", "Other"),
                ],
                default="NONE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="reference_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

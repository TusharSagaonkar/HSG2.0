from django.db import migrations


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
        "is_contra": False,
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
    if name_l.strip() == "fund transfer account":
        metadata["sub_type"] = "BANK"
        metadata["is_bank"] = True
    if "accumulated depreciation" in name_l:
        metadata["is_contra"] = True
    if account_type == "EXPENSE" and metadata["sub_type"] == "GENERAL":
        metadata["sub_type"] = "EXPENSE"
    if account_type == "INCOME" and metadata["sub_type"] == "GENERAL":
        metadata["sub_type"] = "INCOME"
    return metadata


def build_hierarchy(apps, schema_editor):
    Society = apps.get_model("housing", "Society")
    Account = apps.get_model("accounting", "Account")
    AccountCategory = apps.get_model("accounting", "AccountCategory")

    group_defs = [
        ("Assets", "ASSET", "Current Assets", None),
        ("Current Assets", "ASSET", "Current Assets", "Assets"),
        ("Bank Accounts", "ASSET", "Bank & Cash", "Current Assets"),
        ("Receivables", "ASSET", "Member Receivables", "Current Assets"),
        ("Input GST", "ASSET", "Current Assets", "Current Assets"),
        ("Fixed Assets", "ASSET", "Fixed Assets", "Assets"),
        ("Investments", "ASSET", "Investments", "Assets"),
    ]
    leaf_parent_map = {
        "Cash in Hand": "Bank Accounts",
        "Bank Account – Main": "Bank Accounts",
        "Bank Account – Sinking Fund": "Bank Accounts",
        "Bank Account – Repair Fund": "Bank Accounts",
        "Bank Clearing Account": "Bank Accounts",
        "Cheque in Hand": "Bank Accounts",
        "Maintenance Receivable": "Receivables",
        "Interest Receivable": "Receivables",
        "Parking Charges Receivable": "Receivables",
        "TDS Receivable": "Receivables",
        "GST Receivable": "Receivables",
        "Input CGST": "Input GST",
        "Input SGST": "Input GST",
        "Input IGST": "Input GST",
        "Building Structure": "Fixed Assets",
        "Lift": "Fixed Assets",
        "Generator": "Fixed Assets",
        "Furniture & Fixtures": "Fixed Assets",
        "Office Equipment": "Fixed Assets",
        "Accumulated Depreciation": "Fixed Assets",
        "Sinking Fund Investment": "Investments",
        "Repair Fund Investment": "Investments",
    }

    for society in Society.objects.all():
        group_accounts = {}
        for name, account_type, category_name, parent_name in group_defs:
            category = AccountCategory.objects.filter(
                society=society,
                name=category_name,
                account_type=account_type,
            ).first()
            if not category:
                continue
            parent = group_accounts.get(parent_name) if parent_name else None
            metadata = _derive_metadata(name, account_type)
            account, _ = Account.objects.get_or_create(
                society=society,
                name=name,
                parent=parent,
                defaults={
                    "category": category,
                    "system_protected": True,
                    "is_active": False,
                    **metadata,
                },
            )
            account.category = category
            account.parent = parent
            account.system_protected = True
            account.is_active = False
            for field_name, value in metadata.items():
                setattr(account, field_name, value)
            account.save(
                update_fields=[
                    "category",
                    "parent",
                    "system_protected",
                    "is_active",
                    "account_type",
                    "sub_type",
                    "is_gst",
                    "gst_type",
                    "is_bank",
                    "is_member_related",
                    "is_vendor_related",
                    "is_contra",
                ]
            )
            group_accounts[name] = account

        for leaf_name, parent_name in leaf_parent_map.items():
            parent = group_accounts.get(parent_name)
            leaf = Account.objects.filter(society=society, name=leaf_name, parent__isnull=True).first()
            if leaf and parent:
                leaf.parent = parent
                leaf.save(update_fields=["parent"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0018_account_is_contra"),
    ]

    operations = [
        migrations.RunPython(build_hierarchy, migrations.RunPython.noop),
    ]

from accounting.models import Account
from accounting.models import AccountCategory

DEFAULT_CATEGORY_DEFINITIONS = [
    ("Current Assets", "ASSET"),
    ("Bank & Cash", "ASSET"),
    ("Member Receivables", "ASSET"),
    ("Deposits Given", "ASSET"),
    ("Fixed Assets", "ASSET"),
    ("Investments", "ASSET"),
    ("Current Liabilities", "LIABILITY"),
    ("Member Deposits", "LIABILITY"),
    ("Vendor Payables", "LIABILITY"),
    ("Statutory Liabilities", "LIABILITY"),
    ("Opening Balance Fund", "EQUITY"),
    ("Reserve Fund", "EQUITY"),
    ("Sinking Fund", "EQUITY"),
    ("Repair & Maintenance Fund", "EQUITY"),
    ("Parking Fund", "EQUITY"),
    ("Share Capital", "EQUITY"),
    ("Maintenance Charges", "INCOME"),
    ("Interest Income", "INCOME"),
    ("Penalty / Late Fees", "INCOME"),
    ("Parking Charges", "INCOME"),
    ("Transfer Fees", "INCOME"),
    ("Rental Income", "INCOME"),
    ("Utility Expenses", "EXPENSE"),
    ("Administrative Expenses", "EXPENSE"),
    ("Repair & Maintenance Expenses", "EXPENSE"),
    ("Staff Salary & Wages", "EXPENSE"),
    ("Security & Housekeeping", "EXPENSE"),
    ("Professional Charges", "EXPENSE"),
    ("Bank Charges", "EXPENSE"),
    ("Miscellaneous", "EXPENSE"),
]

DEFAULT_ACCOUNT_DEFINITIONS = [
    ("Cash in Hand", "ASSET", "Bank & Cash"),
    ("Bank Account – Main", "ASSET", "Bank & Cash"),
    ("Bank Account – Sinking Fund", "ASSET", "Bank & Cash"),
    ("Bank Account – Repair Fund", "ASSET", "Bank & Cash"),
    ("Maintenance Receivable", "ASSET", "Member Receivables"),
    ("Interest Receivable", "ASSET", "Member Receivables"),
    ("Parking Charges Receivable", "ASSET", "Member Receivables"),
    ("Electricity Deposit", "ASSET", "Deposits Given"),
    ("Water Deposit", "ASSET", "Deposits Given"),
    ("Security Deposit Given", "ASSET", "Deposits Given"),
    ("Building Structure", "ASSET", "Fixed Assets"),
    ("Lift", "ASSET", "Fixed Assets"),
    ("Generator", "ASSET", "Fixed Assets"),
    ("Furniture & Fixtures", "ASSET", "Fixed Assets"),
    ("Office Equipment", "ASSET", "Fixed Assets"),
    ("Security Deposit – Members", "LIABILITY", "Member Deposits"),
    ("Advance Maintenance", "LIABILITY", "Member Deposits"),
    ("Vendor Payable", "LIABILITY", "Vendor Payables"),
    ("TDS Payable", "LIABILITY", "Statutory Liabilities"),
    ("Professional Tax Payable", "LIABILITY", "Statutory Liabilities"),
    ("Opening Balance Fund", "EQUITY", "Opening Balance Fund"),
    ("General Reserve", "EQUITY", "Reserve Fund"),
    ("Sinking Fund", "EQUITY", "Sinking Fund"),
    ("Repair & Maintenance Fund", "EQUITY", "Repair & Maintenance Fund"),
    ("Parking Fund", "EQUITY", "Parking Fund"),
    ("Share Capital", "EQUITY", "Share Capital"),
    ("Surplus / Deficit", "EQUITY", "Reserve Fund"),
    ("Maintenance Charges", "INCOME", "Maintenance Charges"),
    ("Late Payment Penalty", "INCOME", "Penalty / Late Fees"),
    ("Interest Income – Bank", "INCOME", "Interest Income"),
    ("Interest Income – Member", "INCOME", "Interest Income"),
    ("Parking Charges", "INCOME", "Parking Charges"),
    ("Transfer Fees", "INCOME", "Transfer Fees"),
    ("Rental Income – Common Area", "INCOME", "Rental Income"),
    ("Other Income", "INCOME", "Maintenance Charges"),
    ("Electricity Expense", "EXPENSE", "Utility Expenses"),
    ("Water Expense", "EXPENSE", "Utility Expenses"),
    ("Salary Expense", "EXPENSE", "Staff Salary & Wages"),
    ("Security Charges", "EXPENSE", "Security & Housekeeping"),
    ("Housekeeping Charges", "EXPENSE", "Security & Housekeeping"),
    ("Lift Maintenance", "EXPENSE", "Repair & Maintenance Expenses"),
    ("Generator Maintenance", "EXPENSE", "Repair & Maintenance Expenses"),
    ("Plumbing Repairs", "EXPENSE", "Repair & Maintenance Expenses"),
    ("Civil Repairs", "EXPENSE", "Repair & Maintenance Expenses"),
    ("Printing & Stationery", "EXPENSE", "Administrative Expenses"),
    ("Audit Fees", "EXPENSE", "Professional Charges"),
    ("Legal Fees", "EXPENSE", "Professional Charges"),
    ("Software Expense", "EXPENSE", "Administrative Expenses"),
    ("Bank Charges", "EXPENSE", "Bank Charges"),
    ("Miscellaneous Expense", "EXPENSE", "Miscellaneous"),
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
    ("Sinking Fund Investment", "ASSET", "Investments"),
    ("Repair Fund Investment", "ASSET", "Investments"),
    ("Depreciation Expense", "EXPENSE", "Miscellaneous"),
    ("Accumulated Depreciation", "ASSET", "Fixed Assets"),
    ("TDS Receivable", "ASSET", "Current Assets"),
    ("GST Receivable", "ASSET", "Current Assets"),
    ("Suspense Account", "LIABILITY", "Current Liabilities"),
    ("Rounding Off Account", "EXPENSE", "Miscellaneous"),
    ("Fund Transfer Account", "ASSET", "Bank & Cash"),
    ("Prepaid Expenses", "ASSET", "Current Assets"),
]

GROUP_ACCOUNT_DEFINITIONS = [
    ("Assets", "ASSET", "Current Assets", None),
    ("Current Assets", "ASSET", "Current Assets", "Assets"),
    ("Bank Accounts", "ASSET", "Bank & Cash", "Current Assets"),
    ("Receivables", "ASSET", "Member Receivables", "Current Assets"),
    ("Input GST", "ASSET", "Current Assets", "Current Assets"),
    ("Fixed Assets", "ASSET", "Fixed Assets", "Assets"),
    ("Investments", "ASSET", "Investments", "Assets"),
]

LEAF_PARENT_MAP = {
    "Cash in Hand": "Current Assets",
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


def derive_account_metadata(name: str, account_type: str):
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
        "is_clearing": False,
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
    if any(keyword in name_l for keyword in ("clearing", "suspense")) or name_l.strip() == "fund transfer account":
        metadata["is_clearing"] = True

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


def ensure_standard_categories(society):
    """
    Create required categories for one society (idempotent).
    """
    for name, account_type in DEFAULT_CATEGORY_DEFINITIONS:
        AccountCategory.objects.get_or_create(
            society=society,
            name=name,
            account_type=account_type,
        )


def create_default_accounts_for_society(society):
    """
    Create mandatory accounts for ONE society.
    Idempotent. Slice-2 only.
    """
    def _get_existing_account_by_name(name):
        return (
            Account.objects.filter(society=society, name=name)
            .order_by("id")
            .first()
        )

    group_accounts = {}
    for name, acc_type, category_name, parent_name in GROUP_ACCOUNT_DEFINITIONS:
        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=acc_type,
        )
        parent = group_accounts.get(parent_name) if parent_name else None
        metadata = derive_account_metadata(name, acc_type)
        account = _get_existing_account_by_name(name)
        if account is None:
            account = Account(
                society=society,
                name=name,
                category=category,
                parent=parent,
                system_protected=True,
                is_active=False,
                **metadata,
            )
        account.category = category
        account.parent = parent
        account.system_protected = True
        account.is_active = False
        for field_name, value in metadata.items():
            setattr(account, field_name, value)
        if account.pk:
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
                    "is_clearing",
                ]
            )
        else:
            account.save()
        group_accounts[name] = account

    for name, acc_type, category_name in DEFAULT_ACCOUNT_DEFINITIONS:
        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=acc_type,
        )
        metadata = derive_account_metadata(name, acc_type)
        parent_name = LEAF_PARENT_MAP.get(name)
        parent_account = group_accounts.get(parent_name) if parent_name else None

        account = _get_existing_account_by_name(name)
        if account is None:
            account = Account(
                society=society,
                name=name,
                category=category,
                parent=parent_account,
                system_protected=True,
                **metadata,
            )
            account.save()
            continue

        updates = []
        if account.category_id != category.id:
            account.category = category
            updates.append("category")
        if account.parent_id != (parent_account.id if parent_account else None):
            account.parent = parent_account
            updates.append("parent")
        for field_name, value in metadata.items():
            if getattr(account, field_name) != value:
                setattr(account, field_name, value)
                updates.append(field_name)
        if not account.system_protected:
            account.system_protected = True
            updates.append("system_protected")
        if updates:
            account.save(update_fields=updates)

    # Legacy compatibility: keep old GST Payable account non-postable/inactive.
    Account.objects.filter(society=society, name="GST Payable").update(is_active=False)


def ensure_standard_accounts(society):
    """
    Backward-compatible helper: create defaults for one society.
    """
    create_default_accounts_for_society(society)

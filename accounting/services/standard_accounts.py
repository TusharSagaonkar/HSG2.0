from accounting.models import Account
from accounting.models import AccountCategory

DEFAULT_CATEGORY_DEFINITIONS = [
    ("Current Assets", "ASSET"),
    ("Bank & Cash", "ASSET"),
    ("Member Receivables", "ASSET"),
    ("Deposits Given", "ASSET"),
    ("Fixed Assets", "ASSET"),
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
    ("GST Payable", "LIABILITY", "Statutory Liabilities"),
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
]


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
    for name, acc_type, category_name in DEFAULT_ACCOUNT_DEFINITIONS:
        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=acc_type,
        )

        Account.objects.get_or_create(
            society=society,
            name=name,
            category=category,
            parent=None,
            defaults={"system_protected": True},
        )


def ensure_standard_accounts(society):
    """
    Backward-compatible helper: create defaults for one society.
    """
    create_default_accounts_for_society(society)

from accounting.models import AccountCategory, Account

STANDARD_CATEGORIES = {
    "ASSET": [
        "Cash",
        "Maintenance Receivable",
        "Advance Receivable",
    ],
    "LIABILITY": [
        "Advance from Members",
        "Security Deposit Payable",
    ],
    "INCOME": [
        "Maintenance Income",
        "Penalty Income",
        "Interest Income",
        "Other Income",
    ],
    "EXPENSE": [
        "Electricity Expense",
        "Water Expense",
        "Security Expense",
        "Repair & Maintenance",
        "Admin Expense",
    ],
    "EQUITY": [
        "Capital",
        "Opening Balance",
    ],
}


def ensure_standard_categories():
    """
    Create global account categories (idempotent).
    """
    for account_type, names in STANDARD_CATEGORIES.items():
        for name in names:
            AccountCategory.objects.get_or_create(
                name=name,
                account_type=account_type,
            )


def ensure_standard_accounts():
    """
    Create baseline GLOBAL accounts for Slice-1.
    Safe to run multiple times.
    """
    required_accounts = [
        ("Cash", "ASSET"),
        ("Maintenance Receivable", "ASSET"),
        ("Maintenance Income", "INCOME"),
        ("Penalty Income", "INCOME"),
        ("Opening Balance", "EQUITY"),
    ]

    for name, acc_type in required_accounts:
        category = AccountCategory.objects.get(
            name=name,
            account_type=acc_type,
        )

        Account.objects.get_or_create(
            name=name,
            category=category,
            parent=None,
        )
def create_default_accounts_for_society(society):
    """
    Create mandatory accounts for ONE society.
    Idempotent. Slice-2 only.
    """
    required_accounts = [
        ("Cash", "ASSET"),
        ("Maintenance Receivable", "ASSET"),
        ("Maintenance Income", "INCOME"),
        ("Penalty Income", "INCOME"),
        ("Opening Balance", "EQUITY"),
    ]

    for name, acc_type in required_accounts:
        category = AccountCategory.objects.get(
            name=name,
            account_type=acc_type,
        )

        Account.objects.get_or_create(
            society=society,
            name=name,
            category=category,
            parent=None,
        )

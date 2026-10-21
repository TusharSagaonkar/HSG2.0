"""Standard chart of accounts shared by every society.

``_ACCOUNT_TREE_ROWS`` is the single source of truth. The categories, group
accounts, leaf accounts and the leaf→parent map are all derived from it, so
they cannot drift apart.

Each node maps one-to-one onto the ``Account`` model fields. ``is_posting`` is
deliberately *not* stored on the node: it is derived from whether the node has
children, so a new group can never be left postable by accident.

Row layout (kept in the historical order for readability):

    (code, name, account_type, parent_code, sub_type, is_gst, gst_type,
     is_bank, is_contra, is_clearing, is_member_related, is_vendor_related)

    account_type: ASSET, LIABILITY, INCOME, EXPENSE, EQUITY
    sub_type:     GST, BANK, MEMBER, FUND, EXPENSE, INCOME, GENERAL
    gst_type:     INPUT, OUTPUT, NONE
"""

from __future__ import annotations

from typing import NamedTuple

from accounting.models import Account
from accounting.models import AccountCategory


class AccountNode(NamedTuple):
    """One node in the standard account tree.

    Field order matches the historical 12-value tuple so that rows can be
    unpacked positionally, while new code can use the named attributes.
    """

    code: str
    name: str
    account_type: str
    parent_code: str | None = None
    sub_type: str = "GENERAL"
    is_gst: bool = False
    gst_type: str = "NONE"
    is_bank: bool = False
    is_contra: bool = False
    is_clearing: bool = False
    is_member_related: bool = False
    is_vendor_related: bool = False

    @property
    def is_root(self) -> bool:
        return self.parent_code is None

    @property
    def level(self) -> int:
        """Depth in the hierarchy: ``1`` for roots, ``2`` for ``1.1``, ..."""
        return len(self.code.split("."))

    @property
    def root_code(self) -> str:
        return self.code.split(".")[0]

    @property
    def sort_key(self) -> tuple[int, ...]:
        """Natural sort key (``1.2`` before ``1.10``)."""
        return tuple(int(part) for part in self.code.split("."))

    @property
    def is_group(self) -> bool:
        """True when the node has children and therefore cannot be posted to."""
        return self.code in _GROUP_CODES

    @property
    def is_posting(self) -> bool:
        return not self.is_group


_ACCOUNT_TREE_ROWS: tuple[tuple, ...] = (
    # ========================================================================
    # Root nodes (Level 0)
    # ========================================================================
    ("1", "Assets", "ASSET", None, "GENERAL", False, "NONE", False, False, False, False, False),
    ("2", "Liabilities", "LIABILITY", None, "GENERAL", False, "NONE", False, False, False, False, False),
    ("3", "Income", "INCOME", None, "GENERAL", False, "NONE", False, False, False, False, False),
    ("4", "Expenses", "EXPENSE", None, "GENERAL", False, "NONE", False, False, False, False, False),
    ("5", "Equity / Funds", "EQUITY", None, "GENERAL", False, "NONE", False, False, False, False, False),

    # ========================================================================
    # 1. ASSETS (Level 1 children)
    # ========================================================================
    ("1.1", "Fixed Assets", "ASSET", "1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.2", "Deposits", "ASSET", "1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.3", "Investments", "ASSET", "1", "FUND", False, "NONE", False, False, False, False, False),
    ("1.4", "Bank & Cash", "ASSET", "1", "BANK", False, "NONE", True, False, False, False, False),
    ("1.5", "Receivables", "ASSET", "1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("1.6", "Advances", "ASSET", "1", "GENERAL", False, "NONE", False, False, False, False, True),
    ("1.7", "GST Input", "ASSET", "1", "GST", True, "INPUT", False, False, False, False, False),
    ("1.8", "Other Assets", "ASSET", "1", "GENERAL", False, "NONE", False, False, False, False, False),

    # 1.1 Fixed Assets children (Level 2)
    ("1.1.1", "Building Structure", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.2", "Lift & Elevator", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.3", "Generator / DG Set", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.4", "Water Pump & Motor", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.5", "CCTV System", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.6", "Furniture & Fixtures", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.7", "Office Equipment", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.1.8", "Electrical Installations", "ASSET", "1.1", "GENERAL", False, "NONE", False, False, False, False, False),

    # 1.2 Deposits children (Level 2)
    ("1.2.1", "Electricity Deposit", "ASSET", "1.2", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.2.2", "Water Deposit", "ASSET", "1.2", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.2.3", "Security Deposit Given", "ASSET", "1.2", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.2.4", "Other Utility Deposits", "ASSET", "1.2", "GENERAL", False, "NONE", False, False, False, False, False),

    # 1.3 Investments children (Level 2)
    ("1.3.1", "Fixed Deposits (FD)", "ASSET", "1.3", "FUND", False, "NONE", False, False, False, False, False),
    ("1.3.2", "Sinking Fund Investment", "ASSET", "1.3", "FUND", False, "NONE", False, False, False, False, False),
    ("1.3.3", "Repair Fund Investment", "ASSET", "1.3", "FUND", False, "NONE", False, False, False, False, False),
    ("1.3.4", "Reserve Fund Investment", "ASSET", "1.3", "FUND", False, "NONE", False, False, False, False, False),

    # 1.4 Bank & Cash children (Level 2)
    ("1.4.1", "Cash-in-Hand", "ASSET", "1.4", "BANK", False, "NONE", True, False, False, False, False),
    ("1.4.2", "Bank Accounts", "ASSET", "1.4", "BANK", False, "NONE", True, False, False, False, False),
    ("1.4.3", "Fund Transfer Account", "ASSET", "1.4", "BANK", False, "NONE", True, True, False, False, False),

    # 1.4.2 Bank Accounts children (Level 3)
    ("1.4.2.1", "Bank – Maintenance Account", "ASSET", "1.4.2", "BANK", False, "NONE", True, False, False, False, False),
    ("1.4.2.2", "Bank – Sinking Fund", "ASSET", "1.4.2", "BANK", False, "NONE", True, False, False, False, False),
    ("1.4.2.3", "Bank – Repair Fund", "ASSET", "1.4.2", "BANK", False, "NONE", True, False, False, False, False),
    ("1.4.2.4", "Bank – Parking Fund", "ASSET", "1.4.2", "BANK", False, "NONE", True, False, False, False, False),

    # 1.5 Receivables children (Level 2)
    ("1.5.1", "Member Receivable", "ASSET", "1.5", "MEMBER", False, "NONE", False, False, False, True, False),
    ("1.5.2", "Vendor Receivable", "ASSET", "1.5", "GENERAL", False, "NONE", False, False, False, False, True),
    ("1.5.3", "Interest Receivable – Bank", "ASSET", "1.5", "GENERAL", False, "NONE", False, False, False, False, False),

    # 1.5.1 Member Receivable children (Level 3)
    ("1.5.1.1", "Maintenance Due", "ASSET", "1.5.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("1.5.1.2", "Parking Due", "ASSET", "1.5.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("1.5.1.3", "Interest on Arrears", "ASSET", "1.5.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("1.5.1.4", "Other Member Dues", "ASSET", "1.5.1", "MEMBER", False, "NONE", False, False, False, True, False),

    # 1.6 Advances children (Level 2)
    ("1.6.1", "Vendor Advance", "ASSET", "1.6", "GENERAL", False, "NONE", False, False, False, False, True),
    ("1.6.2", "Staff Advance", "ASSET", "1.6", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.6.3", "Prepaid Expenses", "ASSET", "1.6", "GENERAL", False, "NONE", False, False, False, False, False),

    # 1.7 GST Input children (Level 2)
    ("1.7.1", "Input CGST", "ASSET", "1.7", "GST", True, "INPUT", False, False, False, False, False),
    ("1.7.2", "Input SGST", "ASSET", "1.7", "GST", True, "INPUT", False, False, False, False, False),
    ("1.7.3", "Input IGST", "ASSET", "1.7", "GST", True, "INPUT", False, False, False, False, False),

    # 1.8 Other Assets children (Level 2)
    ("1.8.1", "Accrued Income", "ASSET", "1.8", "GENERAL", False, "NONE", False, False, False, False, False),
    ("1.8.2", "Suspense (Debit)", "ASSET", "1.8", "GENERAL", False, "NONE", False, False, False, False, False),

    # ========================================================================
    # 2. LIABILITIES (Level 1 children)
    # ========================================================================
    ("2.1", "Member Liabilities", "LIABILITY", "2", "MEMBER", False, "NONE", False, False, False, True, False),
    ("2.2", "Vendor & Expense Payables", "LIABILITY", "2", "EXPENSE", False, "NONE", False, False, False, False, True),
    ("2.3", "Statutory Liabilities", "LIABILITY", "2", "GST", False, "NONE", False, False, False, False, False),
    ("2.4", "Bank & Clearing", "LIABILITY", "2", "BANK", False, "NONE", False, False, True, False, False),
    ("2.5", "Provisions", "LIABILITY", "2", "GENERAL", False, "NONE", False, False, False, False, False),
    ("2.6", "Other Liabilities", "LIABILITY", "2", "GENERAL", False, "NONE", False, False, False, False, False),

    # 2.1 Member Liabilities children (Level 2)
    ("2.1.1", "Advance Maintenance", "LIABILITY", "2.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("2.1.2", "Member Advance", "LIABILITY", "2.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("2.1.3", "Member Refund Payable", "LIABILITY", "2.1", "MEMBER", False, "NONE", False, False, False, True, False),
    ("2.1.4", "Security Deposit – Members", "LIABILITY", "2.1", "MEMBER", False, "NONE", False, False, False, True, False),

    # 2.2 Vendor & Expense Payables children (Level 2)
    ("2.2.1", "Vendor Payable", "LIABILITY", "2.2", "EXPENSE", False, "NONE", False, False, False, False, True),
    ("2.2.2", "Expense Payable", "LIABILITY", "2.2", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("2.2.3", "Audit Fees Payable", "LIABILITY", "2.2", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 2.3 Statutory Liabilities children (Level 2)
    ("2.3.1", "GST Payable", "LIABILITY", "2.3", "GST", True, "OUTPUT", False, False, False, False, False),
    ("2.3.2", "TDS Payable", "LIABILITY", "2.3", "GENERAL", False, "NONE", False, False, False, False, False),
    ("2.3.3", "Professional Tax Payable", "LIABILITY", "2.3", "GENERAL", False, "NONE", False, False, False, False, False),
    ("2.3.4", "GST TDS / Reverse Charge", "LIABILITY", "2.3", "GST", False, "NONE", False, False, False, False, False),

    # 2.3.1 GST Payable children (Level 3)
    ("2.3.1.1", "Output CGST", "LIABILITY", "2.3.1", "GST", True, "OUTPUT", False, False, False, False, False),
    ("2.3.1.2", "Output SGST", "LIABILITY", "2.3.1", "GST", True, "OUTPUT", False, False, False, False, False),
    ("2.3.1.3", "Output IGST", "LIABILITY", "2.3.1", "GST", True, "OUTPUT", False, False, False, False, False),

    # 2.4 Bank & Clearing children (Level 2)
    ("2.4.1", "Cheque Issued but Not Cleared", "LIABILITY", "2.4", "BANK", False, "NONE", False, False, True, False, False),
    ("2.4.2", "Cheque Deposited but Not Cleared", "LIABILITY", "2.4", "BANK", False, "NONE", False, False, True, False, False),
    ("2.4.3", "Payment Gateway Clearing", "LIABILITY", "2.4", "BANK", False, "NONE", False, False, True, False, False),

    # 2.5 Provisions children (Level 2)
    ("2.5.1", "Provision for Expenses", "LIABILITY", "2.5", "GENERAL", False, "NONE", False, False, False, False, False),
    ("2.5.2", "Provision for Audit", "LIABILITY", "2.5", "GENERAL", False, "NONE", False, False, False, False, False),

    # 2.6 Other Liabilities children (Level 2)
    ("2.6.1", "Suspense Account", "LIABILITY", "2.6", "GENERAL", False, "NONE", False, False, False, False, False),

    # ========================================================================
    # 3. INCOME (Level 1 children)
    # ========================================================================
    ("3.1", "Member Income", "INCOME", "3", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.2", "Financial Income", "INCOME", "3", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.3", "Commercial Income", "INCOME", "3", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.4", "Other Income", "INCOME", "3", "INCOME", False, "NONE", False, False, False, False, False),

    # 3.1 Member Income children (Level 2)
    ("3.1.1", "Maintenance Charges", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.2", "Service Charges", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.3", "Parking Charges", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.4", "Transfer Fees", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.5", "Non-Occupancy Charges", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.6", "Late Payment Penalty", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.1.7", "Interest Income – Member", "INCOME", "3.1", "INCOME", False, "NONE", False, False, False, False, False),

    # 3.2 Financial Income children (Level 2)
    ("3.2.1", "Interest Income – Bank", "INCOME", "3.2", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.2.2", "Interest on FD", "INCOME", "3.2", "INCOME", False, "NONE", False, False, False, False, False),

    # 3.3 Commercial Income children (Level 2)
    ("3.3.1", "Rental Income – Common Area", "INCOME", "3.3", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.3.2", "Advertisement Income", "INCOME", "3.3", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.3.3", "Mobile Tower Income", "INCOME", "3.3", "INCOME", False, "NONE", False, False, False, False, False),

    # 3.4 Other Income children (Level 2)
    ("3.4.1", "Miscellaneous Income", "INCOME", "3.4", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.4.2", "Scrap Sale Income", "INCOME", "3.4", "INCOME", False, "NONE", False, False, False, False, False),
    ("3.4.3", "Donation Received", "INCOME", "3.4", "INCOME", False, "NONE", False, False, False, False, False),

    # ========================================================================
    # 4. EXPENSES (Level 1 children)
    # ========================================================================
    ("4.1", "Administrative", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.2", "Utilities", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3", "Maintenance", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.4", "Staff", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.5", "Security & Cleaning", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.6", "Financial", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.7", "Compliance & Tax", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.8", "Other", "EXPENSE", "4", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.1 Administrative children (Level 2)
    ("4.1.1", "Audit Fees", "EXPENSE", "4.1", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.1.2", "Printing & Stationery", "EXPENSE", "4.1", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.1.3", "Software Expense", "EXPENSE", "4.1", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.1.4", "Legal Fees", "EXPENSE", "4.1", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.1.5", "Office Expenses", "EXPENSE", "4.1", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.2 Utilities children (Level 2)
    ("4.2.1", "Electricity Expense", "EXPENSE", "4.2", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.2.2", "Water Expense", "EXPENSE", "4.2", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.2.3", "Internet Charges", "EXPENSE", "4.2", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.2.4", "Gas Charges", "EXPENSE", "4.2", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.3 Maintenance children (Level 2)
    ("4.3.1", "Civil Repairs", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3.2", "Plumbing Repairs", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3.3", "Lift Maintenance", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3.4", "Generator Maintenance", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3.5", "Electrical Repairs", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.3.6", "Garden Maintenance", "EXPENSE", "4.3", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.4 Staff children (Level 2)
    ("4.4.1", "Salary Expense", "EXPENSE", "4.4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.4.2", "Bonus", "EXPENSE", "4.4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.4.3", "Staff Welfare", "EXPENSE", "4.4", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.4.4", "Uniform Expense", "EXPENSE", "4.4", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.5 Security & Cleaning children (Level 2)
    ("4.5.1", "Security Charges", "EXPENSE", "4.5", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.5.2", "Housekeeping Charges", "EXPENSE", "4.5", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.5.3", "Pest Control", "EXPENSE", "4.5", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.6 Financial children (Level 2)
    ("4.6.1", "Bank Charges", "EXPENSE", "4.6", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.6.2", "Depreciation Expense", "EXPENSE", "4.6", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.6.3", "Interest Expense", "EXPENSE", "4.6", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.7 Compliance & Tax children (Level 2)
    ("4.7.1", "GST Expense (non-creditable)", "EXPENSE", "4.7", "GST", False, "NONE", False, False, False, False, False),
    ("4.7.2", "Penalty & Late Fees", "EXPENSE", "4.7", "EXPENSE", False, "NONE", False, False, False, False, False),

    # 4.8 Other children (Level 2)
    ("4.8.1", "Miscellaneous Expense", "EXPENSE", "4.8", "EXPENSE", False, "NONE", False, False, False, False, False),
    ("4.8.2", "Rounding Off Account", "EXPENSE", "4.8", "EXPENSE", False, "NONE", False, False, False, False, False),

    # ========================================================================
    # 5. EQUITY / FUNDS (Level 1 children)
    # ========================================================================
    ("5.1", "Member Funds", "EQUITY", "5", "FUND", False, "NONE", False, False, False, False, False),
    ("5.2", "Capital & Reserves", "EQUITY", "5", "FUND", False, "NONE", False, False, False, False, False),
    ("5.3", "Retained Earnings", "EQUITY", "5", "FUND", False, "NONE", False, False, False, False, False),

    # 5.1 Member Funds children (Level 2)
    ("5.1.1", "Sinking Fund", "EQUITY", "5.1", "FUND", False, "NONE", False, False, False, False, False),
    ("5.1.2", "Repair & Maintenance Fund", "EQUITY", "5.1", "FUND", False, "NONE", False, False, False, False, False),
    ("5.1.3", "Parking Fund", "EQUITY", "5.1", "FUND", False, "NONE", False, False, False, False, False),

    # 5.2 Capital & Reserves children (Level 2)
    ("5.2.1", "Share Capital", "EQUITY", "5.2", "FUND", False, "NONE", False, False, False, False, False),
    ("5.2.2", "General Reserve", "EQUITY", "5.2", "FUND", False, "NONE", False, False, False, False, False),
    ("5.2.3", "Opening Balance Fund", "EQUITY", "5.2", "FUND", False, "NONE", False, False, False, False, False),

    # 5.3 Retained Earnings children (Level 2)
    ("5.3.1", "Surplus / Deficit", "EQUITY", "5.3", "FUND", False, "NONE", False, False, False, False, False),
)


# ============================================================================
# DERIVED STRUCTURES (single source of truth: _ACCOUNT_TREE_ROWS)
# ============================================================================
NEW_ACCOUNT_TREE: tuple[AccountNode, ...] = tuple(
    AccountNode(*row) for row in _ACCOUNT_TREE_ROWS
)
ACCOUNT_NODES_BY_CODE: dict[str, AccountNode] = {
    node.code: node for node in NEW_ACCOUNT_TREE
}
_GROUP_CODES: frozenset[str] = frozenset(
    node.parent_code for node in NEW_ACCOUNT_TREE if node.parent_code
)
POSTING_CODES: frozenset[str] = frozenset(
    node.code for node in NEW_ACCOUNT_TREE if node.code not in _GROUP_CODES
)

DEFAULT_CATEGORY_DEFINITIONS = [
    (node.name, node.account_type) for node in NEW_ACCOUNT_TREE if node.is_root
]

GROUP_ACCOUNT_DEFINITIONS = [
    (node.code, node.name, node.account_type, node.parent_code)
    for node in NEW_ACCOUNT_TREE
    if node.is_group
]

DEFAULT_ACCOUNT_DEFINITIONS = [
    (node.code, node.name, node.account_type)
    for node in NEW_ACCOUNT_TREE
    if not node.is_group
]

LEAF_PARENT_MAP = {
    node.name: ACCOUNT_NODES_BY_CODE[node.parent_code].name
    for node in NEW_ACCOUNT_TREE
    if node.parent_code is not None and not node.is_group
}


def validate_account_tree(nodes: tuple[AccountNode, ...] = NEW_ACCOUNT_TREE) -> None:
    """Raise ``ValueError`` if the tree violates the ``Account`` model contract.

    This mirrors the rules enforced by ``Account.clean()`` so that a bad tree
    fails at import time instead of while creating a society.
    """
    account_types = set(Account.AccountType.values)
    sub_types = set(Account.SubType.values)
    gst_types = set(Account.GstType.values)

    by_code: dict[str, AccountNode] = {}
    by_name: dict[str, AccountNode] = {}

    for node in nodes:
        where = f"{node.code} ({node.name})"
        if node.code in by_code:
            raise ValueError(f"Duplicate account code: {where}")
        if node.name in by_name:
            raise ValueError(
                f"Duplicate account name '{node.name}': "
                f"{by_name[node.name].code} and {node.code}"
            )
        if not Account.CODE_PATTERN.match(node.code):
            raise ValueError(
                f"Invalid account code '{node.code}': expected '1', '1.1', '1.1.1', ..."
            )
        if node.account_type not in account_types:
            raise ValueError(f"Invalid account type for {where}: {node.account_type}")
        if node.sub_type not in sub_types:
            raise ValueError(f"Invalid sub type for {where}: {node.sub_type}")
        if node.gst_type not in gst_types:
            raise ValueError(f"Invalid GST type for {where}: {node.gst_type}")
        if node.parent_code is None and "." in node.code:
            raise ValueError(f"Root account {where} must not contain dots in its code")
        if node.parent_code is not None and "." not in node.code:
            raise ValueError(f"Child account {where} must use a dotted code")

        if not node.is_gst and node.gst_type != Account.GstType.NONE:
            raise ValueError(f"Non-GST account {where} must use gst_type NONE")
        if node.is_gst and node.gst_type == Account.GstType.NONE:
            raise ValueError(f"GST account {where} must be INPUT or OUTPUT")
        if node.is_gst and node.account_type not in {
            Account.AccountType.ASSET,
            Account.AccountType.LIABILITY,
        }:
            raise ValueError(f"GST account {where} must be an Asset or Liability")
        if node.is_clearing and node.account_type not in {
            Account.AccountType.ASSET,
            Account.AccountType.LIABILITY,
        }:
            raise ValueError(f"Clearing account {where} must be an Asset or Liability")

        by_code[node.code] = node
        by_name[node.name] = node

    for node in nodes:
        if node.parent_code is None:
            continue
        parent = by_code.get(node.parent_code)
        if parent is None:
            raise ValueError(
                f"Account {node.code} ({node.name}) references missing parent "
                f"{node.parent_code}"
            )
        if not node.code.startswith(f"{parent.code}."):
            raise ValueError(
                f"Account code '{node.code}' must start with parent code "
                f"'{parent.code}.'"
            )
        if node.account_type != parent.account_type:
            raise ValueError(
                f"Account {node.code} ({node.account_type}) has parent "
                f"{parent.code} ({parent.account_type}) with a different type"
            )


validate_account_tree()


# ============================================================================
# HELPERS
# ============================================================================

def ensure_standard_categories(society):
    """Create the root-level categories for one society (idempotent)."""
    for name, account_type in DEFAULT_CATEGORY_DEFINITIONS:
        AccountCategory.objects.get_or_create(
            society=society,
            name=name,
            account_type=account_type,
        )


def create_default_accounts_for_society(society):
    """Create/update the mandatory accounts for one society (idempotent).

    Parents are always processed before children. Group accounts are stored
    with ``is_posting=False`` explicitly instead of relying on the side effect
    of creating their first child.
    """
    ensure_standard_categories(society)

    category_by_key = {
        (category.name, category.account_type): category
        for category in AccountCategory.objects.filter(
            society=society,
            name__in=[name for name, _ in DEFAULT_CATEGORY_DEFINITIONS],
        )
    }

    existing_by_code = {
        account.code: account
        for account in Account.objects.filter(society=society, code__isnull=False)
    }

    created: dict[str, Account] = {}

    for node in sorted(NEW_ACCOUNT_TREE, key=lambda item: item.sort_key):
        parent = created.get(node.parent_code) if node.parent_code else None
        root = ACCOUNT_NODES_BY_CODE[node.root_code]

        values = {
            "name": node.name,
            "code": node.code,
            "account_type": node.account_type,
            "category": category_by_key[(root.name, root.account_type)],
            "parent": parent,
            "system_protected": True,
            "is_active": True,
            "is_posting": node.is_posting,
            "sub_type": node.sub_type,
            "is_gst": node.is_gst,
            "gst_type": node.gst_type,
            "is_bank": node.is_bank,
            "is_contra": node.is_contra,
            "is_clearing": node.is_clearing,
            "is_member_related": node.is_member_related,
            "is_vendor_related": node.is_vendor_related,
        }

        account = existing_by_code.get(node.code)
        if account is None:
            account = Account.objects.create(society=society, **values)
        else:
            for field, value in values.items():
                setattr(account, field, value)
            account.save()

        created[node.code] = account

    return created


def rebuild_accounts_for_society(society):
    """Delete every account for a society and recreate it from the tree.

    WARNING: this also deletes the society's ledger entries (accounts cascade).
    Only use it when the society has no transactions, or after taking a backup.
    """
    Account.objects.filter(society=society).delete()
    return create_default_accounts_for_society(society)


def ensure_standard_accounts(society):
    """Backward-compatible alias for ``create_default_accounts_for_society``."""
    create_default_accounts_for_society(society)

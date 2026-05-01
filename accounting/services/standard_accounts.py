from accounting.models import Account
from accounting.models import AccountCategory


# ============================================================================
# NEW ACCOUNT TREE STRUCTURE
# ============================================================================
# Complete hierarchical account structure with codes
# Format: (code, name, account_type, parent_code, sub_type, is_gst, gst_type, is_bank, is_contra, is_clearing, is_member_related, is_vendor_related)
#
# account_type: ASSET, LIABILITY, INCOME, EXPENSE, EQUITY
# sub_type: GST, BANK, MEMBER, FUND, EXPENSE, INCOME, GENERAL
# gst_type: INPUT, OUTPUT, NONE
# ============================================================================

NEW_ACCOUNT_TREE = [
    # Root nodes (Level 0)
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
    ("3.4.1", "Other Income", "INCOME", "3.4", "INCOME", False, "NONE", False, False, False, False, False),
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
]


# ============================================================================
# ROOT-LEVEL CATEGORIES (used for AccountCategory)
# ============================================================================
DEFAULT_CATEGORY_DEFINITIONS = [
    ("Assets", "ASSET"),
    ("Liabilities", "LIABILITY"),
    ("Income", "INCOME"),
    ("Expenses", "EXPENSE"),
    ("Equity / Funds", "EQUITY"),
]


# ============================================================================
# GROUP ACCOUNTS (intermediate nodes in the tree)
# ============================================================================
GROUP_ACCOUNT_DEFINITIONS = [
    # 1. Assets groups
    ("1", "Assets", "ASSET", None),
    ("1.1", "Fixed Assets", "ASSET", "1"),
    ("1.2", "Deposits", "ASSET", "1"),
    ("1.3", "Investments", "ASSET", "1"),
    ("1.4", "Bank & Cash", "ASSET", "1"),
    ("1.4.2", "Bank Accounts", "ASSET", "1.4"),
    ("1.5", "Receivables", "ASSET", "1"),
    ("1.5.1", "Member Receivable", "ASSET", "1.5"),
    ("1.6", "Advances", "ASSET", "1"),
    ("1.7", "GST Input", "ASSET", "1"),
    ("1.8", "Other Assets", "ASSET", "1"),
    # 2. Liabilities groups
    ("2", "Liabilities", "LIABILITY", None),
    ("2.1", "Member Liabilities", "LIABILITY", "2"),
    ("2.2", "Vendor & Expense Payables", "LIABILITY", "2"),
    ("2.3", "Statutory Liabilities", "LIABILITY", "2"),
    ("2.3.1", "GST Payable", "LIABILITY", "2.3"),
    ("2.4", "Bank & Clearing", "LIABILITY", "2"),
    ("2.5", "Provisions", "LIABILITY", "2"),
    ("2.6", "Other Liabilities", "LIABILITY", "2"),
    # 3. Income groups
    ("3", "Income", "INCOME", None),
    ("3.1", "Member Income", "INCOME", "3"),
    ("3.2", "Financial Income", "INCOME", "3"),
    ("3.3", "Commercial Income", "INCOME", "3"),
    ("3.4", "Other Income", "INCOME", "3"),
    # 4. Expenses groups
    ("4", "Expenses", "EXPENSE", None),
    ("4.1", "Administrative", "EXPENSE", "4"),
    ("4.2", "Utilities", "EXPENSE", "4"),
    ("4.3", "Maintenance", "EXPENSE", "4"),
    ("4.4", "Staff", "EXPENSE", "4"),
    ("4.5", "Security & Cleaning", "EXPENSE", "4"),
    ("4.6", "Financial", "EXPENSE", "4"),
    ("4.7", "Compliance & Tax", "EXPENSE", "4"),
    ("4.8", "Other", "EXPENSE", "4"),
    # 5. Equity groups
    ("5", "Equity / Funds", "EQUITY", None),
    ("5.1", "Member Funds", "EQUITY", "5"),
    ("5.2", "Capital & Reserves", "EQUITY", "5"),
    ("5.3", "Retained Earnings", "EQUITY", "5"),
]


# ============================================================================
# LEAF ACCOUNTS (accounts with no children)
# ============================================================================
DEFAULT_ACCOUNT_DEFINITIONS = [
    # 1.1 Fixed Assets leaves
    ("1.1.1", "Building Structure", "ASSET"),
    ("1.1.2", "Lift & Elevator", "ASSET"),
    ("1.1.3", "Generator / DG Set", "ASSET"),
    ("1.1.4", "Water Pump & Motor", "ASSET"),
    ("1.1.5", "CCTV System", "ASSET"),
    ("1.1.6", "Furniture & Fixtures", "ASSET"),
    ("1.1.7", "Office Equipment", "ASSET"),
    ("1.1.8", "Electrical Installations", "ASSET"),
    # 1.2 Deposits leaves
    ("1.2.1", "Electricity Deposit", "ASSET"),
    ("1.2.2", "Water Deposit", "ASSET"),
    ("1.2.3", "Security Deposit Given", "ASSET"),
    ("1.2.4", "Other Utility Deposits", "ASSET"),
    # 1.3 Investments leaves
    ("1.3.1", "Fixed Deposits (FD)", "ASSET"),
    ("1.3.2", "Sinking Fund Investment", "ASSET"),
    ("1.3.3", "Repair Fund Investment", "ASSET"),
    ("1.3.4", "Reserve Fund Investment", "ASSET"),
    # 1.4 Bank & Cash leaves
    ("1.4.1", "Cash-in-Hand", "ASSET"),
    ("1.4.2.1", "Bank – Maintenance Account", "ASSET"),
    ("1.4.2.2", "Bank – Sinking Fund", "ASSET"),
    ("1.4.2.3", "Bank – Repair Fund", "ASSET"),
    ("1.4.2.4", "Bank – Parking Fund", "ASSET"),
    ("1.4.3", "Fund Transfer Account", "ASSET"),
    # 1.5 Receivables leaves
    ("1.5.1.1", "Maintenance Due", "ASSET"),
    ("1.5.1.2", "Parking Due", "ASSET"),
    ("1.5.1.3", "Interest on Arrears", "ASSET"),
    ("1.5.1.4", "Other Member Dues", "ASSET"),
    ("1.5.2", "Vendor Receivable", "ASSET"),
    ("1.5.3", "Interest Receivable – Bank", "ASSET"),
    # 1.6 Advances leaves
    ("1.6.1", "Vendor Advance", "ASSET"),
    ("1.6.2", "Staff Advance", "ASSET"),
    ("1.6.3", "Prepaid Expenses", "ASSET"),
    # 1.7 GST Input leaves
    ("1.7.1", "Input CGST", "ASSET"),
    ("1.7.2", "Input SGST", "ASSET"),
    ("1.7.3", "Input IGST", "ASSET"),
    # 1.8 Other Assets leaves
    ("1.8.1", "Accrued Income", "ASSET"),
    ("1.8.2", "Suspense (Debit)", "ASSET"),
    # 2.1 Member Liabilities leaves
    ("2.1.1", "Advance Maintenance", "LIABILITY"),
    ("2.1.2", "Member Advance", "LIABILITY"),
    ("2.1.3", "Member Refund Payable", "LIABILITY"),
    ("2.1.4", "Security Deposit – Members", "LIABILITY"),
    # 2.2 Vendor & Expense Payables leaves
    ("2.2.1", "Vendor Payable", "LIABILITY"),
    ("2.2.2", "Expense Payable", "LIABILITY"),
    ("2.2.3", "Audit Fees Payable", "LIABILITY"),
    # 2.3 Statutory Liabilities leaves
    ("2.3.1.1", "Output CGST", "LIABILITY"),
    ("2.3.1.2", "Output SGST", "LIABILITY"),
    ("2.3.1.3", "Output IGST", "LIABILITY"),
    ("2.3.2", "TDS Payable", "LIABILITY"),
    ("2.3.3", "Professional Tax Payable", "LIABILITY"),
    ("2.3.4", "GST TDS / Reverse Charge", "LIABILITY"),
    # 2.4 Bank & Clearing leaves
    ("2.4.1", "Cheque Issued but Not Cleared", "LIABILITY"),
    ("2.4.2", "Cheque Deposited but Not Cleared", "LIABILITY"),
    ("2.4.3", "Payment Gateway Clearing", "LIABILITY"),
    # 2.5 Provisions leaves
    ("2.5.1", "Provision for Expenses", "LIABILITY"),
    ("2.5.2", "Provision for Audit", "LIABILITY"),
    # 2.6 Other Liabilities leaves
    ("2.6.1", "Suspense Account", "LIABILITY"),
    # 3.1 Member Income leaves
    ("3.1.1", "Maintenance Charges", "INCOME"),
    ("3.1.2", "Service Charges", "INCOME"),
    ("3.1.3", "Parking Charges", "INCOME"),
    ("3.1.4", "Transfer Fees", "INCOME"),
    ("3.1.5", "Non-Occupancy Charges", "INCOME"),
    ("3.1.6", "Late Payment Penalty", "INCOME"),
    ("3.1.7", "Interest Income – Member", "INCOME"),
    # 3.2 Financial Income leaves
    ("3.2.1", "Interest Income – Bank", "INCOME"),
    ("3.2.2", "Interest on FD", "INCOME"),
    # 3.3 Commercial Income leaves
    ("3.3.1", "Rental Income – Common Area", "INCOME"),
    ("3.3.2", "Advertisement Income", "INCOME"),
    ("3.3.3", "Mobile Tower Income", "INCOME"),
    # 3.4 Other Income leaves
    ("3.4.1", "Other Income", "INCOME"),
    ("3.4.2", "Scrap Sale Income", "INCOME"),
    ("3.4.3", "Donation Received", "INCOME"),
    # 4.1 Administrative leaves
    ("4.1.1", "Audit Fees", "EXPENSE"),
    ("4.1.2", "Printing & Stationery", "EXPENSE"),
    ("4.1.3", "Software Expense", "EXPENSE"),
    ("4.1.4", "Legal Fees", "EXPENSE"),
    ("4.1.5", "Office Expenses", "EXPENSE"),
    # 4.2 Utilities leaves
    ("4.2.1", "Electricity Expense", "EXPENSE"),
    ("4.2.2", "Water Expense", "EXPENSE"),
    ("4.2.3", "Internet Charges", "EXPENSE"),
    ("4.2.4", "Gas Charges", "EXPENSE"),
    # 4.3 Maintenance leaves
    ("4.3.1", "Civil Repairs", "EXPENSE"),
    ("4.3.2", "Plumbing Repairs", "EXPENSE"),
    ("4.3.3", "Lift Maintenance", "EXPENSE"),
    ("4.3.4", "Generator Maintenance", "EXPENSE"),
    ("4.3.5", "Electrical Repairs", "EXPENSE"),
    ("4.3.6", "Garden Maintenance", "EXPENSE"),
    # 4.4 Staff leaves
    ("4.4.1", "Salary Expense", "EXPENSE"),
    ("4.4.2", "Bonus", "EXPENSE"),
    ("4.4.3", "Staff Welfare", "EXPENSE"),
    ("4.4.4", "Uniform Expense", "EXPENSE"),
    # 4.5 Security & Cleaning leaves
    ("4.5.1", "Security Charges", "EXPENSE"),
    ("4.5.2", "Housekeeping Charges", "EXPENSE"),
    ("4.5.3", "Pest Control", "EXPENSE"),
    # 4.6 Financial leaves
    ("4.6.1", "Bank Charges", "EXPENSE"),
    ("4.6.2", "Depreciation Expense", "EXPENSE"),
    ("4.6.3", "Interest Expense", "EXPENSE"),
    # 4.7 Compliance & Tax leaves
    ("4.7.1", "GST Expense (non-creditable)", "EXPENSE"),
    ("4.7.2", "Penalty & Late Fees", "EXPENSE"),
    # 4.8 Other leaves
    ("4.8.1", "Miscellaneous Expense", "EXPENSE"),
    ("4.8.2", "Rounding Off Account", "EXPENSE"),
    # 5.1 Member Funds leaves
    ("5.1.1", "Sinking Fund", "EQUITY"),
    ("5.1.2", "Repair & Maintenance Fund", "EQUITY"),
    ("5.1.3", "Parking Fund", "EQUITY"),
    # 5.2 Capital & Reserves leaves
    ("5.2.1", "Share Capital", "EQUITY"),
    ("5.2.2", "General Reserve", "EQUITY"),
    ("5.2.3", "Opening Balance Fund", "EQUITY"),
    # 5.3 Retained Earnings leaves
    ("5.3.1", "Surplus / Deficit", "EQUITY"),
]


# ============================================================================
# LEAF PARENT MAP (for backward compatibility)
# Maps leaf account names to their immediate parent account name
# ============================================================================
LEAF_PARENT_MAP = {
    "Building Structure": "Fixed Assets",
    "Lift & Elevator": "Fixed Assets",
    "Generator / DG Set": "Fixed Assets",
    "Water Pump & Motor": "Fixed Assets",
    "CCTV System": "Fixed Assets",
    "Furniture & Fixtures": "Fixed Assets",
    "Office Equipment": "Fixed Assets",
    "Electrical Installations": "Fixed Assets",
    "Electricity Deposit": "Deposits",
    "Water Deposit": "Deposits",
    "Security Deposit Given": "Deposits",
    "Other Utility Deposits": "Deposits",
    "Fixed Deposits (FD)": "Investments",
    "Sinking Fund Investment": "Investments",
    "Repair Fund Investment": "Investments",
    "Reserve Fund Investment": "Investments",
    "Cash-in-Hand": "Bank & Cash",
    "Bank – Maintenance Account": "Bank Accounts",
    "Bank – Sinking Fund": "Bank Accounts",
    "Bank – Repair Fund": "Bank Accounts",
    "Bank – Parking Fund": "Bank Accounts",
    "Fund Transfer Account": "Bank & Cash",
    "Maintenance Due": "Member Receivable",
    "Parking Due": "Member Receivable",
    "Interest on Arrears": "Member Receivable",
    "Other Member Dues": "Member Receivable",
    "Vendor Receivable": "Receivables",
    "Interest Receivable – Bank": "Receivables",
    "Vendor Advance": "Advances",
    "Staff Advance": "Advances",
    "Prepaid Expenses": "Advances",
    "Input CGST": "GST Input",
    "Input SGST": "GST Input",
    "Input IGST": "GST Input",
    "Accrued Income": "Other Assets",
    "Suspense (Debit)": "Other Assets",
    "Advance Maintenance": "Member Liabilities",
    "Member Advance": "Member Liabilities",
    "Member Refund Payable": "Member Liabilities",
    "Security Deposit – Members": "Member Liabilities",
    "Vendor Payable": "Vendor & Expense Payables",
    "Expense Payable": "Vendor & Expense Payables",
    "Audit Fees Payable": "Vendor & Expense Payables",
    "Output CGST": "GST Payable",
    "Output SGST": "GST Payable",
    "Output IGST": "GST Payable",
    "TDS Payable": "Statutory Liabilities",
    "Professional Tax Payable": "Statutory Liabilities",
    "GST TDS / Reverse Charge": "Statutory Liabilities",
    "Cheque Issued but Not Cleared": "Bank & Clearing",
    "Cheque Deposited but Not Cleared": "Bank & Clearing",
    "Payment Gateway Clearing": "Bank & Clearing",
    "Provision for Expenses": "Provisions",
    "Provision for Audit": "Provisions",
    "Suspense Account": "Other Liabilities",
    "Maintenance Charges": "Member Income",
    "Service Charges": "Member Income",
    "Parking Charges": "Member Income",
    "Transfer Fees": "Member Income",
    "Non-Occupancy Charges": "Member Income",
    "Late Payment Penalty": "Member Income",
    "Interest Income – Member": "Member Income",
    "Interest Income – Bank": "Financial Income",
    "Interest on FD": "Financial Income",
    "Rental Income – Common Area": "Commercial Income",
    "Advertisement Income": "Commercial Income",
    "Mobile Tower Income": "Commercial Income",
    "Other Income": "Other Income",
    "Scrap Sale Income": "Other Income",
    "Donation Received": "Other Income",
    "Audit Fees": "Administrative",
    "Printing & Stationery": "Administrative",
    "Software Expense": "Administrative",
    "Legal Fees": "Administrative",
    "Office Expenses": "Administrative",
    "Electricity Expense": "Utilities",
    "Water Expense": "Utilities",
    "Internet Charges": "Utilities",
    "Gas Charges": "Utilities",
    "Civil Repairs": "Maintenance",
    "Plumbing Repairs": "Maintenance",
    "Lift Maintenance": "Maintenance",
    "Generator Maintenance": "Maintenance",
    "Electrical Repairs": "Maintenance",
    "Garden Maintenance": "Maintenance",
    "Salary Expense": "Staff",
    "Bonus": "Staff",
    "Staff Welfare": "Staff",
    "Uniform Expense": "Staff",
    "Security Charges": "Security & Cleaning",
    "Housekeeping Charges": "Security & Cleaning",
    "Pest Control": "Security & Cleaning",
    "Bank Charges": "Financial",
    "Depreciation Expense": "Financial",
    "Interest Expense": "Financial",
    "GST Expense (non-creditable)": "Compliance & Tax",
    "Penalty & Late Fees": "Compliance & Tax",
    "Miscellaneous Expense": "Other",
    "Rounding Off Account": "Other",
    "Sinking Fund": "Member Funds",
    "Repair & Maintenance Fund": "Member Funds",
    "Parking Fund": "Member Funds",
    "Share Capital": "Capital & Reserves",
    "General Reserve": "Capital & Reserves",
    "Opening Balance Fund": "Capital & Reserves",
    "Surplus / Deficit": "Retained Earnings",
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def derive_account_metadata(code: str, name: str, account_type: str):
    """
    Derive account metadata based on code prefix and name.
    Uses code prefix to determine metadata for better reliability.
    """
    metadata = {
        "sub_type": "GENERAL",
        "is_gst": False,
        "gst_type": "NONE",
        "is_bank": False,
        "is_member_related": False,
        "is_vendor_related": False,
        "is_contra": False,
        "is_clearing": False,
    }

    # Determine metadata based on code prefix (most reliable)
    if code.startswith("1.7") or "input" in name.lower():
        metadata["sub_type"] = "GST"
        metadata["is_gst"] = True
        metadata["gst_type"] = "INPUT"
    elif code.startswith("2.3.1") or "output" in name.lower():
        metadata["sub_type"] = "GST"
        metadata["is_gst"] = True
        metadata["gst_type"] = "OUTPUT"
    elif code.startswith("1.4") or "bank" in name.lower() or "cash" in name.lower():
        metadata["sub_type"] = "BANK"
        metadata["is_bank"] = True
    elif code.startswith("1.5") or "member" in name.lower():
        metadata["sub_type"] = "MEMBER"
        metadata["is_member_related"] = True
    elif code.startswith("1.6") or "vendor" in name.lower():
        metadata["is_vendor_related"] = True
    elif code.startswith("1.3") or code.startswith("5."):
        metadata["sub_type"] = "FUND"
    elif "fund transfer" in name.lower():
        metadata["sub_type"] = "BANK"
        metadata["is_bank"] = True
        metadata["is_contra"] = True
    elif "clearing" in name.lower() or "cheque" in name.lower():
        metadata["is_clearing"] = True

    # Override sub_type based on account_type
    if account_type == "EXPENSE":
        metadata["sub_type"] = "EXPENSE"
    elif account_type == "INCOME":
        metadata["sub_type"] = "INCOME"

    return metadata


def ensure_standard_categories(society):
    """
    Create required root-level categories for one society (idempotent).
    """
    for name, account_type in DEFAULT_CATEGORY_DEFINITIONS:
        AccountCategory.objects.get_or_create(
            society=society,
            name=name,
            account_type=account_type,
        )


def _get_account_by_code(society, code):
    """Get account by code for a society."""
    return Account.objects.filter(society=society, code=code).first()


def _get_account_by_name(society, name):
    """Get account by name for a society."""
    return Account.objects.filter(society=society, name=name).order_by("id").first()


def create_default_accounts_for_society(society):
    """
    Create mandatory accounts for ONE society using the new tree structure.
    Idempotent - safe to run multiple times.
    """
    # Ensure categories exist
    ensure_standard_categories(society)

    # Build a map of created accounts by code for parent lookups
    created_accounts = {}

    # Sort tree by code to ensure parents are created before children
    sorted_tree = sorted(NEW_ACCOUNT_TREE, key=lambda x: x[0])

    for code, name, account_type, parent_code, sub_type, is_gst, gst_type, is_bank, is_contra, is_clearing, is_member_related, is_vendor_related in sorted_tree:
        # Get parent account if applicable
        parent = created_accounts.get(parent_code) if parent_code else None

        # Get the root-level category
        root_code = code.split(".")[0]
        category_name = {"1": "Assets", "2": "Liabilities", "3": "Income", "4": "Expenses", "5": "Equity / Funds"}[root_code]
        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=account_type,
        )

        # Check if account already exists (by code)
        existing = _get_account_by_code(society, code)

        if existing:
            # Update existing account
            existing.name = name
            existing.code = code
            existing.account_type = account_type
            existing.category = category
            existing.parent = parent
            existing.system_protected = True
            existing.is_active = True
            existing.sub_type = sub_type
            existing.is_gst = is_gst
            existing.gst_type = gst_type
            existing.is_bank = is_bank
            existing.is_contra = is_contra
            existing.is_clearing = is_clearing
            existing.is_member_related = is_member_related
            existing.is_vendor_related = is_vendor_related
            existing.save()
            created_accounts[code] = existing
        else:
            # Create new account
            account = Account.objects.create(
                society=society,
                name=name,
                code=code,
                account_type=account_type,
                category=category,
                parent=parent,
                system_protected=True,
                is_active=True,
                sub_type=sub_type,
                is_gst=is_gst,
                gst_type=gst_type,
                is_bank=is_bank,
                is_contra=is_contra,
                is_clearing=is_clearing,
                is_member_related=is_member_related,
                is_vendor_related=is_vendor_related,
            )
            created_accounts[code] = account

    return created_accounts


def rebuild_accounts_for_society(society):
    """
    Delete ALL existing accounts for a society and recreate them from NEW_ACCOUNT_TREE.
    WARNING: This will delete all accounts and their associated ledger entries.
    Use with caution in production - ensure you have backups and no transactions exist.
    """
    # Delete all existing accounts for this society
    Account.objects.filter(society=society).delete()

    # Recreate accounts from scratch
    return create_default_accounts_for_society(society)


def ensure_standard_accounts(society):
    """
    Backward-compatible helper: create defaults for one society.
    """
    create_default_accounts_for_society(society)

from django.urls import path

from reports.views import accounts_payable_aging_report_view
from reports.views import accounts_receivable_aging_report_view
from reports.views import advanced_regulatory_reports_view
from reports.views import balance_sheet_report_view
from reports.views import bank_reconciliation_statement_view
from reports.views import cash_flow_statement_view
from reports.views import control_risk_reports_view
from reports.views import exception_report_view
from reports.views import fixed_assets_register_view
from reports.views import gst_reports_view
from reports.views import inventory_costing_reports_view
from reports.views import management_analytics_reports_view
from reports.views import profit_and_loss_report_view
from reports.views import reports_home_view
from reports.views import tds_reports_view
from reports.views import trial_balance_export_csv_view
from reports.views import trial_balance_report_view
from reports.views import transaction_reconciliation_view

app_name = "reports"

urlpatterns = [
    path("", view=reports_home_view, name="index"),
    path("trial-balance/", view=trial_balance_report_view, name="trial-balance"),
    path(
        "trial-balance/export.csv/",
        view=trial_balance_export_csv_view,
        name="trial-balance-export-csv",
    ),
    path("profit-and-loss/", view=profit_and_loss_report_view, name="profit-and-loss"),
    path("balance-sheet/", view=balance_sheet_report_view, name="balance-sheet"),
    path(
        "cash-flow-statement/",
        view=cash_flow_statement_view,
        name="cash-flow-statement",
    ),
    path(
        "fixed-assets-register/",
        view=fixed_assets_register_view,
        name="fixed-assets-register",
    ),
    path(
        "accounts-receivable-aging/",
        view=accounts_receivable_aging_report_view,
        name="accounts-receivable-aging",
    ),
    path(
        "accounts-payable-aging/",
        view=accounts_payable_aging_report_view,
        name="accounts-payable-aging",
    ),
    path(
        "bank-reconciliation-statement/",
        view=bank_reconciliation_statement_view,
        name="bank-reconciliation-statement",
    ),
    path(
        "transaction-reconciliation/",
        view=transaction_reconciliation_view,
        name="transaction-reconciliation",
    ),
    path("exceptions/", view=exception_report_view, name="exception-report"),
    path("gst-reports/", view=gst_reports_view, name="gst-reports"),
    path("tds-reports/", view=tds_reports_view, name="tds-reports"),
    path(
        "inventory-costing/",
        view=inventory_costing_reports_view,
        name="inventory-costing-reports",
    ),
    path(
        "management-analytics/",
        view=management_analytics_reports_view,
        name="management-analytics-reports",
    ),
    path(
        "control-risk/",
        view=control_risk_reports_view,
        name="control-risk-reports",
    ),
    path(
        "advanced-regulatory/",
        view=advanced_regulatory_reports_view,
        name="advanced-regulatory-reports",
    ),
]

from __future__ import annotations

import csv
from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.http import HttpResponse
from django.views import View
from django.views.generic import TemplateView

from accounting.services.reporting import build_trial_balance
from housing_accounting.selection import get_selected_scope
from reports.services import build_balance_sheet
from reports.services import build_bank_reconciliation_statement
from reports.services import build_cash_flow_statement
from reports.services import build_exception_report
from reports.services import build_advanced_regulatory_reports
from reports.services import build_control_risk_reports
from reports.services import build_fixed_assets_register
from reports.services import build_gst_reports
from reports.services import build_inventory_costing_reports
from reports.services import build_management_analytics_reports
from reports.services import build_payable_aging
from reports.services import build_profit_and_loss
from reports.services import build_receivable_aging
from reports.services import build_tds_reports
from reports.services import build_transaction_reconciliation


class ReportsHomeView(LoginRequiredMixin, TemplateView):
    template_name = "reports/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["report_links"] = [
            {"title": "General Ledger (GL)", "url_name": "accounting:account-list", "description": "Account-wise drill-down to ledger entries.", "status": "Live"},
            {"title": "Trial Balance", "url_name": "reports:trial-balance", "description": "Debit, credit, and net balances from posted vouchers.", "status": "Live"},
            {"title": "Profit and Loss", "url_name": "reports:profit-and-loss", "description": "Income, expenses, and net profit for the selected period.", "status": "Live"},
            {"title": "Balance Sheet", "url_name": "reports:balance-sheet", "description": "Assets, liabilities, equity, and current-period surplus.", "status": "Live"},
            {"title": "AR Aging", "url_name": "reports:accounts-receivable-aging", "description": "Outstanding member receivables by aging bucket.", "status": "Live"},
            {"title": "AP Aging", "url_name": "reports:accounts-payable-aging", "description": "Outstanding vendor payables by aging bucket.", "status": "Live"},
            {"title": "Exception Report", "url_name": "reports:exception-report", "description": "Duplicate references and suspense balance exceptions.", "status": "Live"},
            {"title": "Cash Flow Statement", "url_name": "reports:cash-flow-statement", "description": "Operating, investing, and financing cash movement.", "status": "Live"},
            {"title": "Fixed Assets Register", "url_name": "reports:fixed-assets-register", "description": "Asset-level balances, additions, and movements.", "status": "Live"},
            {"title": "Bank Reconciliation Statement (BRS)", "url_name": "reports:bank-reconciliation-statement", "description": "Bank balance vs books with break-up of differences.", "status": "Live"},
            {"title": "Transaction Reconciliation", "url_name": "reports:transaction-reconciliation", "description": "IC vs Switch vs Host matched/unmatched lifecycle.", "status": "Live"},
            {"title": "GST Reports", "url_name": "reports:gst-reports", "description": "GSTR-1, GSTR-3B, and Input Tax Credit views.", "status": "Live"},
            {"title": "TDS Reports", "url_name": "reports:tds-reports", "description": "TDS deducted, payable, and return-ready summaries.", "status": "Live"},
            {"title": "Inventory and Costing", "url_name": "reports:inventory-costing-reports", "description": "Inventory valuation, stock ledger, and costing analysis.", "status": "Live"},
            {"title": "Management Analytics", "url_name": "reports:management-analytics-reports", "description": "Budget vs actual, variance, profitability, and KPI dashboards.", "status": "Live"},
            {"title": "Control and Risk", "url_name": "reports:control-risk-reports", "description": "Audit trail, suspense monitoring, and anomaly tracking.", "status": "Live"},
            {"title": "Advanced Regulatory", "url_name": "reports:advanced-regulatory-reports", "description": "Regulatory, settlement, liquidity, and revenue-leakage outputs.", "status": "Live"},
        ]
        return context


def _parse_to_date(request):
    raw = request.GET.get("to_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class BaseReportView(LoginRequiredMixin, TemplateView):
    template_name = "reports/report_table.html"
    page_title = ""
    page_subtitle = ""
    allow_without_scope = False

    def get_selected_scope_or_empty(self):
        selected_society, selected_financial_year = get_selected_scope(self.request)
        to_date = _parse_to_date(self.request)
        return selected_society, selected_financial_year, to_date

    def build_report_context(self, *, society, financial_year, to_date):
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, selected_financial_year, to_date = self.get_selected_scope_or_empty()
        context["page_title"] = self.page_title
        context["page_subtitle"] = self.page_subtitle
        context["to_date"] = to_date
        context["selected_society"] = selected_society
        context["selected_financial_year"] = selected_financial_year
        context["statement_as_of_date"] = (
            to_date
            or (selected_financial_year.end_date if selected_financial_year else None)
        )
        context["report_data"] = None
        if selected_society is None and not self.allow_without_scope:
            return context
        context["report_data"] = self.build_report_context(
            society=selected_society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )
        return context


class TrialBalanceReportView(BaseReportView):
    page_title = "Trial Balance"
    page_subtitle = "Posted vouchers only. Deterministic and balanced by design."

    def build_report_context(self, *, society, financial_year, to_date):
        trial_balance = build_trial_balance(
            society=society,
            financial_year=financial_year,
            to_date=to_date,
        )
        return {
            "mode": "trial_balance",
            "trial_balance": trial_balance,
        }


class TrialBalanceExportCsvView(LoginRequiredMixin, View):
    def get(self, request):
        selected_society, selected_financial_year = get_selected_scope(request)
        if not selected_society:
            raise Http404("No selected society for trial balance export.")

        to_date = _parse_to_date(request)
        trial_balance = build_trial_balance(
            society=selected_society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="trial_balance_{selected_society.id}_{to_date or "all"}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Account",
                "Account Type",
                "Total Debit",
                "Total Credit",
                "Balance Debit",
                "Balance Credit",
            ]
        )
        for row in trial_balance["rows"]:
            writer.writerow(
                [
                    row["account_name"],
                    row["account_type"],
                    row["total_debit"],
                    row["total_credit"],
                    row["balance_debit"],
                    row["balance_credit"],
                ]
            )
        writer.writerow(
            [
                "TOTALS",
                "",
                trial_balance["grand_total_debit"],
                trial_balance["grand_total_credit"],
                trial_balance["total_balance_debit"],
                trial_balance["total_balance_credit"],
            ]
        )
        return response


class ProfitAndLossReportView(BaseReportView):
    page_title = "Profit and Loss"
    page_subtitle = "Income, expenses, and current-period profitability from posted vouchers."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "profit_and_loss",
            "profit_and_loss": build_profit_and_loss(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class BalanceSheetReportView(BaseReportView):
    page_title = "Balance Sheet"
    page_subtitle = "Assets, liabilities, equity, and current-period surplus from posted vouchers."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "balance_sheet",
            "balance_sheet": build_balance_sheet(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class CashFlowStatementReportView(BaseReportView):
    page_title = "Cash Flow Statement"
    page_subtitle = "Operating, investing, and financing cash movement from posted vouchers."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "cash_flow",
            "cash_flow": build_cash_flow_statement(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class BankReconciliationStatementReportView(BaseReportView):
    page_title = "Bank Reconciliation Statement (BRS)"
    page_subtitle = "Book balance vs adjusted bank balance with reconciling items."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "bank_reconciliation",
            "bank_reconciliation": build_bank_reconciliation_statement(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class FixedAssetsRegisterReportView(BaseReportView):
    page_title = "Fixed Assets Register"
    page_subtitle = "Asset-wise opening, additions, reductions, and closing balances."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "fixed_assets",
            "fixed_assets": build_fixed_assets_register(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class TransactionReconciliationReportView(BaseReportView):
    page_title = "Transaction Reconciliation"
    page_subtitle = "Reference-wise matched, unmatched, and exception lifecycle."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "transaction_reconciliation",
            "transaction_reconciliation": build_transaction_reconciliation(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class GstReportsView(BaseReportView):
    page_title = "GST Reports"
    page_subtitle = "GSTR-1, GSTR-3B, and Input Tax Credit summary from posted vouchers."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "gst",
            "gst": build_gst_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class TdsReportsView(BaseReportView):
    page_title = "TDS Reports"
    page_subtitle = "TDS deducted, adjusted, and payable summary from posted vouchers."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "tds",
            "tds": build_tds_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class InventoryCostingReportsView(BaseReportView):
    page_title = "Inventory and Costing Reports"
    page_subtitle = "Inventory valuation summary from configured stock/inventory accounts."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "inventory_costing",
            "inventory_costing": build_inventory_costing_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class ManagementAnalyticsReportsView(BaseReportView):
    page_title = "Management Analytics Reports"
    page_subtitle = "Revenue, expense, margin, and top-account analytics."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "management_analytics",
            "management_analytics": build_management_analytics_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class ControlRiskReportsView(BaseReportView):
    page_title = "Control and Risk Reports"
    page_subtitle = "Duplicate reference and suspense-risk monitoring."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "control_risk",
            "control_risk": build_control_risk_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class AdvancedRegulatoryReportsView(BaseReportView):
    page_title = "Advanced Regulatory Reports"
    page_subtitle = "Settlement, suspense, and operational compliance summary."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "advanced_regulatory",
            "advanced_regulatory": build_advanced_regulatory_reports(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class AccountsReceivableAgingReportView(BaseReportView):
    page_title = "Accounts Receivable Aging"
    page_subtitle = "Outstanding member receivables grouped into aging buckets."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "aging",
            "aging_title": "Receivables Aging",
            "aging": build_receivable_aging(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class AccountsPayableAgingReportView(BaseReportView):
    page_title = "Accounts Payable Aging"
    page_subtitle = "Outstanding vendor payables grouped into aging buckets."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "aging",
            "aging_title": "Payables Aging",
            "aging": build_payable_aging(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class ExceptionReportView(BaseReportView):
    page_title = "Exception Report"
    page_subtitle = "Duplicate references and suspense balances that need attention."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "exceptions",
            "exceptions": build_exception_report(
                society=society,
                financial_year=financial_year,
                to_date=to_date,
            ),
        }


class PlannedReportView(BaseReportView):
    allow_without_scope = True
    report_status = "Planned"
    report_phase = "Roadmap"
    report_scope = "As documented in financial reporting plan."

    def build_report_context(self, *, society, financial_year, to_date):
        return {
            "mode": "planned",
            "planned": {
                "status": self.report_status,
                "phase": self.report_phase,
                "scope": self.report_scope,
            },
        }


reports_home_view = ReportsHomeView.as_view()
trial_balance_report_view = TrialBalanceReportView.as_view()
trial_balance_export_csv_view = TrialBalanceExportCsvView.as_view()
profit_and_loss_report_view = ProfitAndLossReportView.as_view()
balance_sheet_report_view = BalanceSheetReportView.as_view()
cash_flow_statement_view = CashFlowStatementReportView.as_view()
bank_reconciliation_statement_view = BankReconciliationStatementReportView.as_view()
fixed_assets_register_view = FixedAssetsRegisterReportView.as_view()
transaction_reconciliation_view = TransactionReconciliationReportView.as_view()
gst_reports_view = GstReportsView.as_view()
tds_reports_view = TdsReportsView.as_view()
inventory_costing_reports_view = InventoryCostingReportsView.as_view()
management_analytics_reports_view = ManagementAnalyticsReportsView.as_view()
control_risk_reports_view = ControlRiskReportsView.as_view()
advanced_regulatory_reports_view = AdvancedRegulatoryReportsView.as_view()
accounts_receivable_aging_report_view = AccountsReceivableAgingReportView.as_view()
accounts_payable_aging_report_view = AccountsPayableAgingReportView.as_view()
exception_report_view = ExceptionReportView.as_view()

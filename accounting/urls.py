from django.urls import path

from accounting.views import account_list_view
from accounting.views import account_create_view
from accounting.views import account_update_view
from accounting.views import account_tree_view
from accounting.views import accounting_dashboard_view
from accounting.views import voucher_entry_view
from accounting.views import voucher_list_view
from accounting.views import voucher_detail_view
from accounting.views import voucher_delete_draft_view
from accounting.views import voucher_post_view
from accounting.views import voucher_posting_menu_view
from accounting.views import voucher_reverse_view
from accounting.views import account_ledger_view
from accounting.views import account_ledger_export_csv_view
from accounting.views import voucher_template_create_view
from accounting.views import voucher_template_delete_view
from accounting.views import voucher_template_list_view
from accounting.views import voucher_template_update_view
from reports.views import trial_balance_export_csv_view
from reports.views import trial_balance_report_view

app_name = "accounting"

urlpatterns = [
    path("", view=accounting_dashboard_view, name="dashboard"),
    path("accounts/", view=account_list_view, name="account-list"),
    path("accounts/add/", view=account_create_view, name="account-add"),
    path("accounts/tree/", view=account_tree_view, name="account-tree"),
    path("accounts/<int:pk>/edit/", view=account_update_view, name="account-edit"),
    path("accounts/<int:pk>/ledger/", view=account_ledger_view, name="account-ledger"),
    path(
        "accounts/<int:pk>/ledger/export.csv/",
        view=account_ledger_export_csv_view,
        name="account-ledger-export-csv",
    ),
    path("reports/trial-balance/", view=trial_balance_report_view, name="trial-balance"),
    path(
        "reports/trial-balance/export.csv/",
        view=trial_balance_export_csv_view,
        name="trial-balance-export-csv",
    ),
    path("vouchers/", view=voucher_list_view, name="voucher-list"),
    path("vouchers/entry/", view=voucher_entry_view, name="voucher-entry"),
    path("vouchers/templates/", view=voucher_template_list_view, name="voucher-template-list"),
    path("vouchers/templates/add/", view=voucher_template_create_view, name="voucher-template-add"),
    path("vouchers/templates/<int:pk>/edit/", view=voucher_template_update_view, name="voucher-template-edit"),
    path("vouchers/templates/<int:pk>/delete/", view=voucher_template_delete_view, name="voucher-template-delete"),
    path("vouchers/posting/", view=voucher_posting_menu_view, name="voucher-posting"),
    path("vouchers/<int:pk>/detail/", view=voucher_detail_view, name="voucher-detail"),
    path("vouchers/<int:pk>/post/", view=voucher_post_view, name="voucher-post"),
    path("vouchers/<int:pk>/delete-draft/", view=voucher_delete_draft_view, name="voucher-delete-draft"),
    path("vouchers/<int:pk>/reverse/", view=voucher_reverse_view, name="voucher-reverse"),
]

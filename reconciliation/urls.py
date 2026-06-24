"""
URL configuration for the Bank Reconciliation Engine.

Provides routes for:
  - Dashboard overview
  - Statement import workflow (upload, detail)
  - Main reconciliation workspace
  - Match/unmatch/force-match actions
  - Exception management
  - Reports (BRS, unmatched, duplicates)
"""

from django.urls import path

from reconciliation.views import (
    audit_log_view,
    dashboard_view,
    manual_entry_view,
    manual_entry_voucher_match_view,
    manual_entry_voucher_match_edit_view,
    manual_entry_row_add_view,
    manual_entry_row_validate_view,
    manual_entry_batch_save_view,
    manual_entry_shortcodes_view,
    manual_entry_narrations_view,
    manual_statement_import_view,
    manual_workspace_batch_save_view,
    manual_workspace_cell_update_view,
    manual_workspace_grid_data_view,
    manual_workspace_paste_view,
    manual_workspace_rows_delete_view,
    manual_workspace_save_row_view,
    manual_workspace_suggestions_view,
    manual_workspace_undo_view,
    manual_workspace_view,
    manual_workspace_delete_row_view,
    link_audit_view,
    statement_import_view,
    statement_import_detail_view,
    workspace_view,
    run_matching_view,
    confirm_match_view,
    unmatched_link_view,
    mark_duplicate_view,
    force_match_view,
    exception_list_view,
    create_adjustment_view,
    create_adjustment_for_orphan_view,
    brs_report_view,
    unmatched_report_view,
    duplicate_report_view,
    import_history_view,
)

app_name = "reconciliation"

urlpatterns = [
    # Dashboard
    path("", view=dashboard_view, name="dashboard"),
    # Statement import
    path("import/", view=statement_import_view, name="statement-import"),
    path("import/manual/", view=manual_statement_import_view, name="manual-statement-import"),
    path("import/<int:pk>/", view=statement_import_detail_view, name="statement-import-detail"),
    path("imports/", view=import_history_view, name="import-history"),
    # Redesigned manual entry
    path("manual-entry/", view=manual_entry_view, name="manual-entry"),
    path("manual-entry/voucher-match/", view=manual_entry_voucher_match_view, name="manual-entry-voucher-match"),
    path("manual-entry/voucher-match/<int:link_id>/edit/", view=manual_entry_voucher_match_edit_view, name="manual-entry-voucher-match-edit"),
    path("manual-entry/row/add/", view=manual_entry_row_add_view, name="manual-entry-row-add"),
    path("manual-entry/row/validate/", view=manual_entry_row_validate_view, name="manual-entry-row-validate"),
    path("manual-entry/batch/save/", view=manual_entry_batch_save_view, name="manual-entry-batch-save"),
    path("manual-entry/shortcodes/", view=manual_entry_shortcodes_view, name="manual-entry-shortcodes"),
    path("manual-entry/narrations/", view=manual_entry_narrations_view, name="manual-entry-narrations"),
    # Manual reconciliation workspace V1
    path("workspace/manual/", view=manual_workspace_view, name="manual-workspace"),
    path("workspace/manual/rows/", view=manual_workspace_save_row_view, name="manual-workspace-save-row"),
    path("workspace/manual/rows/<int:tx_id>/delete/", view=manual_workspace_delete_row_view, name="manual-workspace-delete-row"),
    path("workspace/manual/paste/", view=manual_workspace_paste_view, name="manual-workspace-paste"),
    path("workspace/manual/rows/<int:tx_id>/suggestions/", view=manual_workspace_suggestions_view, name="manual-workspace-suggestions"),
    # Manual workspace Excel-like grid API endpoints
    path("api/manual-workspace/cell-update/<int:import_id>/", view=manual_workspace_cell_update_view, name="manual-workspace-cell-update"),
    path("api/manual-workspace/batch-save/<int:import_id>/", view=manual_workspace_batch_save_view, name="manual-workspace-batch-save"),
    path("api/manual-workspace/undo/<int:import_id>/", view=manual_workspace_undo_view, name="manual-workspace-undo"),
    path("api/manual-workspace/rows/delete/<int:import_id>/", view=manual_workspace_rows_delete_view, name="manual-workspace-rows-delete"),
    path("api/manual-workspace/grid-data/<int:import_id>/", view=manual_workspace_grid_data_view, name="manual-workspace-grid-data"),
    # Workspace
    path("workspace/", view=workspace_view, name="workspace"),
    path("run-matching/", view=run_matching_view, name="run-matching"),
    # Match actions
    path("match/<int:link_id>/confirm/", view=confirm_match_view, name="confirm-match"),
    path("match/<int:link_id>/unlink/", view=unmatched_link_view, name="unlink-match"),
    path("match/<int:link_id>/duplicate/", view=mark_duplicate_view, name="mark-duplicate"),
    path("force-match/", view=force_match_view, name="force-match"),
    # Adjustments
    path("match/<int:link_id>/adjust/", view=create_adjustment_view, name="create-adjustment"),
    path("adjust-orphan/", view=create_adjustment_for_orphan_view, name="adjust-orphan"),
    # Exceptions
    path("exceptions/", view=exception_list_view, name="exceptions"),
    # Audit Trail
    path("match/<int:link_id>/audit/", view=link_audit_view, name="link-audit"),
    path("audit/", view=audit_log_view, name="audit-log"),
    # Reports
    path("reports/brs/", view=brs_report_view, name="report-brs"),
    path("reports/unmatched/", view=unmatched_report_view, name="report-unmatched"),
    path("reports/duplicates/", view=duplicate_report_view, name="report-duplicates"),
]

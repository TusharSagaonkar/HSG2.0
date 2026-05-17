from django.urls import path

from .views import (
    share_allotment_view,
    share_transfer_view,
    share_transmission_view,
    share_correction_view,
    member_share_history_view,
    share_certificate_list_view,
    share_certificate_detail_view,
    share_dashboard_view,
    share_rules_view,
    share_ledger_list_view,
    EventLogListView,
    EventLogDetailView,
)

app_name = "shares"

urlpatterns = [
    # Dashboard and overview
    path("", view=share_dashboard_view, name="dashboard"),
    path("admin/", view=share_rules_view, name="rules"),
    path("ledger/", view=share_ledger_list_view, name="ledger-list"),
    
    # Share operations
    path("allot/", view=share_allotment_view, name="allotment"),
    path("transfer/", view=share_transfer_view, name="transfer"),
    path("transmit/", view=share_transmission_view, name="transmission"),
    path("correct/", view=share_correction_view, name="correction"),
    
    # Member share history
    path("member/<int:pk>/history/", view=member_share_history_view, name="member-share-history"),
    
    # Share certificates
    path("certificates/", view=share_certificate_list_view, name="certificate-list"),
    path("certificates/<int:pk>/", view=share_certificate_detail_view, name="certificate-detail"),
    
    # Event logs
    path("event-logs/", view=EventLogListView.as_view(), name="event-log-list"),
    path("event-logs/<int:pk>/", view=EventLogDetailView.as_view(), name="event-log-detail"),
]

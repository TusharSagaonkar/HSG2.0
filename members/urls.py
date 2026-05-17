from django.urls import path

from members.views import (
    member_detail_view,
    nominee_create_view,
    nominee_update_view,
    nominee_list_view,
    member_share_dashboard_view,
)

app_name = "members"

urlpatterns = [
    # Member detail
    path("<int:pk>/", view=member_detail_view, name="member-detail"),
    
    # Member share dashboard
    path("<int:pk>/shares/", view=member_share_dashboard_view, name="member-share-dashboard"),
    
    # Nominee management
    path("<int:member_pk>/nominees/", view=nominee_list_view, name="nominee-list"),
    path("<int:member_pk>/nominees/create/", view=nominee_create_view, name="nominee-create"),
    path("<int:member_pk>/nominees/<int:pk>/update/", view=nominee_update_view, name="nominee-update"),
]

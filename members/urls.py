from django.urls import path

from members.views import member_detail_view

app_name = "members"

urlpatterns = [
    path("<int:pk>/", view=member_detail_view, name="member-detail"),
]

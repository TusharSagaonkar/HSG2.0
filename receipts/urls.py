from django.urls import path

from receipts.views import receipt_detail_view
from receipts.views import receipt_list_view

app_name = "receipts"

urlpatterns = [
    path("", view=receipt_list_view, name="receipt-list"),
    path("<int:pk>/", view=receipt_detail_view, name="receipt-detail"),
]

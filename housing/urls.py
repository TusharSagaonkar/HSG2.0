from django.urls import path
from django.views.generic import RedirectView

from housing.views import billing_generate_view
from housing.views import charge_template_create_view
from housing.views import finance_dashboard_view
from housing.views import email_verification_view
from housing.views import member_create_view
from housing.views import member_list_view
from housing.views import member_update_view
from housing.views import member_form_options_api_view
from housing.views import unit_search_api_view
from housing.views import outstanding_dashboard_view
from housing.views import receipt_post_view
from housing.views import reminder_schedule_view
from housing.views import resend_verification_email_view
from housing.views import update_membership_view
from housing.views import society_admin_redirect_view
from housing.views import society_admin_view
from housing.views import society_profile_update_view
from housing.views import society_settings_update_view
from housing.views import society_user_create_view
from housing.views import society_create_view
from housing.views import society_detail_view
from housing.views import society_email_settings_view
from housing.views import society_list_view
from housing.views import structure_create_view
from housing.views import structure_unit_dashboard_view
from housing.views import bulk_unit_create_view
from housing.views import unit_detail_view
from housing.views import unit_create_view
from housing.views import unit_occupancy_create_view
from housing.views import unit_ownership_create_view
from housing.views import society_voucher_templates_view

app_name = "housing"

urlpatterns = [
    path("", view=RedirectView.as_view(pattern_name="home", permanent=False), name="dashboard"),
    path("societies/add/", view=society_create_view, name="society-add"),
    path("societies/", view=society_list_view, name="society-list"),
    path("societies/admin/", view=society_admin_redirect_view, name="society-admin-redirect"),
    path("societies/<int:pk>/", view=society_detail_view, name="society-detail"),
    path("societies/<int:pk>/admin/", view=society_admin_view, name="society-admin"),
    path("societies/<int:pk>/admin/settings/", view=society_settings_update_view, name="society-settings-update"),
    path("societies/<int:pk>/admin/profile/", view=society_profile_update_view, name="society-profile-update"),
    path("societies/<int:pk>/admin/voucher-templates/", view=society_voucher_templates_view, name="society-voucher-templates"),
    path("societies/<int:pk>/admin/user/create/", view=society_user_create_view, name="society-user-create"),
    path("societies/<int:society_pk>/members/<int:user_id>/resend-verification/", view=resend_verification_email_view, name="resend-verification-email"),
    path("societies/<int:society_pk>/members/<int:user_id>/update/", view=update_membership_view, name="update-membership"),
    path(
        "societies/<int:pk>/email-settings/",
        view=society_email_settings_view,
        name="society-email-settings",
    ),
    path(
        "structures-units/",
        view=structure_unit_dashboard_view,
        name="structure-unit-dashboard",
    ),
    path("units/<int:pk>/", view=unit_detail_view, name="unit-detail"),
    path("structures/add/", view=structure_create_view, name="structure-add"),
    path("units/add/", view=unit_create_view, name="unit-add"),
    path("units/bulk-add/", view=bulk_unit_create_view, name="unit-bulk-add"),
    path("ownerships/add/", view=unit_ownership_create_view, name="ownership-add"),
    path("occupancies/add/", view=unit_occupancy_create_view, name="occupancy-add"),
    path("members/", view=member_list_view, name="member-list"),
    path("members/add/", view=member_create_view, name="member-add"),
    path("members/api/form-options/", view=member_form_options_api_view, name="member-form-options-api"),
    path("members/api/unit-search/", view=unit_search_api_view, name="unit-search-api"),
    path("members/<int:pk>/edit/", view=member_update_view, name="member-edit"),
    path(
        "billing/templates/add/",
        view=charge_template_create_view,
        name="charge-template-add",
    ),
    path("billing/generate/", view=billing_generate_view, name="billing-generate"),
    path("finance/", view=finance_dashboard_view, name="finance-dashboard"),
    path("receipts/post/", view=receipt_post_view, name="receipt-post"),
    path("outstanding/", view=outstanding_dashboard_view, name="outstanding-dashboard"),
    path("reminders/schedule/", view=reminder_schedule_view, name="reminder-schedule"),
    path("verify-email/<str:token>/", view=email_verification_view, name="email-verify"),
]

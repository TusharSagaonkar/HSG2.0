from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views import defaults as default_views

from config.views import HomeDashboardView
from parking.views import verify_vehicle

urlpatterns = [
    path("", HomeDashboardView.as_view(), name="home"),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # User management
    path("users/", include("housing_accounting.users.urls", namespace="users")),
    path("accounts/", include("allauth.urls")),
    path("housing/", include("housing.urls", namespace="housing")),
    path("accounting/", include("accounting.urls", namespace="accounting")),
    path("billing/", include("billing.urls", namespace="billing")),
    path("parking/", include("parking.urls", namespace="parking")),
    path("vehicle/verify/<uuid:token>/", view=verify_vehicle, name="vehicle_verify"),
    path("receipts/", include("receipts.urls", namespace="receipts")),
    path("notifications/", include("notifications.urls", namespace="notifications")),
    path("members/", include("members.urls", namespace="members")),
    # Your stuff: custom urls includes go here
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]


if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]

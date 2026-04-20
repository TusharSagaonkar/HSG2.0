from django.urls import path

from . import views

app_name = 'administration'

urlpatterns = [
    path(
        'api/shortcuts/',
        views.get_shortcuts_api,
        name='shortcuts_api',
    ),
    # Alternative class-based view
    # path('api/shortcuts/', views.ShortcutAPIView.as_view(), name='shortcuts_api'),
]
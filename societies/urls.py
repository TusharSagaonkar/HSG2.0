from django.urls import path
from . import views

app_name = 'societies'

urlpatterns = [
    # List and CRUD operations
    path('', views.society_list, name='society-list'),
    path('create/', views.society_create, name='society-create'),
    path('<int:pk>/', views.society_detail, name='society-detail'),
    path('<int:pk>/update/', views.society_update, name='society-update'),
    path('<int:pk>/delete/', views.society_delete, name='society-delete'),
    
    # HTMX endpoints
    path('validate-field/', views.validate_field, name='validate-field'),
    path('<int:pk>/config/', views.society_config, name='society-config'),
    path('<int:pk>/stats/', views.society_stats, name='society-stats'),
    path('<int:pk>/activity/', views.society_activity, name='society-activity'),
]

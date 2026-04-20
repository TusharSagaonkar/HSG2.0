from django.contrib import admin
from django.utils.html import format_html
from .models import Shortcut


class ShortcutAdmin(admin.ModelAdmin):
    """Admin configuration for Shortcut model"""
    
    list_display = (
        'name',
        'display_key_combination',
        'action_type_display',
        'scope_display',
        'page',
        'role',
        'is_active',
        'created_at',
    )
    
    list_filter = (
        'action_type',
        'scope',
        'role',
        'is_active',
        'created_at',
    )
    
    search_fields = (
        'name',
        'key_combination',
        'normalized_key',
        'action_value',
        'page',
    )
    
    readonly_fields = (
        'normalized_key',
        'created_at',
    )
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'is_active', 'priority')
        }),
        ('Shortcut Configuration', {
            'fields': ('key_combination', 'normalized_key', 'action_type', 'action_value')
        }),
        ('Targeting & Scope', {
            'fields': ('scope', 'page', 'role')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def display_key_combination(self, obj):
        """Display key combination with styling"""
        return format_html(
            '<code style="background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-weight: bold;">{}</code>',
            obj.key_combination
        )
    display_key_combination.short_description = 'Key Combination'
    
    def action_type_display(self, obj):
        """Display action type with colors"""
        colors = {
            'URL': 'blue',
            'MODAL': 'green',
            'JS': 'orange',
        }
        color = colors.get(obj.action_type, 'gray')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_action_type_display()
        )
    action_type_display.short_description = 'Action Type'
    
    def scope_display(self, obj):
        """Display scope with colors"""
        colors = {
            'GLOBAL': 'purple',
            'PAGE': 'teal',
        }
        color = colors.get(obj.scope, 'gray')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_scope_display()
        )
    scope_display.short_description = 'Scope'
    
    list_per_page = 50
    ordering = ('-priority', '-created_at')
    date_hierarchy = 'created_at'
    
    def get_queryset(self, request):
        """Optimize queryset with select_related"""
        return super().get_queryset(request).select_related()


admin.site.register(Shortcut, ShortcutAdmin)

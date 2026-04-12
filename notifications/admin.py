from django.contrib import admin

from notifications.models import EmailLog
from notifications.models import EmailQueue
from notifications.models import EmailTemplate
from notifications.models import GlobalEmailSettings
from notifications.models import SocietyEmailSettings


class AdminGlobalEmailSettings(GlobalEmailSettings):
    class Meta:
        proxy = True
        app_label = "notifications"
        verbose_name = "Global email settings"
        verbose_name_plural = "Global email settings"


class AdminSocietyEmailSettings(SocietyEmailSettings):
    class Meta:
        proxy = True
        app_label = "notifications"
        verbose_name = "Society email settings"
        verbose_name_plural = "Society email settings"


class AdminEmailTemplate(EmailTemplate):
    class Meta:
        proxy = True
        app_label = "notifications"
        verbose_name = "Email template"
        verbose_name_plural = "Email templates"


class AdminEmailQueue(EmailQueue):
    class Meta:
        proxy = True
        app_label = "notifications"
        verbose_name = "Email queue"
        verbose_name_plural = "Email queue"


class AdminEmailLog(EmailLog):
    class Meta:
        proxy = True
        app_label = "notifications"
        verbose_name = "Email log"
        verbose_name_plural = "Email logs"


@admin.register(AdminGlobalEmailSettings)
class GlobalEmailSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "provider_type",
        "smtp_host",
        "smtp_port",
        "default_from_email",
        "active",
        "updated_at",
    )
    list_filter = ("provider_type", "active", "use_tls", "use_ssl")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AdminSocietyEmailSettings)
class SocietyEmailSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "provider_type",
        "smtp_host",
        "smtp_port",
        "default_from_email",
        "is_active",
    )
    list_filter = ("provider_type", "is_active", "use_tls", "use_ssl")
    search_fields = ("society__name", "smtp_host", "smtp_username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AdminEmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("template_name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("template_name",)


@admin.register(AdminEmailQueue)
class EmailQueueAdmin(admin.ModelAdmin):
    list_display = (
        "recipient_email",
        "email_type",
        "status",
        "scheduled_at",
        "sent_at",
        "society",
    )
    list_filter = ("email_type", "status", "society")
    search_fields = ("recipient_email", "subject")
    readonly_fields = (
        "smtp_used",
        "retry_count",
        "error_message",
        "created_at",
        "updated_at",
        "sent_at",
    )


@admin.register(AdminEmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("email_queue", "attempt_no", "smtp_host", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "email_queue",
        "attempt_no",
        "smtp_host",
        "response",
        "status",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

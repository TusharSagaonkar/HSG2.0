from django.apps import AppConfig


class SharesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shares'
    verbose_name = 'Share Management'

    def ready(self):
        """
        Import signals to connect them.
        
        Signals connected:
        - ShareLedger post_save -> log_share_ledger_event
        - Nominee post_save/post_delete -> log_nominee_save_event / log_nominee_delete_event
        - ShareCertificate post_save -> log_share_certificate_event
        - Member pre_save/post_save -> capture_member_share_balance_before / log_member_share_balance_change
        """
        import shares.signals  # noqa: F401

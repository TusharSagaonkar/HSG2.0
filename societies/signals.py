"""
Signals for the societies app.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Society, SocietyConfig
from accounting.models import AccountMapping


@receiver(post_save, sender=Society)
def create_default_society_config(sender, instance, created, **kwargs):
    """
    Automatically create a default SocietyConfig and AccountMapping when a new Society is created.
    """
    if created:
        SocietyConfig.objects.get_or_create(
            society=instance,
            defaults={
                "share_value": 100.00,
                "default_share_count": 1,
                "entrance_fee": 0,
                "transfer_fee": 0,
                "premium_amount": 0,
                "allow_multiple_nominees": False,
                "require_approval": True,
            }
        )
        # Ensure AccountMapping exists (will create default mapping if missing)
        AccountMapping.ensure_for_society(instance)
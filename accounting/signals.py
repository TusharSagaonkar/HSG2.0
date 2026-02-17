from datetime import date

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from accounting.models import FinancialYear
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from societies.models import Society


def _current_financial_year_range(today):
    start_year = today.year if today.month >= 4 else today.year - 1
    start_date = date(start_year, 4, 1)
    end_date = date(start_year + 1, 3, 31)
    base_name = f"FY {start_year}-{str(start_year + 1)[-2:]}"
    return base_name, start_date, end_date


@receiver(post_save, sender=Society)
def bootstrap_accounts_for_new_society(sender, instance, created, **kwargs):
    if not created:
        return

    base_name, start_date, end_date = _current_financial_year_range(
        timezone.localdate()
    )
    FinancialYear.objects.get_or_create(
        society=instance,
        start_date=start_date,
        end_date=end_date,
        defaults={
            "name": base_name,
            "is_open": True,
        },
    )

    ensure_standard_categories(instance)
    create_default_accounts_for_society(instance)

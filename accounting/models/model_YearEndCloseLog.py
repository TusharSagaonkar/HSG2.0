from django.conf import settings
from django.db import models


class YearEndCloseLog(models.Model):
    source_financial_year = models.OneToOneField(
        "accounting.FinancialYear",
        on_delete=models.CASCADE,
        related_name="year_end_close_log",
    )
    target_financial_year = models.ForeignKey(
        "accounting.FinancialYear",
        on_delete=models.PROTECT,
        related_name="carry_forward_sources",
    )
    opening_voucher = models.ForeignKey(
        "accounting.Voucher",
        on_delete=models.PROTECT,
        related_name="carry_forward_logs",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="year_end_actions",
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-performed_at", "-id")

    def __str__(self):
        return (
            f"Close {self.source_financial_year} -> {self.target_financial_year} "
            f"({self.opening_voucher.display_number})"
        )

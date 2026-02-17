from django.db import models
from django.db.models import F
from django.core.exceptions import ValidationError
from societies.models import Society


class VoucherSequence(models.Model):
    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="voucher_sequences",
    )

    voucher_type = models.CharField(
        max_length=20,
    )

    current_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("society", "voucher_type")

    def __str__(self):
        return f"{self.society} - {self.voucher_type} ({self.current_number})"

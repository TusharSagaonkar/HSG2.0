from django.db import models

from societies.models import Society


class BankParserProfile(models.Model):
    """Registry entry for a bank statement format parser."""

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="bank_parser_profiles",
    )
    bank_name = models.CharField(max_length=100)
    format_name = models.CharField(max_length=120)
    file_type = models.CharField(max_length=20)
    header_signature = models.JSONField(default=dict, blank=True)
    parser_class = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=100)
    confidence_floor = models.IntegerField(default=70)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-priority", "bank_name", "format_name")
        indexes = [
            models.Index(fields=["society", "is_active"]),
            models.Index(fields=["society", "bank_name", "file_type"]),
        ]

    def __str__(self):
        return f"{self.bank_name} / {self.format_name} ({self.file_type})"

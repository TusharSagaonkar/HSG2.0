# shares/models/model_ShareCertificate.py

from django.db import models
from django.core.exceptions import ValidationError
from members.models.model_Member import Member


class ShareCertificate(models.Model):
    """
    Share certificate tracking for members.
    Certificates are issued when shares are allotted.
    """
    
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CANCELLED = "CANCELLED", "Cancelled"
        REPLACED = "REPLACED", "Replaced"
        # Additional statuses from design document for completeness
        ISSUED = "ISSUED", "Issued"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        LOST = "LOST", "Lost (Replacement Issued)"
    
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="share_certificates"
    )
    certificate_no = models.CharField(
        max_length=50,
        unique=True,
        help_text="Unique certificate number"
    )
    share_count = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Number of shares covered by this certificate"
    )
    issued_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE
    )
    
    # For transfers (optional)
    transferred_to = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_certificates"
    )
    transferred_date = models.DateField(null=True, blank=True)
    
    # Audit fields
    issued_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates_issued"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = "shares"
        ordering = ["-issued_date", "certificate_no"]
        indexes = [
            models.Index(fields=["member", "status"]),
            models.Index(fields=["certificate_no"]),
        ]
        verbose_name = "Share Certificate"
        verbose_name_plural = "Share Certificates"
    
    def clean(self):
        if self.status == self.Status.TRANSFERRED and not self.transferred_to:
            raise ValidationError("Transferred certificate must have transferred_to member")
        
        # Ensure share_count is positive
        if self.share_count <= 0:
            raise ValidationError("Share count must be positive")
    
    def __str__(self):
        return f"Cert #{self.certificate_no} - {self.member} ({self.share_count} shares)"
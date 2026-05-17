from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from members.models.model_Member import Member


class Nominee(models.Model):
    """
    Nominee records for members.
    Implements versioning via is_active and deactivated_at.
    Only one active set per member is allowed at a time.
    """
    
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="nominees"
    )
    name = models.CharField(max_length=255)
    relationship = models.CharField(
        max_length=50,
        help_text="Relationship to member (e.g., Spouse, Child, Parent)"
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Percentage of shares nominated (0-100)"
    )
    priority_order = models.PositiveIntegerField(
        default=1,
        help_text="Priority order for multiple nominees (1 = highest)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Set to False when nominee is replaced (don't delete)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when nominee was replaced/deactivated"
    )
    deactivated_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deactivated_nominees"
    )
    
    class Meta:
        app_label = "housing"
        ordering = ["priority_order", "created_at"]
        constraints = [
            # Total percentage of active nominees cannot exceed 100
            models.CheckConstraint(
                check=models.Q(percentage__gte=0) & models.Q(percentage__lte=100),
                name="nominee_percentage_range"
            ),
            # Priority order must be unique per member among active nominees
            models.UniqueConstraint(
                fields=["member", "priority_order"],
                condition=models.Q(is_active=True),
                name="unique_active_priority_per_member"
            )
        ]
        indexes = [
            models.Index(fields=["member", "is_active"]),
            models.Index(fields=["is_active", "deactivated_at"]),
        ]
    
    def clean(self):
        # Validate total percentage of active nominees for the member
        if self.is_active and self.percentage is not None and self.percentage > 0 and self.member is not None:
            from django.db.models import Sum
            total = Nominee.objects.filter(
                member=self.member,
                is_active=True
            ).exclude(pk=self.pk).aggregate(
                total=models.Sum('percentage')
            )['total'] or 0
            
            if total + self.percentage > 100:
                raise ValidationError(
                    f"Total nominee percentage cannot exceed 100%. "
                    f"Current total: {total}%, adding: {self.percentage}%"
                )
    
    def deactivate(self, user=None):
        """Deactivate nominee (soft delete)."""
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_by = user
        self.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])
    
    def activate(self):
        """Reactivate a previously deactivated nominee."""
        self.is_active = True
        self.deactivated_at = None
        self.deactivated_by = None
        self.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])
    
    @classmethod
    def get_active_nominees_for_member(cls, member):
        """Return all active nominees for a given member."""
        return cls.objects.filter(member=member, is_active=True).order_by('priority_order')
    
    def __str__(self):
        return f"{self.name} ({self.relationship}) - {self.percentage}%"
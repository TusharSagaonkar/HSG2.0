import secrets
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class EmailVerificationToken(models.Model):
    """Token for verifying email addresses for new users."""
    
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    token = models.CharField(max_length=128, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "housing"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("token",)),
            models.Index(fields=("user", "is_used")),
        ]

    def __str__(self):
        return f"Email verification token for {self.user.email}"

    @classmethod
    def create_token(cls, user, expires_in_hours=24):
        """Create a new verification token for a user."""
        from django.utils import timezone
        
        # Invalidate previous tokens
        cls.objects.filter(user=user, is_used=False).delete()
        
        token = secrets.token_urlsafe(96)
        expires_at = timezone.now() + timezone.timedelta(hours=expires_in_hours)
        
        return cls.objects.create(
            user=user,
            token=token,
            expires_at=expires_at,
        )

    def is_expired(self):
        """Check if the token has expired."""
        return timezone.now() > self.expires_at

    def verify(self):
        """Mark the token as used and the email as verified."""
        if self.is_used:
            return False
        if self.is_expired():
            return False
        
        self.is_used = True
        self.verified_at = timezone.now()
        self.save(update_fields=["is_used", "verified_at"])
        
        # Mark user email as verified
        user = self.user
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        
        return True

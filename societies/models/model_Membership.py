from django.db import models


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        ACCOUNTANT = "accountant", "Accountant"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invited_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "society"),
                name="housing_membership_unique_user_society",
            ),
        ]
        indexes = [
            models.Index(fields=("society",), name="hsg_mship_soc_idx"),
            models.Index(fields=("user", "society"), name="hsg_mship_usr_soc_idx"),
            models.Index(
                fields=("society", "is_active"),
                name="hsg_mship_soc_act_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.society} ({self.role})"

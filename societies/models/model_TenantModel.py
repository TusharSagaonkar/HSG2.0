import uuid

from django.conf import settings
from django.db import models


class TenantModel(models.Model):
    """Abstract base for all tenant-scoped models.

    Provides:
    - society FK for tenant isolation
    - created_by/updated_by/deleted_by for accountability
    - uuid for external references (avoids sequential ID enumeration)
    - version for optimistic locking
    - is_deleted/deleted_at for soft-delete

    All fields are nullable initially for safe migration.
    Subclasses should make fields non-nullable via follow-up migrations
    after data backfill.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="+",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        null=True,
        blank=True,
    )
    version = models.PositiveIntegerField(default=1, null=True, blank=True)
    is_deleted = models.BooleanField(default=False, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

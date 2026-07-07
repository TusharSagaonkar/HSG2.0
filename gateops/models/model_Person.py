from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from gateops.crypto import decrypt_id_number, encrypt_id_number


class Person(models.Model):
    """Master person record, deduplicated across all gate events by (society, phone).

    A single physical visitor maps to exactly one ``Person`` row per society,
    identified by phone number. This enables visit history aggregation,
    blacklisting, VIP flagging, and ID-document storage without duplicating
    visitor data across every gate event.

    The ``id_number`` is stored encrypted at rest via Fernet (see
    :mod:`gateops.crypto`). Access is transparent through the ``id_number``
    property which encrypts on assignment and decrypts on read.

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    The conditional unique constraint on ``(society, phone)`` only enforces
    uniqueness among active persons with a non-empty phone, so a soft-deleted
    phone can be reused.
    """

    class IdType(models.TextChoices):
        AADHAAR = "aadhaar", _("Aadhaar")
        PAN = "pan", _("PAN")
        PASSPORT = "passport", _("Passport")
        DL = "dl", _("Driving License")
        VOTER = "voter", _("Voter ID")
        OTHER = "other", _("Other")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="persons",
        verbose_name=_("society"),
    )
    name = models.CharField(_("name"), max_length=200)
    phone = models.CharField(_("phone"), max_length=20, db_index=True)
    email = models.EmailField(_("email"), blank=True)
    photo = models.ImageField(_("photo"), null=True, blank=True, upload_to="gateops/persons/")
    id_type = models.CharField(
        _("ID type"), max_length=20, choices=IdType.choices, null=True, blank=True
    )
    id_number_encrypted = models.TextField(_("ID number (encrypted)"), blank=True)
    is_blacklisted = models.BooleanField(_("blacklisted"), default=False)
    blacklist_reason = models.TextField(_("blacklist reason"), blank=True)
    blacklist_until = models.DateField(_("blacklist until"), null=True, blank=True)
    is_vip = models.BooleanField(_("VIP"), default=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("persons")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["society", "phone"],
                condition=models.Q(is_active=True, phone__gt=""),
                name="uniq_person_phone_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_blacklisted"], name="person_bl_idx"),
            models.Index(fields=["society", "is_vip"], name="person_vip_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def id_number(self):
        """Transparently decrypt the ID number on access."""
        return decrypt_id_number(self.id_number_encrypted)

    @id_number.setter
    def id_number(self, value):
        """Encrypt the ID number on assignment."""
        self.id_number_encrypted = encrypt_id_number(value or "")

    def clean(self):
        super().clean()
        # Name is mandatory and must not be blank/whitespace.
        if not self.name or not self.name.strip():
            raise ValidationError({"name": _("Name is required.")})
        # Blacklist requires a reason; an unexplained blacklist is an audit gap.
        if self.is_blacklisted and not self.blacklist_reason.strip():
            raise ValidationError(
                {"blacklist_reason": _("Blacklist reason is required when blacklisted.")}
            )
        # Blacklist window must be a future date (otherwise it is already expired).
        if self.blacklist_until and self.blacklist_until <= timezone.now().date():
            raise ValidationError(
                {"blacklist_until": _("Blacklist until must be a future date.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

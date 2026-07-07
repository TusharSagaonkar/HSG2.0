from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class GateEventPhoto(models.Model):
    """A photo captured during a gate event (arrival, exit, vehicle, material, ID).

    Multiple photos may be attached to a single :class:`GateEvent` — e.g. an
    arrival photo of the visitor, a vehicle plate photo, and an ID-document
    photo. ``photo_type`` classifies the capture context.
    """

    class PhotoType(models.TextChoices):
        ARRIVAL = "arrival", _("Arrival")
        EXIT = "exit", _("Exit")
        VEHICLE = "vehicle", _("Vehicle")
        MATERIAL = "material", _("Material")
        ID_DOCUMENT = "id_document", _("ID Document")

    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name=_("gate event"),
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        verbose_name=_("society"),
    )
    photo_type = models.CharField(
        _("photo type"), max_length=20, choices=PhotoType.choices, default=PhotoType.ARRIVAL
    )
    image = models.ImageField(_("image"), upload_to="gateops/events/")
    captured_at = models.DateTimeField(_("captured at"), default=timezone.now)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("uploaded by"),
    )

    class Meta:
        verbose_name = _("gate event photo")
        verbose_name_plural = _("gate event photos")
        ordering = ("-captured_at",)
        indexes = [
            models.Index(fields=["gate_event"], name="gatephoto_evt_idx"),
        ]

    def __str__(self):
        return f"{self.photo_type} photo for {self.gate_event_id}"

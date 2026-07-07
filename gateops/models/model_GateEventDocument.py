from django.db import models
from django.utils.translation import gettext_lazy as _


class GateEventDocument(models.Model):
    """An arbitrary document attached to a :class:`GateEvent`.

    Unlike :class:`GateEventPhoto` (image captures), this model stores
    free-form file attachments such as signed delivery challans, material
    invoices, or contractor work orders. ``document_type`` is a free-text
    label (e.g. ``"delivery_challan"``, ``"invoice"``).
    """

    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name=_("gate event"),
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        verbose_name=_("society"),
    )
    document_type = models.CharField(_("document type"), max_length=50)
    file = models.FileField(_("file"), upload_to="gateops/documents/")
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)

    class Meta:
        verbose_name = _("gate event document")
        verbose_name_plural = _("gate event documents")
        ordering = ("-uploaded_at",)
        indexes = [
            models.Index(fields=["gate_event"], name="gatedoc_evt_idx"),
        ]

    def __str__(self):
        return f"{self.document_type} for {self.gate_event_id}"

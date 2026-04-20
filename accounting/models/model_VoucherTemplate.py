from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from societies.models import Society
from accounting.models.model_Account import Account
from members.models import Unit
from accounting.models.model_Voucher import Voucher


class VoucherTemplate(models.Model):
    """
    A society‑specific template that pre‑fills the voucher entry form for a given voucher type.
    """
    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="voucher_templates",
        help_text=_("Society that owns this template."),
    )

    voucher_type = models.CharField(
        max_length=20,
        choices=Voucher.VoucherType.choices,
        help_text=_("Voucher type this template is intended for."),
    )

    name = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Human‑readable name for the template (e.g., 'Monthly Maintenance Receipt')."),
    )

    narration = models.TextField(
        blank=True,
        help_text=_("Default narration text for vouchers created from this template."),
    )

    payment_mode = models.CharField(
        max_length=20,
        choices=Voucher.PaymentMode.choices,
        blank=True,
        help_text=_("Default payment mode (if applicable)."),
    )

    reference_number_pattern = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Optional pattern for generating reference numbers (e.g., 'CHQ‑{seq}')."),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this template is available for use."),
    )

    is_pinned = models.BooleanField(
        default=False,
        help_text=_("Pinned templates are shown before other templates."),
    )

    sort_order = models.IntegerField(
        default=0,
        help_text=_("Manual display order among templates with similar priority."),
    )

    usage_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of times this template has been used from voucher entry."),
    )

    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this template was most recently used from voucher entry."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("society", "-is_pinned", "-usage_count", "sort_order", "voucher_type", "name", "id")
        verbose_name = _("Voucher Template")
        verbose_name_plural = _("Voucher Templates")

    def __str__(self):
        if self.name:
            return f"{self.society.name} – {self.get_voucher_type_display()} – {self.name}"
        return f"{self.society.name} – {self.get_voucher_type_display()}"

    def clean(self):
        super().clean()
        # Ensure that the template's voucher type is consistent with payment mode requirements
        if self.voucher_type in (Voucher.VoucherType.RECEIPT, Voucher.VoucherType.PAYMENT):
            if not self.payment_mode:
                raise ValidationError(
                    _("Payment mode is required for Receipt and Payment voucher templates.")
                )
        # If a reference number pattern is provided, ensure it's not empty
        if self.reference_number_pattern and not self.reference_number_pattern.strip():
            self.reference_number_pattern = ""

    @classmethod
    def ordered_for_quick_actions(cls, queryset=None):
        queryset = queryset if queryset is not None else cls.objects.all()
        return queryset.order_by(
            "-is_pinned",
            "-usage_count",
            "sort_order",
            "voucher_type",
            "name",
            "id",
        )


class VoucherTemplateRow(models.Model):
    """
    A single ledger row within a voucher template.
    """
    class Side(models.TextChoices):
        DEBIT = "DEBIT", _("Debit")
        CREDIT = "CREDIT", _("Credit")

    template = models.ForeignKey(
        VoucherTemplate,
        on_delete=models.CASCADE,
        related_name="rows",
        help_text=_("The template this row belongs to."),
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        help_text=_("Ledger account to pre‑fill."),
    )

    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text=_("Optional unit to link (must belong to the same society)."),
    )

    side = models.CharField(
        max_length=10,
        choices=Side.choices,
        help_text=_("Whether this row is a debit or credit entry."),
    )

    default_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Suggested amount (leave empty for zero)."),
    )

    order = models.IntegerField(
        default=0,
        help_text=_("Order of rows within the template (lower numbers appear first)."),
    )

    class Meta:
        ordering = ("template", "order", "id")
        verbose_name = _("Voucher Template Row")
        verbose_name_plural = _("Voucher Template Rows")

    def __str__(self):
        return f"{self.template} – {self.account.name} ({self.get_side_display()})"

    def clean(self):
        super().clean()
        # Ensure account belongs to the same society as the template
        if self.account.society_id != self.template.society_id:
            raise ValidationError(
                _("Account must belong to the same society as the template.")
            )
        # Ensure unit belongs to the same society as the template
        if self.unit and self.unit.structure.society_id != self.template.society_id:
            raise ValidationError(
                _("Unit must belong to the same society as the template.")
            )
        # Ensure default_amount is non‑negative
        if self.default_amount is not None and self.default_amount < 0:
            raise ValidationError(_("Default amount cannot be negative."))
        # Ensure side is valid
        if self.side not in (self.Side.DEBIT, self.Side.CREDIT):
            raise ValidationError(_("Side must be either DEBIT or CREDIT."))

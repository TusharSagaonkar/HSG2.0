from django.db import models, transaction
from django.db.models import F
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from collections import defaultdict
from societies.models import Society
from accounting.models.model_voucher_sequence import VoucherSequence
from accounting.models.model_FinancialYear import FinancialYear
from accounting.models.model_AccountingPeriod import AccountingPeriod


class Voucher(models.Model):
    class PaymentMode(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        CHEQUE = "CHEQUE", "Cheque"
        UPI = "UPI", "UPI"
        CARD = "CARD", "Card"
        OTHER = "OTHER", "Other"

    class VoucherType(models.TextChoices):
        GENERAL = "GENERAL", "General"
        RECEIPT = "RECEIPT", "Receipt"
        PAYMENT = "PAYMENT", "Payment"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        OPENING = "OPENING", "Opening Balance"

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="vouchers",
    )

    voucher_type = models.CharField(
        max_length=20,
        choices=VoucherType.choices,
    )

    voucher_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        editable=False,
    )

    voucher_date = models.DateField(db_index=True)
    narration = models.TextField(blank=True)
    payment_mode = models.CharField(
        max_length=20,
        choices=PaymentMode.choices,
        blank=True,
    )
    reference_number = models.CharField(max_length=120, blank=True)
    posted_at = models.DateTimeField(blank=True, null=True)
    reversal_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by_voucher",
    )

    class Meta:
        ordering = ("-voucher_date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "voucher_type", "voucher_number"],
                condition=Q(voucher_number__isnull=False),
                name="uniq_voucher_number_per_society_type",
            )
        ]
        indexes = [
            models.Index(fields=["society", "voucher_date"]),
            models.Index(fields=["society", "posted_at", "voucher_date"]),
        ]

    def clean(self):
        if self.voucher_date and self.voucher_date > timezone.localdate():
            raise ValidationError("Voucher date cannot be in the future.")

        # 🚫 Do nothing until voucher exists
        if not self.pk:
            return

        entries = list(self.entries.all())
        if len(entries) < 2:
            raise ValidationError("Voucher must contain at least two ledger entries.")

        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")

        account_sides = defaultdict(
            lambda: {"debit": Decimal("0.00"), "credit": Decimal("0.00")}
        )

        for e in entries:
            total_debit += e.debit
            total_credit += e.credit
            account_sides[e.account_id]["debit"] += e.debit
            account_sides[e.account_id]["credit"] += e.credit

        if total_debit != total_credit:
            raise ValidationError("Total debit and credit must be equal.")

        for sides in account_sides.values():
            if sides["debit"] > 0 and sides["credit"] > 0:
                raise ValidationError(
                    "Same account cannot be debited and credited in the same voucher."
                )

        self._validate_voucher_type_policy(entries)

    @staticmethod
    def _is_cash_or_bank_account(account):
        account_name = (account.name or "").lower()
        category_name = (account.category.name if account.category else "").lower()
        return (
            "cash" in account_name
            or "bank" in account_name
            or "cash" in category_name
            or "bank" in category_name
        )

    def _validate_voucher_type_policy(self, entries):
        if self.voucher_type not in (
            self.VoucherType.RECEIPT,
            self.VoucherType.PAYMENT,
        ):
            return

        if not self.payment_mode:
            raise ValidationError("Receipt and payment vouchers require payment mode.")

        if not (self.narration or "").strip():
            raise ValidationError("Receipt and payment vouchers require narration/reference.")

        if self.payment_mode != self.PaymentMode.CASH and not (self.reference_number or "").strip():
            raise ValidationError("Reference number is required for non-cash payment modes.")

        has_cash_bank_debit = any(
            self._is_cash_or_bank_account(entry.account) and entry.debit > 0 for entry in entries
        )
        has_cash_bank_credit = any(
            self._is_cash_or_bank_account(entry.account) and entry.credit > 0 for entry in entries
        )

        if self.voucher_type == self.VoucherType.RECEIPT and not has_cash_bank_debit:
            raise ValidationError("Receipt voucher must debit a cash/bank account.")

        if self.voucher_type == self.VoucherType.PAYMENT and not has_cash_bank_credit:
            raise ValidationError("Payment voucher must credit a cash/bank account.")

    def post(self):
        if self.posted_at:
            raise ValidationError("Voucher is already posted.")

        # 🔒 Financial Year lock check
        fy = FinancialYear.get_open_year_for_date(self.voucher_date, society=self.society)
        if not fy:
            raise ValidationError(
                f"No open financial year for voucher date {self.voucher_date}."
            )
        

        # 🔒 Month / Period lock
        if not AccountingPeriod.is_period_open(self.society, self.voucher_date):
            raise ValidationError(
                f"No open accounting period for date {self.voucher_date}."
            )

        self.full_clean()

        with transaction.atomic():
            seq, _ = VoucherSequence.objects.select_for_update().get_or_create(
                society=self.society,
                voucher_type=self.voucher_type,
                defaults={"current_number": 0},
            )

            seq.current_number = F("current_number") + 1
            seq.save(update_fields=["current_number"])
            seq.refresh_from_db()

            # 🔥 SINGLE system update (no save())
            Voucher.objects.filter(pk=self.pk).update(
                voucher_number=seq.current_number,
                posted_at=timezone.now(),
            )

        self.refresh_from_db()

    def save(self, *args, **kwargs):
        if self.pk:
            original = Voucher.objects.get(pk=self.pk)

            # Allow idempotent saves after posting
            if original.posted_at:
                immutable_fields = {
                    "society",
                    "voucher_type",
                    "voucher_date",
                    "narration",
                }
                for field in immutable_fields:
                    if getattr(self, field) != getattr(original, field):
                        raise ValidationError("Posted voucher cannot be modified.")

            if (
                original.voucher_number
                and self.voucher_number != original.voucher_number
            ):
                raise ValidationError("Voucher number cannot be changed.")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.voucher_type} - {self.voucher_date}"

    @property
    def display_number(self):
        if not self.voucher_number:
            return "DRAFT"
        return f"{self.voucher_type[:3]}-{self.voucher_number:05d}"

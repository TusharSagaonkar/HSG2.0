"""
Services for share management operations.
"""

from __future__ import annotations

from decimal import Decimal
import logging

from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from shares.models import ShareLedger, ShareCertificate, EventLog
from members.models.model_Member import Member
from members.models.model_Nominee import Nominee
from societies.models.model_SocietyConfig import SocietyConfig
from accounting.models import Voucher, LedgerEntry
from accounting.models.model_AccountMapping import AccountMapping
from accounting.models.model_FinancialYear import FinancialYear
from accounting.models.model_AccountingPeriod import AccountingPeriod
from django.contrib.auth import get_user_model


logger = logging.getLogger(__name__)


class ShareTransactionError(Exception):
    """Base exception for share transaction errors."""
    pass


class InsufficientSharesError(ShareTransactionError):
    """Raised when a member has insufficient shares for a transaction."""
    pass


class InvalidTransferError(ShareTransactionError):
    """Raised when a transfer is invalid (e.g., same member, different society)."""
    pass


class NomineeNotEligibleError(ShareTransactionError):
    """Raised when a nominee is not eligible to receive shares."""
    pass


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class ShareLedgerService:
    """Append-only share ledger operations."""

    @staticmethod
    def _last_balance(*, society, member, transaction_date):
        last_entry = (
            ShareLedger.objects.filter(
                society=society,
                member=member,
                transaction_date__lte=transaction_date,
            )
            .order_by("-transaction_date", "-created_at", "-pk")
            .only("balance_after")
            .first()
        )
        return last_entry.balance_after if last_entry is not None else Decimal("0")

    @classmethod
    def _get_performed_by(cls, created_by):
        """
        Return a valid User instance for performed_by.
        If created_by is None, fallback to a system user (first superuser).
        """
        if created_by is not None:
            return created_by
        # Fallback to system user (first superuser)
        User = get_user_model()
        try:
            # Try to get a superuser (should exist in production)
            system_user = User.objects.filter(is_superuser=True).order_by('id').first()
            if system_user:
                return system_user
        except Exception:
            pass
        # Last resort: create a dummy user? Not possible.
        # Raise an error because we cannot log without a user.
        raise ValueError("No user provided and no system user found for event logging.")

    @classmethod
    @transaction.atomic
    def allot_shares(
        cls,
        *,
        society,
        member,
        share_count,
        transaction_date=None,
        reference_id="",
        reason="",
        created_by=None,
        voucher=None,
    ):
        share_count = _as_decimal(share_count)
        if share_count <= 0:
            raise ValueError("share_count must be positive")

        transaction_date = transaction_date or getattr(member, "start_date", None)
        if transaction_date is None:
            from django.utils import timezone

            transaction_date = timezone.localdate()

        balance_before = cls._last_balance(
            society=society, member=member, transaction_date=transaction_date
        )
        ledger_entry = ShareLedger.objects.create(
            society=society,
            member=member,
            shares_in=share_count,
            shares_out=Decimal("0"),
            balance_after=balance_before + share_count,
            transaction_type=ShareLedger.TransactionType.ALLOTMENT,
            transaction_date=transaction_date,
            reference_id=reference_id,
            reason=reason,
            created_by=created_by,
            voucher=voucher,
        )

        # Defer audit logging until the ledger write commits successfully.
        # This keeps a logging failure from poisoning the share transaction.
        def _log_allotment_event():
            try:
                performed_by = cls._get_performed_by(created_by)
                EventLogService.log_allotment_event(
                    society=society,
                    member=member,
                    share_count=share_count,
                    performed_by=performed_by,
                    certificate_number=reference_id,
                    description=reason or f"Allotted {share_count} shares",
                )
            except Exception:
                logger.exception("Failed to log allotment event for ledger entry %s", ledger_entry.id)

        transaction.on_commit(_log_allotment_event)
        
        return ledger_entry

    @classmethod
    @transaction.atomic
    def transfer_shares(
        cls,
        *,
        society,
        from_member,
        to_member,
        share_count,
        transaction_date=None,
        reference_id="",
        reason="",
        created_by=None,
        voucher=None,
    ):
        share_count = _as_decimal(share_count)
        if share_count <= 0:
            raise ValueError("share_count must be positive")

        transaction_date = transaction_date or getattr(from_member, "start_date", None)
        if transaction_date is None:
            from django.utils import timezone

            transaction_date = timezone.localdate()

        from_balance = cls._last_balance(
            society=society, member=from_member, transaction_date=transaction_date
        )
        to_balance = cls._last_balance(
            society=society, member=to_member, transaction_date=transaction_date
        )
        if from_balance < share_count:
            raise ValueError("Insufficient shares for transfer")

        transfer_out = ShareLedger.objects.create(
            society=society,
            member=from_member,
            shares_in=Decimal("0"),
            shares_out=share_count,
            balance_after=from_balance - share_count,
            transaction_type=ShareLedger.TransactionType.TRANSFER_OUT,
            transaction_date=transaction_date,
            reference_id=reference_id,
            reason=reason,
            created_by=created_by,
            voucher=voucher,
        )
        transfer_in = ShareLedger.objects.create(
            society=society,
            member=to_member,
            shares_in=share_count,
            shares_out=Decimal("0"),
            balance_after=to_balance + share_count,
            transaction_type=ShareLedger.TransactionType.TRANSFER_IN,
            transaction_date=transaction_date,
            reference_id=reference_id,
            reason=reason,
            created_by=created_by,
            voucher=voucher,
        )
        
        # Log transfer event
        try:
            performed_by = cls._get_performed_by(created_by)
            EventLogService.log_transfer_event(
                society=society,
                from_member=from_member,
                to_member=to_member,
                share_count=share_count,
                performed_by=performed_by,
                description=reason or f"Transferred {share_count} shares",
            )
        except Exception as e:
            logger.exception("Failed to log transfer event for ledger entries %s, %s", transfer_out.id, transfer_in.id)
        
        return transfer_out, transfer_in

    @classmethod
    @transaction.atomic
    def transmit_shares(
        cls,
        *,
        society,
        member,
        share_count,
        transaction_date=None,
        reference_id="",
        reason="",
        created_by=None,
        voucher=None,
    ):
        share_count = _as_decimal(share_count)
        if share_count <= 0:
            raise ValueError("share_count must be positive")

        transaction_date = transaction_date or getattr(member, "start_date", None)
        if transaction_date is None:
            from django.utils import timezone

            transaction_date = timezone.localdate()

        balance_before = cls._last_balance(
            society=society, member=member, transaction_date=transaction_date
        )
        if balance_before < share_count:
            raise ValueError("Insufficient shares for transmission")

        ledger_entry = ShareLedger.objects.create(
            society=society,
            member=member,
            shares_in=Decimal("0"),
            shares_out=share_count,
            balance_after=balance_before - share_count,
            transaction_type=ShareLedger.TransactionType.TRANSMISSION,
            transaction_date=transaction_date,
            reference_id=reference_id,
            reason=reason,
            created_by=created_by,
            voucher=voucher,
        )
        
        # Log transmission event
        try:
            performed_by = cls._get_performed_by(created_by)
            EventLogService.log_transmission_event(
                society=society,
                member=member,
                share_count=share_count,
                performed_by=performed_by,
                description=reason or f"Transmitted {share_count} shares",
            )
        except Exception as e:
            logger.exception("Failed to log transmission event for ledger entry %s", ledger_entry.id)
        
        return ledger_entry

    @classmethod
    @transaction.atomic
    def adjust_balance(
        cls,
        *,
        society,
        member,
        balance_change,
        transaction_date=None,
        reference_id="",
        reason="",
        created_by=None,
        voucher=None,
    ):
        balance_change = _as_decimal(balance_change)
        if balance_change == 0:
            raise ValueError("balance_change must be non-zero")

        transaction_date = transaction_date or getattr(member, "start_date", None)
        if transaction_date is None:
            from django.utils import timezone

            transaction_date = timezone.localdate()

        balance_before = cls._last_balance(
            society=society, member=member, transaction_date=transaction_date
        )
        balance_after = balance_before + balance_change
        if balance_after < 0:
            raise ValueError("Share balance cannot be negative")

        shares_in = balance_change if balance_change > 0 else Decimal("0")
        shares_out = -balance_change if balance_change < 0 else Decimal("0")
        ledger_entry = ShareLedger.objects.create(
            society=society,
            member=member,
            shares_in=shares_in,
            shares_out=shares_out,
            balance_after=balance_after,
            transaction_type=ShareLedger.TransactionType.ADJUSTMENT,
            transaction_date=transaction_date,
            reference_id=reference_id,
            reason=reason,
            created_by=created_by,
            voucher=voucher,
        )
        
        # Log correction event
        try:
            performed_by = cls._get_performed_by(created_by)
            EventLogService.log_correction_event(
                society=society,
                member=member,
                share_count=abs(balance_change),
                performed_by=performed_by,
                description=reason or f"Adjusted share balance by {balance_change}",
            )
        except Exception as e:
            logger.exception("Failed to log correction event for ledger entry %s", ledger_entry.id)
        
        return ledger_entry


class ShareTransactionService:
    """High-level share transaction service with validation and event emission."""

    @staticmethod
    def validate_share_transfer(from_member: Member, to_member: Member, share_count: Decimal) -> None:
        """
        Validate that a share transfer is possible.
        
        Raises:
            InvalidTransferError: If transfer is invalid.
            InsufficientSharesError: If from_member has insufficient shares.
        """
        if from_member.society_id != to_member.society_id:
            raise InvalidTransferError(
                f"Members belong to different societies: {from_member.society_id} vs {to_member.society_id}"
            )
        if from_member.id == to_member.id:
            raise InvalidTransferError("Cannot transfer shares to same member")
        
        # Check share count positive
        if share_count <= 0:
            raise InvalidTransferError("Share count must be positive")
        
        # Check sufficient shares
        ShareTransactionService.validate_member_has_sufficient_shares(from_member, share_count)

    @staticmethod
    def validate_member_has_sufficient_shares(member: Member, share_count: Decimal) -> None:
        """Validate that member has at least share_count shares."""
        if member.share_balance < share_count:
            raise InsufficientSharesError(
                f"Member {member} has insufficient shares. "
                f"Balance: {member.share_balance}, required: {share_count}"
            )

    @staticmethod
    def validate_nominee_eligibility(nominee: Nominee, share_count: Decimal) -> None:
        """
        Validate that nominee is eligible to receive shares.
        
        Currently checks:
        - Nominee is active
        - Nominee's member is active
        - Share count positive
        """
        if not nominee.is_active:
            raise NomineeNotEligibleError("Nominee is not active")
        if nominee.member.status != Member.MemberStatus.ACTIVE:
            raise NomineeNotEligibleError("Nominee's member is not active")
        if share_count <= 0:
            raise NomineeNotEligibleError("Share count must be positive")

    @staticmethod
    @transaction.atomic
    def allot_shares_to_member(
        member: Member,
        share_count: Decimal,
        transaction_date=None,
        reference: str = "",
        created_by=None,
    ) -> ShareLedger:
        """
        Allot shares to a new member.
        
        Args:
            member: Member receiving shares
            share_count: Number of shares to allot (positive)
            transaction_date: Date of allotment (defaults to today)
            reference: External reference (certificate number, etc.)
            created_by: User who performed the allotment
        
        Returns:
            ShareLedger entry for the allotment
        """
        share_count = _as_decimal(share_count)
        if share_count <= 0:
            raise ValueError("share_count must be positive")
        
        transaction_date = transaction_date or timezone.localdate()
        
        # Determine if automatic voucher generation is enabled
        config = SocietyConfig.objects.get(society=member.society)
        voucher = None
        entrance_fee_voucher = None
        
        if config.auto_generate_vouchers:
            # Create share allotment voucher
            voucher = ShareVoucherService.create_share_allotment_voucher(
                member=member,
                share_count=share_count,
                transaction_date=transaction_date,
            )
            # Create entrance fee voucher if entrance fee > 0
            if config.entrance_fee > 0:
                entrance_fee_voucher = ShareVoucherService.create_entrance_fee_voucher(
                    member=member,
                    amount=config.entrance_fee,
                    transaction_date=transaction_date,
                )
        
        ledger_entry = ShareLedgerService.allot_shares(
            society=member.society,
            member=member,
            share_count=share_count,
            transaction_date=transaction_date,
            reference_id=reference,
            reason="Allotment",
            created_by=created_by,
            voucher=voucher,
        )
        
        logger.info(
            "Shares allotted to member %s: %s shares",
            member.id, share_count
        )
        return ledger_entry

    @staticmethod
    @transaction.atomic
    def transfer_shares(
        from_member: Member,
        to_member: Member,
        share_count: Decimal,
        transaction_date=None,
        reference: str = "",
        created_by=None,
    ) -> tuple[ShareLedger, ShareLedger]:
        """
        Transfer shares between members.
        
        Args:
            from_member: Member transferring shares out
            to_member: Member receiving shares
            share_count: Number of shares to transfer (positive)
            transaction_date: Date of transfer (defaults to today)
            reference: External reference (transfer ID, etc.)
            created_by: User who performed the transfer
        
        Returns:
            Tuple of (transfer_out_entry, transfer_in_entry)
        """
        share_count = _as_decimal(share_count)
        
        # Validate transfer
        ShareTransactionService.validate_share_transfer(from_member, to_member, share_count)
        
        transaction_date = transaction_date or timezone.localdate()
        
        # Determine if automatic voucher generation is enabled
        config = SocietyConfig.objects.get(society=from_member.society)
        voucher = None
        
        if config.auto_generate_vouchers and config.transfer_fee > 0:
            # Calculate transfer fee
            transfer_fee = ShareVoucherService.calculate_transfer_fee(
                member=from_member,
                share_count=share_count,
            )
            
            if transfer_fee > 0:
                # Create share transfer voucher for the fee
                voucher = ShareVoucherService.create_share_transfer_voucher(
                    from_member=from_member,
                    to_member=to_member,
                    share_count=share_count,
                    transaction_date=transaction_date,
                    transfer_fee=transfer_fee,
                )
        
        transfer_out, transfer_in = ShareLedgerService.transfer_shares(
            society=from_member.society,
            from_member=from_member,
            to_member=to_member,
            share_count=share_count,
            transaction_date=transaction_date,
            reference_id=reference,
            reason="Transfer",
            created_by=created_by,
            voucher=voucher,
        )
        
        logger.info(
            "Shares transferred from member %s to %s: %s shares",
            from_member.id, to_member.id, share_count
        )
        return transfer_out, transfer_in

    @staticmethod
    @transaction.atomic
    def transmit_shares(
        deceased_member: Member,
        nominee: Nominee,
        share_count: Decimal,
        transaction_date=None,
        reference: str = "",
        created_by=None,
    ) -> ShareLedger:
        """
        Transmit shares to nominee after member death.
        
        Args:
            deceased_member: Member who has died
            nominee: Nominee receiving shares
            share_count: Number of shares to transmit (positive)
            transaction_date: Date of transmission (defaults to today)
            reference: External reference (death certificate, etc.)
            created_by: User who performed the transmission
        
        Returns:
            ShareLedger entry for the transmission
        """
        share_count = _as_decimal(share_count)
        
        # Validate nominee eligibility
        ShareTransactionService.validate_nominee_eligibility(nominee, share_count)
        
        # Validate deceased member has sufficient shares
        ShareTransactionService.validate_member_has_sufficient_shares(deceased_member, share_count)
        
        transaction_date = transaction_date or timezone.localdate()
        
        # Determine if automatic voucher generation is enabled
        config = SocietyConfig.objects.get(society=deceased_member.society)
        voucher = None
        
        if config.auto_generate_vouchers:
            # Create share transmission voucher (returns None as there's no accounting entry)
            voucher = ShareVoucherService.create_share_transmission_voucher(
                deceased_member=deceased_member,
                nominee=nominee,
                share_count=share_count,
                transaction_date=transaction_date,
            )
        
        transmission_entry = ShareLedgerService.transmit_shares(
            society=deceased_member.society,
            member=deceased_member,
            share_count=share_count,
            transaction_date=transaction_date,
            reference_id=reference,
            reason=f"Transmission to nominee {nominee.id}",
            created_by=created_by,
            voucher=voucher,
        )
        
        logger.info(
            "Shares transmitted from deceased member %s to nominee %s: %s shares",
            deceased_member.id, nominee.id, share_count
        )
        return transmission_entry

    @staticmethod
    @transaction.atomic
    def record_share_correction(
        member: Member,
        new_balance: Decimal,
        transaction_date=None,
        reference: str = "",
        reason: str = "",
        created_by=None,
    ) -> ShareLedger:
        """
        Record a correction to share balance.
        
        This creates an adjustment entry that brings the member's share balance
        to the specified new_balance.
        
        Args:
            member: Member whose balance is being corrected
            new_balance: The corrected total share balance after this transaction
            transaction_date: Date of correction (defaults to today)
            reference: External reference (audit ID, etc.)
            reason: Reason for correction (mandatory)
            created_by: User who performed the correction
        
        Returns:
            ShareLedger entry for the correction
        """
        if not reason:
            raise ValueError("Reason must be provided for share correction")
        
        new_balance = _as_decimal(new_balance)
        transaction_date = transaction_date or timezone.localdate()
        
        # Calculate current balance as of transaction_date
        current_balance = ShareLedgerService._last_balance(
            society=member.society,
            member=member,
            transaction_date=transaction_date,
        )
        
        balance_change = new_balance - current_balance
        
        if balance_change == 0:
            raise ValueError("No correction needed; balance already matches")
        
        correction_entry = ShareLedgerService.adjust_balance(
            society=member.society,
            member=member,
            balance_change=balance_change,
            transaction_date=transaction_date,
            reference_id=reference,
            reason=f"Correction: {reason}",
            created_by=created_by,
            voucher=None,
        )
        
        logger.info(
            "Share correction for member %s: change %s, new balance %s",
            member.id, balance_change, new_balance
        )
        return correction_entry

    @staticmethod
    def get_member_share_history(member: Member) -> list[ShareLedger]:
        """Return all ShareLedger entries for member, ordered by transaction date."""
        return list(ShareLedger.objects.filter(member=member).order_by("transaction_date", "created_at"))

    @staticmethod
    def calculate_member_share_balance(member: Member, as_of_date=None) -> Decimal:
        """
        Calculate member's share balance from ledger (for verification).
        
        If as_of_date is provided, calculates balance up to that date.
        Otherwise, calculates current total balance.
        """
        qs = ShareLedger.objects.filter(member=member)
        if as_of_date:
            qs = qs.filter(transaction_date__lte=as_of_date)
        
        last_entry = qs.order_by("-transaction_date", "-created_at").first()
        if last_entry:
            return last_entry.balance_after
        return Decimal("0")

    @staticmethod
    def generate_share_certificate(
        member: Member,
        share_count: Decimal,
        transaction_date=None,
        certificate_no: str = "",
        issued_by=None,
    ) -> ShareCertificate:
        """
        Create a ShareCertificate for a member.
        
        This is a helper function that creates a certificate record.
        It does NOT affect share ledger.
        
        Args:
            member: Member receiving certificate
            share_count: Number of shares covered by certificate
            transaction_date: Date of issuance (defaults to today)
            certificate_no: Unique certificate number (auto-generated if empty)
            issued_by: User issuing the certificate
        
        Returns:
            Created ShareCertificate instance
        """
        from shares.models import ShareCertificate
        
        if share_count <= 0:
            raise ValueError("share_count must be positive")
        
        transaction_date = transaction_date or timezone.localdate()
        
        if not certificate_no:
            # Generate a simple certificate number (can be improved)
            last_cert = ShareCertificate.objects.filter(
                member__society=member.society
            ).order_by("-issued_date").first()
            if last_cert:
                last_num = int(last_cert.certificate_no.split("-")[-1]) if "-" in last_cert.certificate_no else 0
                certificate_no = f"CERT-{last_num + 1:06d}"
            else:
                certificate_no = f"CERT-000001"
        
        certificate = ShareCertificate.objects.create(
            member=member,
            certificate_no=certificate_no,
            share_count=share_count,
            issued_date=transaction_date,
            status=ShareCertificate.Status.ACTIVE,
            issued_by=issued_by,
        )
        
        logger.info(
            "Share certificate %s generated for member %s covering %s shares",
            certificate_no, member.id, share_count
        )
        
        # Log certificate event
        try:
            EventLogService.log_certificate_event(
                certificate=certificate,
                event_type=EventLog.EventType.SHARE_CERTIFICATE_ISSUED,
                performed_by=ShareLedgerService._get_performed_by(issued_by),
                description=f"Share certificate {certificate_no} issued for {member.get_full_name()}",
                metadata={
                    "certificate_id": certificate.id,
                    "member_id": member.id,
                    "share_count": str(share_count),
                    "issued_date": str(transaction_date),
                }
            )
        except Exception as e:
            # Log but don't fail the certificate creation
            logger.warning("Failed to log certificate event: %s", e)
        
        return certificate


class ShareVoucherService:
    """
    Service for generating accounting vouchers for share transactions.
    Follows double-entry accounting rules and integrates with share ledger.
    """

    @staticmethod
    def _member_display_name(member):
        """Return the best available display name for a member."""
        return getattr(member, "full_name", None) or getattr(member, "name", None) or str(member)

    @staticmethod
    def _nominee_display_name(nominee):
        """Return the best available display name for a nominee."""
        return getattr(nominee, "name", None) or getattr(nominee, "full_name", None) or str(nominee)

    @staticmethod
    def _get_society_config(member):
        """Get society configuration for member's society."""
        return SocietyConfig.objects.get(society=member.society)

    @staticmethod
    def _get_account_mapping(member):
        """Get account mapping for member's society."""
        return AccountMapping.ensure_for_society(member.society)

    @staticmethod
    def _validate_accounting_period(society, voucher_date):
        """Ensure accounting period is open for voucher date."""
        if not AccountingPeriod.is_period_open(society, voucher_date):
            raise ValidationError(
                f"No open accounting period for date {voucher_date}."
            )
        # Also ensure financial year is open
        fy = FinancialYear.get_open_year_for_date(voucher_date, society=society)
        if not fy:
            raise ValidationError(
                f"No open financial year for voucher date {voucher_date}."
            )

    @staticmethod
    def _create_voucher(society, voucher_date, voucher_type, narration, rows, payment_mode="", reference_number=""):
        """
        Internal helper to create and post a voucher.
        """
        with transaction.atomic():
            voucher = Voucher.objects.create(
                society=society,
                voucher_date=voucher_date,
                voucher_type=voucher_type,
                narration=narration,
                payment_mode=payment_mode,
                reference_number=reference_number,
            )
            for row in rows:
                LedgerEntry.objects.create(
                    voucher=voucher,
                    account=row["account"],
                    unit=row.get("unit"),
                    debit=row.get("debit", Decimal("0.00")),
                    credit=row.get("credit", Decimal("0.00")),
                    reference_type=row.get("reference_type", LedgerEntry.ReferenceType.NONE),
                    reference_id=row.get("reference_id", ""),
                )
            voucher.post()
            return voucher

    @staticmethod
    def calculate_share_allotment_amount(member, share_count):
        """
        Calculate total amount based on share value.
        """
        config = SocietyConfig.objects.get(society=member.society)
        share_value = config.share_value
        premium = config.premium_amount
        total = (share_value + premium) * share_count
        return total.quantize(Decimal("0.01"))

    @staticmethod
    def calculate_transfer_fee(member, share_count):
        """
        Calculate transfer fee based on configuration.
        Currently uses flat fee per share from society config.
        """
        config = SocietyConfig.objects.get(society=member.society)
        fee_per_share = config.transfer_fee
        total_fee = fee_per_share * share_count
        return total_fee.quantize(Decimal("0.01"))

    @staticmethod
    def get_voucher_narration(transaction_type, member, share_count):
        """
        Generate descriptive narration for voucher.
        """
        member_name = ShareVoucherService._member_display_name(member)
        if transaction_type == "allotment":
            return f"Share allotment of {share_count} shares to {member_name}"
        elif transaction_type == "transfer":
            return f"Share transfer of {share_count} shares from member {member_name}"
        elif transaction_type == "transmission":
            return f"Share transmission of {share_count} shares to nominee"
        elif transaction_type == "entrance_fee":
            return f"Entrance fee from {member_name}"
        else:
            return f"Share transaction for {member_name}"

    @staticmethod
    def create_share_allotment_voucher(member, share_count, transaction_date):
        """
        Create voucher for share allotment.
        Accounting: Debit Bank/Cash, Credit Share Capital.
        """
        config = ShareVoucherService._get_society_config(member)
        mapping = ShareVoucherService._get_account_mapping(member)
        ShareVoucherService._validate_accounting_period(member.society, transaction_date)

        total_amount = ShareVoucherService.calculate_share_allotment_amount(member, share_count)
        narration = ShareVoucherService.get_voucher_narration("allotment", member, share_count)

        rows = [
            {"account": mapping.bank_account, "debit": total_amount},
            {"account": mapping.share_capital_account, "credit": total_amount},
        ]

        voucher = ShareVoucherService._create_voucher(
            society=member.society,
            voucher_date=transaction_date,
            voucher_type=Voucher.VoucherType.RECEIPT,
            narration=narration,
            rows=rows,
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            reference_number=f"ALLOT-{member.id}-{share_count}",
        )
        return voucher

    @staticmethod
    def create_entrance_fee_voucher(member, amount, transaction_date):
        """
        Create voucher for entrance fee.
        Accounting: Debit Bank/Cash, Credit Entrance Fee Income.
        """
        config = ShareVoucherService._get_society_config(member)
        mapping = ShareVoucherService._get_account_mapping(member)
        ShareVoucherService._validate_accounting_period(member.society, transaction_date)

        narration = ShareVoucherService.get_voucher_narration("entrance_fee", member, Decimal("0"))

        rows = [
            {"account": mapping.bank_account, "debit": amount},
            {"account": mapping.entrance_fee_account, "credit": amount},
        ]

        voucher = ShareVoucherService._create_voucher(
            society=member.society,
            voucher_date=transaction_date,
            voucher_type=Voucher.VoucherType.RECEIPT,
            narration=narration,
            rows=rows,
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            reference_number=f"ENTRANCE-{member.id}",
        )
        return voucher

    @staticmethod
    def create_share_transfer_voucher(from_member, to_member, share_count, transaction_date, transfer_fee):
        """
        Create voucher for share transfer fee (if fee charged).
        Accounting: Debit Transfer Fee Receivable, Credit Transfer Fee Income.
        Note: Share transfer itself is ownership change, no accounting entry.
        Only the transfer fee is accounted.
        """
        if transfer_fee <= 0:
            return None  # No voucher needed if no fee

        config = ShareVoucherService._get_society_config(from_member)
        mapping = ShareVoucherService._get_account_mapping(from_member)
        ShareVoucherService._validate_accounting_period(from_member.society, transaction_date)

        narration = (
            f"Transfer fee for {share_count} shares from "
            f"{ShareVoucherService._member_display_name(from_member)} to "
            f"{ShareVoucherService._member_display_name(to_member)}"
        )

        # Assuming we have a receivable account for transfer fees (maybe member-specific)
        # For simplicity, we'll use the bank account as debit (cash received) and transfer fee income as credit.
        # Actually transfer fee is income, so we debit bank (cash received) and credit transfer fee income.
        rows = [
            {"account": mapping.bank_account, "debit": transfer_fee},
            {"account": mapping.transfer_fee_account, "credit": transfer_fee},
        ]

        voucher = ShareVoucherService._create_voucher(
            society=from_member.society,
            voucher_date=transaction_date,
            voucher_type=Voucher.VoucherType.RECEIPT,
            narration=narration,
            rows=rows,
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            reference_number=f"TRANSFER-{from_member.id}-{to_member.id}",
        )
        return voucher

    @staticmethod
    def create_share_transmission_voucher(deceased_member, nominee, share_count, transaction_date):
        """
        Create voucher for share transmission.
        Typically no accounting entry (ownership change only).
        However, if there is any fee or tax, we could account for it.
        For now, returns None.
        """
        # No accounting entry for pure transmission
        return None


class EventLogService:
    """
    Service for logging share-related events for audit trails.
    """
    
    @staticmethod
    def log_share_event(
        *,
        event_type: EventLog.EventType,
        society,
        performed_by,
        member=None,
        from_member=None,
        to_member=None,
        share_count=None,
        share_value=None,
        certificate_number="",
        nominee=None,
        description="",
        metadata=None,
        ip_address=None,
        user_agent=None,
        timestamp=None,
    ) -> EventLog:
        """
        General function to log any share-related event.
        
        Args:
            event_type: EventLog.EventType enum value
            society: Society instance
            performed_by: User who performed the action
            member: Primary member involved (optional)
            from_member: Member transferring shares (optional)
            to_member: Member receiving shares (optional)
            share_count: Number of shares involved (optional)
            share_value: Value per share (optional)
            certificate_number: Certificate number (optional)
            nominee: Nominee instance (optional)
            description: Human-readable description
            metadata: Additional JSON context (optional)
            ip_address: IP address of request (optional)
            user_agent: User agent of request (optional)
            timestamp: Override timestamp (defaults to now)
        
        Returns:
            EventLog instance
        """
        if metadata is None:
            metadata = {}
        
        event = EventLog.objects.create(
            timestamp=timestamp or timezone.now(),
            event_type=event_type,
            society=society,
            performed_by=performed_by,
            member=member,
            from_member=from_member,
            to_member=to_member,
            share_count=share_count,
            share_value=share_value,
            certificate_number=certificate_number,
            nominee=nominee,
            description=description,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        logger.info(
            "Event logged: %s for member %s (society %s)",
            event_type, member.id if member else "N/A", society.id
        )
        return event
    
    @staticmethod
    def log_allotment_event(
        *,
        society,
        member,
        share_count,
        performed_by,
        certificate_number="",
        share_value=None,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log share allotment event."""
        if not description:
            description = f"Allotted {share_count} shares to {ShareVoucherService._member_display_name(member)}"
        
        return EventLogService.log_share_event(
            event_type=EventLog.EventType.SHARE_ALLOTMENT,
            society=society,
            performed_by=performed_by,
            member=member,
            share_count=share_count,
            share_value=share_value,
            certificate_number=certificate_number,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_transfer_event(
        *,
        society,
        from_member,
        to_member,
        share_count,
        performed_by,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log share transfer event."""
        if not description:
            description = (
                f"Transferred {share_count} shares from "
                f"{ShareVoucherService._member_display_name(from_member)} "
                f"to {ShareVoucherService._member_display_name(to_member)}"
            )
        
        return EventLogService.log_share_event(
            event_type=EventLog.EventType.SHARE_TRANSFER,
            society=society,
            performed_by=performed_by,
            from_member=from_member,
            to_member=to_member,
            share_count=share_count,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_transmission_event(
        *,
        society,
        member,
        share_count,
        performed_by,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log share transmission event."""
        if not description:
            description = (
                f"Transmitted {share_count} shares from "
                f"{ShareVoucherService._member_display_name(member)} (deceased)"
            )
        
        return EventLogService.log_share_event(
            event_type=EventLog.EventType.SHARE_TRANSMISSION,
            society=society,
            performed_by=performed_by,
            member=member,
            share_count=share_count,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_correction_event(
        *,
        society,
        member,
        share_count,
        performed_by,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log share correction event."""
        if not description:
            description = (
                f"Corrected share balance by {share_count} for "
                f"{ShareVoucherService._member_display_name(member)}"
            )
        
        return EventLogService.log_share_event(
            event_type=EventLog.EventType.SHARE_CORRECTION,
            society=society,
            performed_by=performed_by,
            member=member,
            share_count=share_count,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_nominee_event(
        *,
        society,
        member,
        nominee,
        event_type,
        performed_by,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log nominee addition, update, or removal."""
        if not description:
            if event_type == EventLog.EventType.NOMINEE_ADDED:
                description = (
                    f"Added nominee {ShareVoucherService._nominee_display_name(nominee)} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            elif event_type == EventLog.EventType.NOMINEE_UPDATED:
                description = (
                    f"Updated nominee {ShareVoucherService._nominee_display_name(nominee)} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            elif event_type == EventLog.EventType.NOMINEE_REMOVED:
                description = (
                    f"Removed nominee {ShareVoucherService._nominee_display_name(nominee)} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            else:
                description = f"Nominee event for {ShareVoucherService._member_display_name(member)}"
        
        return EventLogService.log_share_event(
            event_type=event_type,
            society=society,
            performed_by=performed_by,
            member=member,
            nominee=nominee,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_certificate_event(
        *,
        society,
        member,
        certificate_number,
        event_type,
        performed_by,
        share_count=None,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log share certificate issuance, cancellation, replacement, or transfer."""
        if not description:
            if event_type == EventLog.EventType.SHARE_CERTIFICATE_ISSUED:
                description = (
                    f"Issued certificate {certificate_number} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            elif event_type == EventLog.EventType.SHARE_CERTIFICATE_CANCELLED:
                description = (
                    f"Cancelled certificate {certificate_number} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            elif event_type == EventLog.EventType.SHARE_CERTIFICATE_REPLACED:
                description = (
                    f"Replaced certificate {certificate_number} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            elif event_type == EventLog.EventType.SHARE_CERTIFICATE_TRANSFERRED:
                description = (
                    f"Transferred certificate {certificate_number} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
            else:
                description = (
                    f"Certificate event {certificate_number} for "
                    f"{ShareVoucherService._member_display_name(member)}"
                )
        
        return EventLogService.log_share_event(
            event_type=event_type,
            society=society,
            performed_by=performed_by,
            member=member,
            certificate_number=certificate_number,
            share_count=share_count,
            description=description,
            **kwargs,
        )
    
    @staticmethod
    def log_member_share_balance_change(
        *,
        society,
        member,
        old_balance,
        new_balance,
        performed_by,
        description="",
        **kwargs,
    ) -> EventLog:
        """Log member share balance change (e.g., after transaction)."""
        if not description:
            description = (
                f"Share balance changed from {old_balance} to {new_balance} "
                f"for {ShareVoucherService._member_display_name(member)}"
            )
        
        return EventLogService.log_share_event(
            event_type=EventLog.EventType.MEMBER_SHARE_BALANCE_CHANGED,
            society=society,
            performed_by=performed_by,
            member=member,
            description=description,
            metadata={
                "old_balance": str(old_balance),
                "new_balance": str(new_balance),
                "difference": str(new_balance - old_balance),
            },
            **kwargs,
        )


__all__ = ["ShareLedgerService", "ShareTransactionService", "ShareVoucherService", "EventLogService"]

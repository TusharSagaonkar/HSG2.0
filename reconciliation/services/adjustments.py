"""
Adjustment Service — Creates adjustment vouchers to resolve reconciliation exceptions.

Handles BANK_ONLY exceptions where a bank transaction exists but there is
no corresponding book entry. Creates a properly balanced Voucher so the
transaction can be reconciled.
"""

import logging
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from accounting.models.model_Account import Account
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Voucher import Voucher

logger = logging.getLogger(__name__)


class AdjustmentService:
    """
    Creates adjustment vouchers to resolve BANK_ONLY exceptions.

    When a bank transaction has no matching book entry (e.g., bank charges ₹17),
    this service creates a properly balanced Voucher so the entry can be reconciled.
    """

    @staticmethod
    def get_default_adjustment_account(society, direction):
        """
        Find a suitable default account for adjustment entries.

        Args:
            society: The Society instance.
            direction: "debit" (we need an EXPENSE account) or "credit" (we need an INCOME account).

        Returns:
            Account instance or None.
        """
        if direction == "debit":
            # The book side needs a debit → expense account
            account = Account.objects.filter(
                society=society,
                sub_type=Account.SubType.EXPENSE,
                is_active=True,
            ).first()
            if not account:
                account = Account.objects.filter(
                    society=society,
                    account_type=Account.AccountType.EXPENSE,
                    is_active=True,
                ).first()
        else:
            # The book side needs a credit → income account
            account = Account.objects.filter(
                society=society,
                sub_type=Account.SubType.INCOME,
                is_active=True,
            ).first()
            if not account:
                account = Account.objects.filter(
                    society=society,
                    account_type=Account.AccountType.INCOME,
                    is_active=True,
                ).first()

        return account

    @classmethod
    def create_adjustment(cls, society, bank_transaction, expense_account=None,
                          income_account=None, user=None):
        """
        Create an adjustment voucher for a bank transaction that has no book entry.

        Logic:
        - If bank transaction is CREDIT (money received) → debit bank account,
          credit income account.
        - If bank transaction is DEBIT (money paid out) → debit expense account,
          credit bank account.

        Args:
            society: The Society instance.
            bank_transaction: The BankTransaction to create an adjustment for.
            expense_account: Optional specific expense account (for DEBIT bank txns).
            income_account: Optional specific income account (for CREDIT bank txns).
            user: The User performing the adjustment.

        Returns:
            The created and posted Voucher instance.
        """
        from reconciliation.models.model_BankTransaction import BankTransaction

        # 1. Find the society's bank account
        bank_account = Account.objects.filter(
            society=society,
            is_bank=True,
            is_active=True,
        ).first()

        if not bank_account:
            raise ValueError(
                f"No active bank account found for society '{society.name}'. "
                f"Please configure a bank account before creating adjustments."
            )

        # 2. Determine direction
        is_credit = bank_transaction.dr_cr == BankTransaction.DrCr.CREDIT

        if is_credit:
            # Bank CREDIT → money came in → debit bank, credit income
            if not income_account:
                income_account = cls.get_default_adjustment_account(society, "credit")
            if not income_account:
                raise ValueError(
                    "No active income account found. "
                    "Please create an income account before creating adjustments."
                )
            debit_account = bank_account
            credit_account = income_account
        else:
            # Bank DEBIT → money went out → debit expense, credit bank
            if not expense_account:
                expense_account = cls.get_default_adjustment_account(society, "debit")
            if not expense_account:
                raise ValueError(
                    "No active expense account found. "
                    "Please create an expense account before creating adjustments."
                )
            debit_account = expense_account
            credit_account = bank_account

        amount = bank_transaction.amount
        narration = (
            f"Bank adjustment: {bank_transaction.narration} "
            f"[{bank_transaction.reference_no or 'N/A'}]"
        )

        # 3. Create the voucher
        voucher = Voucher(
            society=society,
            voucher_type=Voucher.VoucherType.ADJUSTMENT,
            voucher_date=bank_transaction.transaction_date,
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            reference_number=bank_transaction.reference_no or "",
            narration=narration,
        )

        with db_transaction.atomic():
            voucher.save()

            # 4. Create ledger entries (debit + credit must balance)
            LedgerEntry.objects.create(
                voucher=voucher,
                account=debit_account,
                debit=amount,
                credit=Decimal("0.00"),
            )
            LedgerEntry.objects.create(
                voucher=voucher,
                account=credit_account,
                debit=Decimal("0.00"),
                credit=amount,
            )

            # 5. Post the voucher
            voucher.post()

        logger.info(
            "Adjustment voucher %s created for bank tx %s (%s ₹%s) by user %s",
            voucher.display_number,
            bank_transaction.id,
            bank_transaction.dr_cr,
            amount,
            getattr(user, "username", "system"),
        )

        return voucher
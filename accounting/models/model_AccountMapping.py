import logging
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .model_Account import Account
from societies.models import Society

logger = logging.getLogger(__name__)


class AccountMapping(models.Model):
    """
    Maps a society to its key accounting accounts (share capital, fees, bank, etc.)
    Ensures all accounts belong to the same society and have correct account types.
    """
    society = models.OneToOneField(
        Society,
        on_delete=models.CASCADE,
        related_name="account_mapping",
        help_text="Society this mapping belongs to",
    )
    share_capital_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Equity account for share capital",
    )
    entrance_fee_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Income account for entrance fees",
    )
    transfer_fee_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Income account for share transfer fees",
    )
    premium_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Equity account for share premium",
    )
    bank_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Bank account (asset) for primary transactions",
    )

    class Meta:
        app_label = "accounting"
        verbose_name = "Account Mapping"
        verbose_name_plural = "Account Mappings"
        indexes = [
            models.Index(fields=["society"]),
        ]

    def __str__(self):
        return f"Account mapping for {self.society.name}"

    def clean(self):
        """
        Validate that all accounts belong to the same society and have correct types.
        """
        super().clean()
        self._validate_same_society()
        self.validate_account_types()

    def _validate_same_society(self):
        """
        Ensure all referenced accounts belong to the same society as the mapping.
        """
        society = self.society
        fields = [
            ("share_capital_account", self.share_capital_account),
            ("entrance_fee_account", self.entrance_fee_account),
            ("transfer_fee_account", self.transfer_fee_account),
            ("premium_account", self.premium_account),
            ("bank_account", self.bank_account),
        ]
        for field_name, account in fields:
            if account and account.society != society:
                raise ValidationError(
                    _(f"{field_name.replace('_', ' ').title()} must belong to the same society.")
                )

    def validate_account_types(self):
        """
        Validate that each account has the expected account type and sub‑type.
        """
        # Mapping of field name to expected account_type and sub_type
        expected = {
            "share_capital_account": {
                "account_type": Account.AccountType.EQUITY,
                "sub_type": Account.SubType.FUND,
            },
            "entrance_fee_account": {
                "account_type": Account.AccountType.INCOME,
                "sub_type": Account.SubType.INCOME,
            },
            "transfer_fee_account": {
                "account_type": Account.AccountType.INCOME,
                "sub_type": Account.SubType.INCOME,
            },
            "premium_account": {
                "account_type": Account.AccountType.EQUITY,
                "sub_type": Account.SubType.FUND,
            },
            "bank_account": {
                "account_type": Account.AccountType.ASSET,
                "sub_type": Account.SubType.BANK,
                "is_bank": True,
            },
        }

        errors = []
        for field_name, criteria in expected.items():
            account = getattr(self, field_name, None)
            if not account:
                continue
            if account.account_type != criteria["account_type"]:
                errors.append(
                    _(f"{field_name.replace('_', ' ').title()} must be of type {criteria['account_type'].label}.")
                )
            if account.sub_type != criteria["sub_type"]:
                errors.append(
                    _(f"{field_name.replace('_', ' ').title()} must have sub‑type {criteria['sub_type'].label}.")
                )
            if criteria.get("is_bank") and not account.is_bank:
                errors.append(
                    _(f"{field_name.replace('_', ' ').title()} must be marked as a bank account.")
                )

        if errors:
            raise ValidationError(errors)

    @classmethod
    def ensure_for_society(cls, society):
        """
        Ensure an AccountMapping exists for the society, creating a default one if needed.
        Returns the mapping instance.
        """
        try:
            return cls.objects.get(society=society)
        except cls.DoesNotExist:
            # Create default mapping
            from accounting.services.gst_vouchers import AccountCodes

            # Helper to get account by code or create a placeholder
            def get_or_create_account(code, name, **defaults):
                try:
                    return Account.objects.get(society=society, code=code)
                except Account.DoesNotExist:
                    logger.warning(
                        f"Account with code {code} not found for society {society.name}. "
                        f"Creating placeholder account."
                    )
                    # Find a suitable category (default to first equity/income/asset category)
                    from .model_AccountCategory import AccountCategory
                    account_type = defaults.get("account_type", Account.AccountType.EQUITY)
                    category_name = f"Default {account_type.title()}"
                    category, _ = AccountCategory.objects.get_or_create(
                        society=society,
                        name=category_name,
                        account_type=account_type,
                    )

                    account_defaults = {
                        "society": society,
                        "name": name,
                        "code": code,
                        "category": category,
                        "account_type": account_type,
                        "sub_type": defaults.get("sub_type", Account.SubType.GENERAL),
                        "is_bank": defaults.get("is_bank", False),
                        "system_protected": True,
                    }
                    return Account.objects.create(**account_defaults)

            # Map each field to a standard account code
            mapping = {
                "share_capital_account": (AccountCodes.SHARE_CAPITAL, "Share Capital"),
                "entrance_fee_account": (AccountCodes.TRANSFER_FEES, "Entrance Fee"),  # placeholder
                "transfer_fee_account": (AccountCodes.TRANSFER_FEES, "Transfer Fee"),
                "premium_account": (AccountCodes.SHARE_CAPITAL, "Share Premium"),  # placeholder
                "bank_account": (AccountCodes.BANK_MAINTENANCE, "Bank Account"),
            }

            # Build kwargs for creation
            kwargs = {"society": society}
            for field, (code, name) in mapping.items():
                account = get_or_create_account(
                    code,
                    name,
                    account_type={
                        "share_capital_account": Account.AccountType.EQUITY,
                        "entrance_fee_account": Account.AccountType.INCOME,
                        "transfer_fee_account": Account.AccountType.INCOME,
                        "premium_account": Account.AccountType.EQUITY,
                        "bank_account": Account.AccountType.ASSET,
                    }.get(field),
                    sub_type={
                        "share_capital_account": Account.SubType.FUND,
                        "entrance_fee_account": Account.SubType.INCOME,
                        "transfer_fee_account": Account.SubType.INCOME,
                        "premium_account": Account.SubType.FUND,
                        "bank_account": Account.SubType.BANK,
                    }.get(field),
                    is_bank=(field == "bank_account"),
                )
                kwargs[field] = account

            # Create the mapping
            return cls.objects.create(**kwargs)

    @classmethod
    def create_default_for_society(cls, society):
        """
        Create a default AccountMapping for a society (alias for ensure_for_society).
        This method attempts to find suitable accounts by code (using standard account tree)
        and falls back to creating placeholder accounts if needed.
        """
        return cls.ensure_for_society(society)

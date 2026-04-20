from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from accounting.models import VoucherTemplate, VoucherTemplateRow, Account, Voucher
from societies.models import Society
from members.models import Unit


class Command(BaseCommand):
    help = _("Create default voucher templates for societies.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            help=_("Society name (or ID) to seed templates for. If omitted, seed all societies."),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=_("Show what would be created without actually saving."),
        )

    def handle(self, *args, **options):
        society_arg = options.get("society")
        dry_run = options.get("dry_run", False)

        if society_arg:
            try:
                if society_arg.isdigit():
                    societies = Society.objects.filter(id=int(society_arg))
                else:
                    societies = Society.objects.filter(name__icontains=society_arg)
            except ValueError:
                societies = Society.objects.none()
            if not societies.exists():
                self.stderr.write(self.style.ERROR(f"Society '{society_arg}' not found."))
                return
        else:
            societies = Society.objects.all()

        self.stdout.write(
            self.style.SUCCESS(f"Seeding voucher templates for {societies.count()} society(ies).")
        )

        for society in societies:
            self._seed_for_society(society, dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run completed – no changes saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Voucher templates seeded successfully."))

    def _seed_for_society(self, society, dry_run):
        """Create default templates for a single society."""
        # Find commonly used accounts (by name patterns)
        cash_account = Account.objects.filter(
            society=society,
            name__icontains="cash",
            is_active=True,
        ).first()
        bank_account = Account.objects.filter(
            society=society,
            name__icontains="bank",
            is_active=True,
        ).first()
        maintenance_receivable = Account.objects.filter(
            society=society,
            name__icontains="maintenance receivable",
            is_active=True,
        ).first()
        electricity_expense = Account.objects.filter(
            society=society,
            name__icontains="electricity",
            is_active=True,
        ).first()
        depreciation_expense = Account.objects.filter(
            society=society,
            name__icontains="depreciation",
            is_active=True,
        ).first()
        furniture_account = Account.objects.filter(
            society=society,
            name__icontains="furniture",
            is_active=True,
        ).first()

        # If any required account is missing, skip that template
        templates_to_create = []

        # 1. Payment Voucher template
        if electricity_expense and (cash_account or bank_account):
            templates_to_create.append({
                "voucher_type": Voucher.VoucherType.PAYMENT,
                "name": "Electricity Bill Payment",
                "narration": "Paid electricity bill for the month.",
                "payment_mode": Voucher.PaymentMode.CASH,
                "rows": [
                    {
                        "account": electricity_expense,
                        "side": VoucherTemplateRow.Side.DEBIT,
                        "default_amount": None,
                    },
                    {
                        "account": cash_account or bank_account,
                        "side": VoucherTemplateRow.Side.CREDIT,
                        "default_amount": None,
                    },
                ],
            })

        # 2. Receipt Voucher template
        if maintenance_receivable and (cash_account or bank_account):
            templates_to_create.append({
                "voucher_type": Voucher.VoucherType.RECEIPT,
                "name": "Maintenance Collection",
                "narration": "Maintenance collected from member.",
                "payment_mode": Voucher.PaymentMode.CASH,
                "rows": [
                    {
                        "account": cash_account or bank_account,
                        "side": VoucherTemplateRow.Side.DEBIT,
                        "default_amount": None,
                    },
                    {
                        "account": maintenance_receivable,
                        "side": VoucherTemplateRow.Side.CREDIT,
                        "default_amount": None,
                    },
                ],
            })

        # 3. Contra Voucher template (cash ↔ bank)
        if cash_account and bank_account:
            templates_to_create.append({
                "voucher_type": Voucher.VoucherType.GENERAL,
                "name": "Cash to Bank Transfer",
                "narration": "Deposited cash into bank.",
                "payment_mode": "",
                "rows": [
                    {
                        "account": bank_account,
                        "side": VoucherTemplateRow.Side.DEBIT,
                        "default_amount": None,
                    },
                    {
                        "account": cash_account,
                        "side": VoucherTemplateRow.Side.CREDIT,
                        "default_amount": None,
                    },
                ],
            })

        # 4. Journal Voucher template (depreciation)
        if depreciation_expense and furniture_account:
            templates_to_create.append({
                "voucher_type": Voucher.VoucherType.JOURNAL,
                "name": "Monthly Depreciation",
                "narration": "Depreciation on furniture.",
                "payment_mode": "",
                "rows": [
                    {
                        "account": depreciation_expense,
                        "side": VoucherTemplateRow.Side.DEBIT,
                        "default_amount": None,
                    },
                    {
                        "account": furniture_account,
                        "side": VoucherTemplateRow.Side.CREDIT,
                        "default_amount": None,
                    },
                ],
            })

        with transaction.atomic():
            for template_data in templates_to_create:
                voucher_type = template_data["voucher_type"]
                # Avoid creating duplicate seeded templates with the same purpose.
                existing = VoucherTemplate.objects.filter(
                    society=society,
                    voucher_type=voucher_type,
                    name=template_data["name"],
                ).exists()
                if existing:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Template "{template_data["name"]}" already exists in {society.name}. Skipping.'
                        )
                    )
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'[DRY RUN] Would create "{template_data["name"]}" for {society.name}.'
                        )
                    )
                    continue

                # Create the template
                template = VoucherTemplate.objects.create(
                    society=society,
                    voucher_type=voucher_type,
                    name=template_data["name"],
                    narration=template_data["narration"],
                    payment_mode=template_data.get("payment_mode", ""),
                    reference_number_pattern="",
                    is_active=True,
                )

                # Create rows
                for row_data in template_data["rows"]:
                    VoucherTemplateRow.objects.create(
                        template=template,
                        account=row_data["account"],
                        unit=None,
                        side=row_data["side"],
                        default_amount=row_data.get("default_amount"),
                        order=template.rows.count() + 1,
                    )

                self.stdout.write(
                    self.style.SUCCESS(
                        f'Created "{template_data["name"]}" for {society.name}.'
                    )
                )

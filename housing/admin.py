from django.contrib import admin
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.shortcuts import render
from decimal import Decimal
from .models import Society, Structure, Unit
from .models import Member, ChargeTemplate, Bill, BillLine, PaymentReceipt, ReceiptAllocation, ReminderLog
from members.models.model_Nominee import Nominee
from shares.models import ShareLedger, ShareCertificate
from shares.services import ShareLedgerService, ShareTransactionService
from accounting.models.model_AccountMapping import AccountMapping
from societies.models.model_SocietyConfig import SocietyConfig
############ SOCIETY, STRUCTURE, UNIT ##############


class SocietyConfigInline(admin.StackedInline):
    model = SocietyConfig
    can_delete = False
    extra = 0
    max_num = 1
    verbose_name = "Share Configuration"
    verbose_name_plural = "Share Configuration"
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "share_value",
                    "default_share_count",
                )
            },
        ),
        (
            "Fees",
            {
                "fields": (
                    "entrance_fee",
                    "transfer_fee",
                    "premium_amount",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Settings",
            {
                "fields": (
                    "allow_multiple_nominees",
                    "require_approval",
                    "auto_generate_vouchers",
                ),
                "classes": ("collapse",),
            },
        ),
    )


class AccountMappingInline(admin.StackedInline):
    model = AccountMapping
    can_delete = False
    extra = 0
    max_num = 1
    verbose_name = "Account Mapping"
    verbose_name_plural = "Account Mapping"
    fieldsets = (
        (None, {"fields": ("society",)}),
        (
            "Share Accounts",
            {
                "fields": (
                    "share_capital_account",
                    "premium_account",
                )
            },
        ),
        (
            "Fee Accounts",
            {
                "fields": (
                    "entrance_fee_account",
                    "transfer_fee_account",
                )
            },
        ),
        ("Bank Account", {"fields": ("bank_account",)}),
    )

@admin.register(Society)
class SocietyAdmin(admin.ModelAdmin):
    list_display = ("name", "registration_number", "created_at")
    search_fields = ("name",)
    inlines = [SocietyConfigInline, AccountMappingInline]


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "structure_type",
        "society",
        "parent",
        "display_order",
    )
    list_filter = ("society", "structure_type")
    search_fields = ("name",)
    ordering = ("society", "display_order")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = (
        "identifier",
        "unit_type",
        "structure",
        "is_active",
    )
    list_filter = ("unit_type", "is_active", "structure__society")
    search_fields = ("identifier",)



############ OWNERSHIP, OCCUPANCY ##############
from .models import UnitOwnership, UnitOccupancy

@admin.register(UnitOwnership)
class UnitOwnershipAdmin(admin.ModelAdmin):
    list_display = ("unit", "owner", "start_date", "end_date")
    list_filter = ("unit__structure__society",)
    search_fields = ("unit__identifier", "owner__username")


@admin.register(UnitOccupancy)
class UnitOccupancyAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "occupancy_type",
        "occupant",
        "start_date",
        "end_date",
    )
    list_filter = ("occupancy_type", "unit__structure__society")
    search_fields = ("unit__identifier",)


from django.contrib import admin
from django.utils.translation import gettext_lazy as _


class ShareBalanceFilter(admin.SimpleListFilter):
    """Filter members by share balance ranges."""
    title = _('Share balance')
    parameter_name = 'share_balance'

    def lookups(self, request, model_admin):
        return (
            ('0', _('Zero')),
            ('1-10', _('1-10 shares')),
            ('11-100', _('11-100 shares')),
            ('101+', _('101+ shares')),
        )

    def queryset(self, request, queryset):
        if self.value() == '0':
            return queryset.filter(share_balance=0)
        if self.value() == '1-10':
            return queryset.filter(share_balance__gte=1, share_balance__lte=10)
        if self.value() == '11-100':
            return queryset.filter(share_balance__gte=11, share_balance__lte=100)
        if self.value() == '101+':
            return queryset.filter(share_balance__gte=101)
        return queryset


class NomineeInline(admin.TabularInline):
    """Inline for Nominee records."""
    model = Nominee
    extra = 0
    fields = ("name", "relationship", "percentage", "priority_order", "is_active")
    readonly_fields = ("created_at", "deactivated_at")
    can_delete = True
    ordering = ("priority_order",)


class ShareLedgerInline(admin.TabularInline):
    """Readonly inline for ShareLedger entries."""
    model = ShareLedger
    extra = 0
    fields = ("transaction_type", "shares_in", "shares_out", "balance_after", "transaction_date", "reference_id")
    readonly_fields = fields
    can_delete = False
    ordering = ("-transaction_date", "-created_at")
    max_num = 10  # Show only recent entries

    def has_add_permission(self, request, obj):
        return False


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "society",
        "unit",
        "role",
        "status",
        "share_balance",
        "join_date",
        "exit_date",
        "total_share_value",
    )
    list_filter = (
        "society",
        "role",
        "status",
        ShareBalanceFilter,
    )
    search_fields = ("full_name", "email", "phone", "unit__identifier")
    readonly_fields = (
        "share_balance",
        "total_share_value",
        "active_nominees",
        "is_active_member",
    )
    ordering = ("-share_balance", "full_name")
    inlines = [NomineeInline, ShareLedgerInline]

    fieldsets = (
        (None, {
            "fields": (
                "society",
                "unit",
                "full_name",
                "email",
                "phone",
                "role",
                "status",
            )
        }),
        ("Share Information", {
            "fields": (
                "share_balance",
                "join_date",
                "exit_date",
                "total_share_value",
                "active_nominees",
                "is_active_member",
            ),
            "classes": ("collapse",),
        }),
        ("Accounting", {
            "fields": ("receivable_account",),
            "classes": ("collapse",),
        }),
    )

    actions = [
        "allot_shares_to_members",
        "transfer_shares_between_members",
        "generate_share_certificates",
        "validate_nominee_percentages",
    ]

    @admin.action(description="Allot shares to selected members")
    def allot_shares_to_members(self, request, queryset):
        """Allot shares to selected members."""
        from django import forms
        
        class AllotSharesForm(forms.Form):
            share_count = forms.DecimalField(
                label=_("Number of shares to allot"),
                min_value=Decimal("0.01"),
                max_digits=10,
                decimal_places=2,
                help_text=_("Enter the number of shares to allot to each selected member")
            )
            transaction_date = forms.DateField(
                label=_("Transaction date"),
                help_text=_("Date when shares are allotted")
            )
            narration = forms.CharField(
                label=_("Narration"),
                max_length=255,
                required=False,
                help_text=_("Optional description for the transaction")
            )
        
        if 'apply' in request.POST:
            form = AllotSharesForm(request.POST)
            if form.is_valid():
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                narration = form.cleaned_data['narration']
                
                success_count = 0
                error_count = 0
                
                for member in queryset:
                    try:
                        ShareTransactionService.allot_shares_to_member(
                            member=member,
                            share_count=share_count,
                            transaction_date=transaction_date,
                            narration=narration
                        )
                        success_count += 1
                    except Exception as e:
                        self.message_user(
                            request,
                            _("Failed to allot shares to %(member)s: %(error)s") % {
                                'member': member.full_name,
                                'error': str(e)
                            },
                            level=messages.ERROR
                        )
                        error_count += 1
                
                if success_count > 0:
                    self.message_user(
                        request,
                        _("Successfully allotted %(count)s shares to %(num)s members") % {
                            'count': share_count,
                            'num': success_count
                        },
                        level=messages.SUCCESS
                    )
                if error_count > 0:
                    self.message_user(
                        request,
                        _("Failed to allot shares to %(num)s members") % {'num': error_count},
                        level=messages.WARNING
                    )
                return
        
        else:
            form = AllotSharesForm(initial={
                'transaction_date': timezone.now().date(),
                'share_count': Decimal("1.00"),
            })
        
        return render(request, 'admin/share_allotment_form.html', {
            'form': form,
            'members': queryset,
            'action': 'allot_shares_to_members',
            'opts': self.model._meta,
        })

    @admin.action(description="Transfer shares between members")
    def transfer_shares_between_members(self, request, queryset):
        """Transfer shares from selected members to another member."""
        from django import forms
        
        if queryset.count() != 1:
            self.message_user(
                request,
                _("Please select exactly one member to transfer shares FROM"),
                level=messages.ERROR
            )
            return
        
        from_member = queryset.first()
        
        class TransferSharesForm(forms.Form):
            to_member = forms.ModelChoiceField(
                label=_("Transfer shares TO"),
                queryset=Member.objects.filter(society=from_member.society).exclude(id=from_member.id),
                help_text=_("Select the member who will receive the shares")
            )
            share_count = forms.DecimalField(
                label=_("Number of shares to transfer"),
                min_value=Decimal("0.01"),
                max_digits=10,
                decimal_places=2,
                help_text=_("Enter the number of shares to transfer")
            )
            transaction_date = forms.DateField(
                label=_("Transaction date"),
                help_text=_("Date when shares are transferred")
            )
            narration = forms.CharField(
                label=_("Narration"),
                max_length=255,
                required=False,
                help_text=_("Optional description for the transaction")
            )
        
        if 'apply' in request.POST:
            form = TransferSharesForm(request.POST)
            if form.is_valid():
                to_member = form.cleaned_data['to_member']
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                narration = form.cleaned_data['narration']
                
                try:
                    ShareTransactionService.transfer_shares(
                        from_member=from_member,
                        to_member=to_member,
                        share_count=share_count,
                        transaction_date=transaction_date,
                        narration=narration
                    )
                    self.message_user(
                        request,
                        _("Successfully transferred %(count)s shares from %(from)s to %(to)s") % {
                            'count': share_count,
                            'from': from_member.full_name,
                            'to': to_member.full_name
                        },
                        level=messages.SUCCESS
                    )
                except Exception as e:
                    self.message_user(
                        request,
                        _("Failed to transfer shares: %(error)s") % {'error': str(e)},
                        level=messages.ERROR
                    )
                return
        
        else:
            form = TransferSharesForm(initial={
                'transaction_date': timezone.now().date(),
                'share_count': Decimal("1.00"),
            })
        
        return render(request, 'admin/share_transfer_form.html', {
            'form': form,
            'from_member': from_member,
            'action': 'transfer_shares_between_members',
            'opts': self.model._meta,
        })

    @admin.action(description="Generate share certificates")
    def generate_share_certificates(self, request, queryset):
        """Generate share certificates for selected members."""
        success_count = 0
        error_count = 0
        
        for member in queryset:
            try:
                # Generate certificate for current share balance
                certificate = ShareCertificate.objects.create(
                    member=member,
                    society=member.society,
                    certificate_number=f"CERT-{member.id}-{int(timezone.now().timestamp())}",
                    share_count=member.share_balance,
                    issue_date=timezone.now().date(),
                    status=ShareCertificate.Status.ACTIVE,
                    remarks=f"Generated via admin action for {member.full_name}"
                )
                success_count += 1
            except Exception as e:
                self.message_user(
                    request,
                    _("Failed to generate certificate for %(member)s: %(error)s") % {
                        'member': member.full_name,
                        'error': str(e)
                    },
                    level=messages.ERROR
                )
                error_count += 1
        
        if success_count > 0:
            self.message_user(
                request,
                _("Successfully generated %(num)s share certificates") % {'num': success_count},
                level=messages.SUCCESS
            )
        if error_count > 0:
            self.message_user(
                request,
                _("Failed to generate %(num)s share certificates") % {'num': error_count},
                level=messages.WARNING
            )

    @admin.action(description="Validate nominee percentages")
    def validate_nominee_percentages(self, request, queryset):
        """Validate that nominee percentages sum to 100% for selected members."""
        from members.models.model_Nominee import Nominee
        
        invalid_members = []
        
        for member in queryset:
            active_nominees = Nominee.objects.filter(member=member, is_active=True)
            total_percentage = sum(nominee.percentage for nominee in active_nominees)
            
            if active_nominees.exists() and total_percentage != 100:
                invalid_members.append({
                    'member': member,
                    'total_percentage': total_percentage,
                    'nominee_count': active_nominees.count()
                })
        
        if invalid_members:
            message_lines = [_("The following members have invalid nominee percentages:")]
            for item in invalid_members:
                message_lines.append(
                    _("- %(member)s: %(count)s nominees, total percentage = %(percent)s%% (should be 100%%)") % {
                        'member': item['member'].full_name,
                        'count': item['nominee_count'],
                        'percent': item['total_percentage']
                    }
                )
            self.message_user(
                request,
                "\n".join(message_lines),
                level=messages.ERROR
            )
        else:
            self.message_user(
                request,
                _("All selected members have valid nominee percentages (sum to 100%%)"),
                level=messages.SUCCESS
            )


@admin.register(ChargeTemplate)
class ChargeTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "version_no",
        "society",
        "charge_type",
        "rate",
        "frequency",
        "effective_from",
        "effective_to",
        "is_active",
    )
    list_filter = ("society", "charge_type", "frequency", "is_active")
    search_fields = ("name",)
    readonly_fields = ("version_no",)


class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 0


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("bill_number", "society", "member", "bill_date", "due_date", "total_amount", "status")
    list_filter = ("society", "status")
    search_fields = ("member__full_name", "unit__identifier")
    inlines = [BillLineInline]


class ReceiptAllocationInline(admin.TabularInline):
    model = ReceiptAllocation
    extra = 0


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "society", "member", "receipt_date", "amount", "payment_mode", "status")
    list_filter = ("society", "payment_mode", "status")
    search_fields = ("member__full_name", "reference_number")
    inlines = [ReceiptAllocationInline]


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ("member", "bill", "channel", "status", "scheduled_for", "created_at")
    list_filter = ("society", "channel", "status")
    search_fields = ("member__full_name", "bill__bill_number")

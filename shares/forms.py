"""
Forms for share management operations.
"""
from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from members.models.model_Member import Member
from members.models.model_Nominee import Nominee
from shares.models import ShareCertificate
from accounting.models import Account
from accounting.models import AccountMapping
from societies.models.model_SocietyConfig import SocietyConfig


class ShareAllotmentForm(forms.Form):
    """Form for allotting shares to a member."""
    
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("Member"),
        help_text=_("Select member to allot shares to.")
    )
    share_count = forms.DecimalField(
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
        label=_("Number of Shares"),
        help_text=_("Number of shares to allot.")
    )
    transaction_date = forms.DateField(
        label=_("Transaction Date"),
        help_text=_("Date when shares are allotted."),
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    reason = forms.CharField(
        required=False,
        max_length=500,
        label=_("Reason"),
        help_text=_("Optional reason for allotment."),
        widget=forms.Textarea(attrs={'rows': 2})
    )
    reference_id = forms.CharField(
        required=False,
        max_length=100,
        label=_("Reference ID"),
        help_text=_("Optional external reference (certificate number, etc.).")
    )
    issue_certificate = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Issue Share Certificate"),
        help_text=_("Create a share certificate for this allotment.")
    )
    certificate_no = forms.CharField(
        required=False,
        max_length=50,
        label=_("Certificate Number"),
        help_text=_("Leave blank to auto-generate.")
    )
    
    def __init__(self, *args, **kwargs):
        # Remove instance parameter if passed (for compatibility with CreateView)
        kwargs.pop('instance', None)
        self.society = kwargs.pop('society', None)
        super().__init__(*args, **kwargs)
        
        if self.society:
            self.fields['member'].queryset = Member.objects.filter(
                society=self.society
            ).order_by('full_name')
        
        # Set default date to today
        from django.utils import timezone
        self.fields['transaction_date'].initial = timezone.localdate()
    
    def clean(self):
        cleaned_data = super().clean()
        member = cleaned_data.get('member')
        share_count = cleaned_data.get('share_count')
        issue_certificate = cleaned_data.get('issue_certificate', False)
        certificate_no = cleaned_data.get('certificate_no', '')
        
        if member and self.society and member.society != self.society:
            raise ValidationError(_("Selected member does not belong to the current society."))
        
        if issue_certificate and certificate_no:
            # Check if certificate number already exists
            if ShareCertificate.objects.filter(certificate_no=certificate_no).exists():
                raise ValidationError(_("Certificate number already exists."))
        
        return cleaned_data


class SocietyRulesForm(forms.ModelForm):
    share_capital_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        label=_("Share Capital Account"),
    )
    entrance_fee_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        label=_("Entrance Fee Account"),
    )
    transfer_fee_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        label=_("Transfer Fee Account"),
    )
    premium_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        label=_("Premium Account"),
    )
    bank_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        label=_("Bank Account"),
    )

    class Meta:
        model = SocietyConfig
        fields = [
            "share_value",
            "default_share_count",
            "entrance_fee",
            "transfer_fee",
            "premium_amount",
            "allow_multiple_nominees",
            "require_approval",
            "auto_generate_vouchers",
        ]
        widgets = {
            "share_value": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "default_share_count": forms.NumberInput(attrs={"min": "1"}),
            "entrance_fee": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "transfer_fee": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "premium_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, society=None, **kwargs):
        self.society = society
        super().__init__(*args, **kwargs)

        if self.society:
            # Mapping of field name to expected account_type and sub_type
            field_criteria = {
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

            for field_name, criteria in field_criteria.items():
                qs = Account.objects.filter(society=self.society)
                qs = qs.filter(account_type=criteria["account_type"], sub_type=criteria["sub_type"])
                if criteria.get("is_bank"):
                    qs = qs.filter(is_bank=True)
                qs = qs.only("id", "name", "society_id").order_by("name")
                self.fields[field_name].queryset = qs

            mapping = getattr(self.society, "account_mapping", None)
            if mapping:
                self.fields["share_capital_account"].initial = mapping.share_capital_account_id
                self.fields["entrance_fee_account"].initial = mapping.entrance_fee_account_id
                self.fields["transfer_fee_account"].initial = mapping.transfer_fee_account_id
                self.fields["premium_account"].initial = mapping.premium_account_id
                self.fields["bank_account"].initial = mapping.bank_account_id

    def clean_default_share_count(self):
        value = self.cleaned_data["default_share_count"]
        if value <= 0:
            raise ValidationError(_("Default share count must be positive."))
        return value

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if self.society:
            mapping, _ = AccountMapping.objects.get_or_create(
                society=self.society,
                defaults={
                    "share_capital_account": self.cleaned_data.get("share_capital_account"),
                    "entrance_fee_account": self.cleaned_data.get("entrance_fee_account"),
                    "transfer_fee_account": self.cleaned_data.get("transfer_fee_account"),
                    "premium_account": self.cleaned_data.get("premium_account"),
                    "bank_account": self.cleaned_data.get("bank_account"),
                },
            )
            mapping.share_capital_account = self.cleaned_data.get("share_capital_account")
            mapping.entrance_fee_account = self.cleaned_data.get("entrance_fee_account")
            mapping.transfer_fee_account = self.cleaned_data.get("transfer_fee_account")
            mapping.premium_account = self.cleaned_data.get("premium_account")
            mapping.bank_account = self.cleaned_data.get("bank_account")
            mapping.full_clean()
            mapping.save()
        return instance


class ShareTransferForm(forms.Form):
    """Form for transferring shares between members."""
    
    from_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("From Member"),
        help_text=_("Member transferring shares.")
    )
    to_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("To Member"),
        help_text=_("Member receiving shares.")
    )
    share_count = forms.DecimalField(
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
        label=_("Number of Shares"),
        help_text=_("Number of shares to transfer.")
    )
    transaction_date = forms.DateField(
        label=_("Transaction Date"),
        help_text=_("Date when transfer occurs."),
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    reason = forms.CharField(
        required=False,
        max_length=500,
        label=_("Reason"),
        help_text=_("Optional reason for transfer."),
        widget=forms.Textarea(attrs={'rows': 2})
    )
    reference_id = forms.CharField(
        required=False,
        max_length=100,
        label=_("Reference ID"),
        help_text=_("Optional external reference.")
    )
    
    def __init__(self, *args, **kwargs):
        # Remove instance parameter if passed (for compatibility with CreateView)
        kwargs.pop('instance', None)
        self.society = kwargs.pop('society', None)
        super().__init__(*args, **kwargs)
        
        if self.society:
            member_qs = Member.objects.filter(society=self.society).order_by('full_name')
            self.fields['from_member'].queryset = member_qs
            self.fields['to_member'].queryset = member_qs
        
        # Set default date to today
        from django.utils import timezone
        self.fields['transaction_date'].initial = timezone.localdate()
    
    def clean(self):
        cleaned_data = super().clean()
        from_member = cleaned_data.get('from_member')
        to_member = cleaned_data.get('to_member')
        share_count = cleaned_data.get('share_count')
        
        if from_member and to_member:
            if from_member == to_member:
                raise ValidationError(_("Cannot transfer shares to the same member."))
            
            if from_member.society != to_member.society:
                raise ValidationError(_("Both members must belong to the same society."))
            
            # Check sufficient shares
            if share_count and from_member.share_balance < share_count:
                raise ValidationError(
                    _("Insufficient shares. Available: %(available)s, Requested: %(requested)s") % {
                        'available': from_member.share_balance,
                        'requested': share_count
                    }
                )
        
        return cleaned_data


class ShareTransmissionForm(forms.Form):
    """Form for transmitting shares to nominee."""
    
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("Member"),
        help_text=_("Member whose shares are being transmitted.")
    )
    nominee = forms.ModelChoiceField(
        queryset=Nominee.objects.none(),
        label=_("Nominee"),
        help_text=_("Nominee receiving shares.")
    )
    share_count = forms.DecimalField(
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
        label=_("Number of Shares"),
        help_text=_("Number of shares to transmit.")
    )
    transaction_date = forms.DateField(
        label=_("Transaction Date"),
        help_text=_("Date when transmission occurs."),
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    reason = forms.CharField(
        required=False,
        max_length=500,
        label=_("Reason"),
        help_text=_("Reason for transmission (e.g., death, incapacitation)."),
        widget=forms.Textarea(attrs={'rows': 2})
    )
    reference_id = forms.CharField(
        required=False,
        max_length=100,
        label=_("Reference ID"),
        help_text=_("Optional external reference.")
    )
    
    def __init__(self, *args, **kwargs):
        # Remove instance parameter if passed (for compatibility with CreateView)
        kwargs.pop('instance', None)
        self.society = kwargs.pop('society', None)
        super().__init__(*args, **kwargs)
        
        if self.society:
            self.fields['member'].queryset = Member.objects.filter(
                society=self.society
            ).order_by('full_name')
            
            # Get nominees for members in this society
            self.fields['nominee'].queryset = Nominee.objects.filter(
                member__society=self.society,
                is_active=True
            ).select_related('member').order_by('member__full_name', 'priority_order')
        
        # Set default date to today
        from django.utils import timezone
        self.fields['transaction_date'].initial = timezone.localdate()
    
    def clean(self):
        cleaned_data = super().clean()
        member = cleaned_data.get('member')
        nominee = cleaned_data.get('nominee')
        share_count = cleaned_data.get('share_count')
        
        if member and nominee:
            if nominee.member != member:
                raise ValidationError(_("Selected nominee does not belong to the selected member."))
            
            # Check sufficient shares
            if share_count and member.share_balance < share_count:
                raise ValidationError(
                    _("Insufficient shares. Available: %(available)s, Requested: %(requested)s") % {
                        'available': member.share_balance,
                        'requested': share_count
                    }
                )
        
        return cleaned_data


class ShareCorrectionForm(forms.Form):
    """Form for recording share corrections."""
    
    ADJUSTMENT_CHOICES = [
        ('ADD', _("Add Shares")),
        ('DEDUCT', _("Deduct Shares")),
    ]
    
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("Member"),
        help_text=_("Member whose shares are being corrected.")
    )
    adjustment_type = forms.ChoiceField(
        choices=ADJUSTMENT_CHOICES,
        label=_("Adjustment Type"),
        help_text=_("Whether to add or deduct shares.")
    )
    share_count = forms.DecimalField(
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
        label=_("Number of Shares"),
        help_text=_("Number of shares to add or deduct.")
    )
    transaction_date = forms.DateField(
        label=_("Transaction Date"),
        help_text=_("Date when correction occurs."),
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    reason = forms.CharField(
        required=True,
        max_length=500,
        label=_("Reason"),
        help_text=_("Detailed reason for correction (required)."),
        widget=forms.Textarea(attrs={'rows': 3})
    )
    reference_id = forms.CharField(
        required=False,
        max_length=100,
        label=_("Reference ID"),
        help_text=_("Optional external reference (document number, etc.).")
    )
    
    def __init__(self, *args, **kwargs):
        # Remove instance parameter if passed (for compatibility with CreateView)
        kwargs.pop('instance', None)
        self.society = kwargs.pop('society', None)
        super().__init__(*args, **kwargs)
        
        if self.society:
            self.fields['member'].queryset = Member.objects.filter(
                society=self.society
            ).order_by('full_name')
        
        # Set default date to today
        from django.utils import timezone
        self.fields['transaction_date'].initial = timezone.localdate()
    
    def clean(self):
        cleaned_data = super().clean()
        member = cleaned_data.get('member')
        adjustment_type = cleaned_data.get('adjustment_type')
        share_count = cleaned_data.get('share_count')
        
        if member and adjustment_type == 'DEDUCT' and share_count:
            if member.share_balance < share_count:
                raise ValidationError(
                    _("Cannot deduct more shares than member has. Available: %(available)s") % {
                        'available': member.share_balance
                    }
                )
        
        return cleaned_data

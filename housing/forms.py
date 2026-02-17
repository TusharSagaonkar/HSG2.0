from django import forms
from django.utils.translation import gettext_lazy as _
from decimal import Decimal

from societies.models import Society
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from billing.models import Bill
from billing.models import ChargeTemplate
from accounting.models import Account


class BootstrapModelForm(forms.ModelForm):
    """Apply bootstrap-friendly widgets for all model forms."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css_class = "form-control"
            if isinstance(widget, forms.CheckboxInput):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


class SocietyForm(BootstrapModelForm):
    class Meta:
        model = Society
        fields = ["name", "registration_number", "address"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }


class StructureForm(BootstrapModelForm):
    class Meta:
        model = Structure
        fields = ["society", "parent", "structure_type", "name", "display_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["parent"].queryset = Structure.objects.filter(
                society_id=society_id
            )


class UnitForm(BootstrapModelForm):
    class Meta:
        model = Unit
        fields = ["structure", "unit_type", "identifier", "area_sqft", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["structure"].queryset = Structure.objects.filter(
                society_id=society_id
            )
        self.fields["area_sqft"].help_text = _("Can be greater than 300 sq ft.")


class UnitOwnershipForm(BootstrapModelForm):
    class Meta:
        model = UnitOwnership
        fields = ["unit", "owner", "role", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )


class UnitOccupancyForm(BootstrapModelForm):
    class Meta:
        model = UnitOccupancy
        fields = ["unit", "occupant", "occupancy_type", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )


class MemberForm(BootstrapModelForm):
    class Meta:
        model = Member
        fields = [
            "society",
            "unit",
            "user",
            "full_name",
            "email",
            "phone",
            "role",
            "status",
            "receivable_account",
            "start_date",
            "end_date",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )
            self.fields["receivable_account"].queryset = Account.objects.filter(
                society_id=society_id,
            ).order_by("name")


class ChargeTemplateForm(BootstrapModelForm):
    class Meta:
        model = ChargeTemplate
        fields = [
            "society",
            "name",
            "description",
            "amount",
            "frequency",
            "due_days",
            "late_fee_percent",
            "income_account",
            "receivable_account",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["income_account"].queryset = Account.objects.filter(
                society_id=society_id,
                category__account_type="INCOME",
            ).order_by("name")
            self.fields["receivable_account"].queryset = Account.objects.filter(
                society_id=society_id,
                category__account_type="ASSET",
            ).order_by("name")


class BillingGenerationForm(forms.Form):
    society = forms.ModelChoiceField(queryset=Society.objects.all())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    bill_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        period_start = cleaned.get("period_start")
        period_end = cleaned.get("period_end")
        bill_date = cleaned.get("bill_date")
        if period_start and period_end and period_end < period_start:
            raise forms.ValidationError("Period end date cannot be before period start date.")
        if period_start and bill_date and bill_date < period_start:
            raise forms.ValidationError("Bill date cannot be before period start date.")
        return cleaned


class ReceiptPostingForm(forms.Form):
    society = forms.ModelChoiceField(queryset=Society.objects.all())
    member = forms.ModelChoiceField(queryset=Member.objects.none())
    bill = forms.ModelChoiceField(queryset=Bill.objects.none())
    receipt_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    payment_mode = forms.ChoiceField(
        choices=(
            ("CASH", "Cash"),
            ("BANK_TRANSFER", "Bank Transfer"),
            ("CHEQUE", "Cheque"),
            ("UPI", "UPI"),
            ("CARD", "Card"),
            ("OTHER", "Other"),
        )
    )
    reference_number = forms.CharField(required=False)
    deposited_account = forms.ModelChoiceField(queryset=Account.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society = None
        if self.is_bound:
            society_id = self.data.get("society")
            if society_id:
                society = Society.objects.filter(pk=society_id).first()
        else:
            society = self.initial.get("society")
        if society:
            self.fields["member"].queryset = Member.objects.filter(
                society=society,
                status=Member.MemberStatus.ACTIVE,
            ).order_by("full_name")
            self.fields["bill"].queryset = Bill.objects.filter(
                society=society,
                status__in=(Bill.BillStatus.OPEN, Bill.BillStatus.PARTIAL, Bill.BillStatus.OVERDUE),
            ).order_by("-due_date", "-id")
            self.fields["deposited_account"].queryset = Account.objects.filter(
                society=society,
                category__account_type="ASSET",
            ).order_by("name")

    def clean(self):
        cleaned = super().clean()
        payment_mode = cleaned.get("payment_mode")
        reference = (cleaned.get("reference_number") or "").strip()
        amount = cleaned.get("amount") or Decimal("0.00")
        bill = cleaned.get("bill")
        member = cleaned.get("member")
        if payment_mode != "CASH" and not reference:
            raise forms.ValidationError("Reference number is required for non-cash receipts.")
        if bill and member and bill.member_id != member.id:
            raise forms.ValidationError("Bill must belong to selected member.")
        if bill and amount > bill.outstanding_amount:
            raise forms.ValidationError("Receipt amount cannot exceed bill outstanding amount.")
        return cleaned

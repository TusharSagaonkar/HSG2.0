from decimal import Decimal

from django import forms
from django.forms import BaseFormSet
from django.utils import timezone

from accounting.models import Account
from accounting.models import Voucher
from members.models import Unit


class VoucherForm(forms.ModelForm):
    class Meta:
        model = Voucher
        fields = [
            "society",
            "voucher_date",
            "voucher_type",
            "payment_mode",
            "reference_number",
            "narration",
        ]
        widgets = {
            "voucher_date": forms.DateInput(attrs={"type": "date"}),
            "narration": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("voucher_date"):
            self.initial["voucher_date"] = timezone.localdate()
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css

    def clean_voucher_date(self):
        voucher_date = self.cleaned_data["voucher_date"]
        if voucher_date > timezone.localdate():
            raise forms.ValidationError("Voucher date cannot be in the future.")
        return voucher_date


class LedgerEntryRowForm(forms.Form):
    account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False)
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    debit = forms.DecimalField(max_digits=12, decimal_places=2, required=False)
    credit = forms.DecimalField(max_digits=12, decimal_places=2, required=False)

    def __init__(self, *args, **kwargs):
        society = kwargs.pop("society", None)
        self.society = society
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = (
            Account.objects.filter(society=society).order_by("name")
            if society
            else Account.objects.none()
        )
        self.fields["unit"].queryset = (
            Unit.objects.filter(structure__society=society).order_by("identifier")
            if society
            else Unit.objects.none()
        )
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        unit = cleaned.get("unit")
        debit = cleaned.get("debit") or Decimal("0")
        credit = cleaned.get("credit") or Decimal("0")

        if not account and debit == 0 and credit == 0:
            return cleaned

        if not account:
            raise forms.ValidationError("Select an account for this row.")

        if debit < 0 or credit < 0:
            raise forms.ValidationError("Debit and credit amounts must be positive.")

        if debit > 0 and credit > 0:
            raise forms.ValidationError("Row cannot have both debit and credit.")

        if debit == 0 and credit == 0:
            raise forms.ValidationError("Enter either debit or credit.")

        if not account.is_active:
            raise forms.ValidationError("Selected account is inactive.")

        if self.society and account.society_id != self.society.id:
            raise forms.ValidationError("Account must belong to the selected society.")

        if unit and self.society and unit.structure.society_id != self.society.id:
            raise forms.ValidationError("Unit must belong to the selected society.")

        return cleaned


class LedgerEntryRowBaseFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        rows = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        account_sides = {}

        for form in self.forms:
            row = form.cleaned_data
            if not row:
                continue
            account = row.get("account")
            debit = row.get("debit") or Decimal("0")
            credit = row.get("credit") or Decimal("0")
            if not account and debit == 0 and credit == 0:
                continue

            rows.append(row)
            total_debit += debit
            total_credit += credit
            if account:
                sides = account_sides.setdefault(
                    account.id,
                    {"debit": Decimal("0"), "credit": Decimal("0")},
                )
                sides["debit"] += debit
                sides["credit"] += credit

        if len(rows) < 2:
            raise forms.ValidationError("Voucher must contain at least two ledger entry rows.")

        if total_debit != total_credit:
            raise forms.ValidationError("Total debit and credit must be equal.")

        for sides in account_sides.values():
            if sides["debit"] > 0 and sides["credit"] > 0:
                raise forms.ValidationError(
                    "Same account cannot be debited and credited in the same voucher."
                )

"""
Forms for nominee management.
"""
from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from members.models.model_Nominee import Nominee


class NomineeForm(forms.ModelForm):
    """Form for creating/updating nominees."""
    
    class Meta:
        model = Nominee
        fields = ['name', 'relationship', 'percentage', 'priority_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'relationship': forms.TextInput(attrs={'class': 'form-control'}),
            'percentage': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'max': '100'
            }),
            'priority_order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '1'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'percentage': _("Percentage of shares (0-100)"),
            'priority_order': _("Priority order (1 = highest)"),
            'is_active': _("Uncheck to deactivate nominee"),
        }
    
    def __init__(self, *args, **kwargs):
        self.member = kwargs.pop('member', None)
        super().__init__(*args, **kwargs)
        
        if self.instance.pk:
            # Editing existing nominee
            self.member = self.instance.member
    
    def clean(self):
        cleaned_data = super().clean()
        percentage = cleaned_data.get('percentage')
        priority_order = cleaned_data.get('priority_order')
        is_active = cleaned_data.get('is_active', True)
        
        if self.member and is_active:
            # Check total percentage of active nominees
            active_nominees = Nominee.objects.filter(
                member=self.member,
                is_active=True
            ).exclude(pk=self.instance.pk if self.instance.pk else None)
            
            total_percentage = sum(n.percentage for n in active_nominees)
            if percentage and total_percentage + percentage > 100:
                raise ValidationError(
                    _("Total nominee percentage cannot exceed 100%. "
                      "Current total: %(current)s%, adding: %(adding)s%") % {
                        'current': total_percentage,
                        'adding': percentage
                    }
                )
            
            # Check priority order uniqueness
            if priority_order:
                conflicting = Nominee.objects.filter(
                    member=self.member,
                    is_active=True,
                    priority_order=priority_order
                ).exclude(pk=self.instance.pk if self.instance.pk else None)
                
                if conflicting.exists():
                    raise ValidationError(
                        _("Priority order %(order)s is already in use by another active nominee.") % {
                            'order': priority_order
                        }
                    )
        
        return cleaned_data
    
    def save(self, commit=True):
        if self.member and not self.instance.pk:
            self.instance.member = self.member
        
        return super().save(commit)
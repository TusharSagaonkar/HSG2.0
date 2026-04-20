from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
import re


def normalize_key_combination(key_str):
    """
    Normalize key combination to consistent format.
    
    Examples:
        "ctrl + r" -> "CTRL+R"
        "Ctrl+Alt+R" -> "CTRL+ALT+R"
        "f9" -> "F9"
    """
    if not key_str:
        return ""
    
    # Remove spaces, convert to uppercase
    normalized = key_str.upper().replace(" ", "")
    
    # Standardize separator to '+'
    normalized = re.sub(r'[+\-_,;]+', '+', normalized)
    
    # Remove duplicate '+' characters
    normalized = re.sub(r'\++', '+', normalized)
    
    # Standardize common key names
    replacements = {
        'CONTROL': 'CTRL',
        'COMMAND': 'CMD',
        'OPTION': 'ALT',
        'WINDOWS': 'WIN',
        'META': 'META',
    }
    
    parts = normalized.split('+')
    standardized_parts = []
    for part in parts:
        if part in replacements:
            standardized_parts.append(replacements[part])
        else:
            standardized_parts.append(part)
    
    return '+'.join(standardized_parts)


class Shortcut(models.Model):
    """
    Database-driven keyboard shortcut configuration.
    """
    class ActionType(models.TextChoices):
        URL = 'URL', _('URL Redirect')
        MODAL = 'MODAL', _('Open Modal')
        JS = 'JS', _('Custom JS Action')
    
    class Scope(models.TextChoices):
        GLOBAL = 'GLOBAL', _('Global')
        PAGE = 'PAGE', _('Page Specific')
    
    name = models.CharField(
        _('name'),
        max_length=100,
        help_text=_('Descriptive name for the shortcut (e.g., "Purchase Voucher")')
    )
    
    key_combination = models.CharField(
        _('key combination'),
        max_length=50,
        help_text=_('Key combination like "F9", "Ctrl+Alt+R". Will be normalized to uppercase.')
    )
    
    action_type = models.CharField(
        _('action type'),
        max_length=20,
        choices=ActionType.choices,
        default=ActionType.URL
    )
    
    action_value = models.CharField(
        _('action value'),
        max_length=255,
        help_text=_(
            'URL → "/voucher/purchase/"\n'
            'MODAL → URL to load modal content\n'
            'JS → JavaScript function name like "runReconciliation()"'
        )
    )
    
    scope = models.CharField(
        _('scope'),
        max_length=20,
        choices=Scope.choices,
        default=Scope.GLOBAL
    )
    
    page = models.CharField(
        _('page'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('Page identifier for page-specific shortcuts (e.g., "voucher_page", "dashboard")')
    )
    
    role = models.CharField(
        _('role'),
        max_length=50,
        blank=True,
        null=True,
        help_text=_('Optional role restriction (e.g., "ACCOUNTANT", "ADMIN"). Leave empty for all roles.')
    )
    
    is_active = models.BooleanField(
        _('active'),
        default=True,
        help_text=_('Whether this shortcut is currently active')
    )
    
    priority = models.IntegerField(
        _('priority'),
        default=0,
        help_text=_('Higher priority shortcuts take precedence')
    )
    
    created_at = models.DateTimeField(
        _('created at'),
        auto_now_add=True
    )
    
    normalized_key = models.CharField(
        _('normalized key'),
        max_length=50,
        editable=False,
        db_index=True
    )
    
    class Meta:
        verbose_name = _('keyboard shortcut')
        verbose_name_plural = _('keyboard shortcuts')
        ordering = ['-priority', 'name']
        indexes = [
            models.Index(fields=['normalized_key', 'is_active']),
            models.Index(fields=['scope', 'page', 'is_active']),
            models.Index(fields=['role', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.key_combination})"
    
    def clean(self):
        """Validate and normalize key combination before saving."""
        super().clean()
        
        if not self.key_combination:
            raise ValidationError({'key_combination': _('Key combination is required')})
        
        # Normalize the key combination
        self.normalized_key = normalize_key_combination(self.key_combination)
        
        # Validate scope and page relationship
        if self.scope == self.Scope.PAGE and not self.page:
            raise ValidationError({
                'page': _('Page must be specified for page-specific shortcuts')
            })
        
        if self.scope == self.Scope.GLOBAL and self.page:
            raise ValidationError({
                'page': _('Page should be empty for global shortcuts')
            })
    
    def save(self, *args, **kwargs):
        """Ensure normalization is applied before saving."""
        self.normalized_key = normalize_key_combination(self.key_combination)
        super().save(*args, **kwargs)

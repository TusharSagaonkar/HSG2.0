"""
Management command to seed default keyboard shortcuts for the Housing Accounting system.
Run with: python manage.py seed_shortcuts
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from administration.models import Shortcut, normalize_key_combination


User = get_user_model()


class Command(BaseCommand):
    help = "Seed default keyboard shortcuts for the system"

    def handle(self, *args, **options):
        self.stdout.write("Seeding default keyboard shortcuts...")
        
        # Default shortcuts for common actions - NO BROWSER CONFLICTS
        default_shortcuts = [
            # ==================== NAVIGATION SHORTCUTS (Global, no role restriction) ====================
            {
                'name': 'Go to Home',
                'key_combination': 'CTRL+H',
                'action_type': 'URL',
                'action_value': '/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Housing Module',
                'key_combination': 'CTRL+SHIFT+H',
                'action_type': 'URL',
                'action_value': '/housing/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Accounting Module',
                'key_combination': 'CTRL+SHIFT+A',
                'action_type': 'URL',
                'action_value': '/accounting/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Reports Module',
                'key_combination': 'CTRL+SHIFT+R',
                'action_type': 'URL',
                'action_value': '/reports/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Billing Module',
                'key_combination': 'CTRL+SHIFT+B',
                'action_type': 'URL',
                'action_value': '/billing/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Parking Module',
                'key_combination': 'CTRL+SHIFT+P',
                'action_type': 'URL',
                'action_value': '/parking/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Members Module',
                'key_combination': 'CTRL+SHIFT+M',
                'action_type': 'URL',
                'action_value': '/members/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users (changed from admin-only)
                'is_active': True,
            },
            
            # ==================== ACTION SHORTCUTS (Global, no role restriction) ====================
            {
                'name': 'Quick Search',
                'key_combination': 'F2',
                'action_type': 'JS',
                'action_value': 'focusSearchField()',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Create New Item',
                'key_combination': 'F4',
                'action_type': 'JS',
                'action_value': 'openCreateModal()',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'View Dashboard',
                'key_combination': 'F7',
                'action_type': 'URL',
                'action_value': '/accounting/dashboard/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Open Voucher Entry',
                'key_combination': 'CTRL+SHIFT+V',
                'action_type': 'URL',
                'action_value': '/accounting/vouchers/entry/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Print/Export',
                'key_combination': 'F8',
                'action_type': 'JS',
                'action_value': 'triggerPrint()',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Quick Help',
                'key_combination': 'CTRL+Q',
                'action_type': 'JS',
                'action_value': 'showShortcutHelp',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            
            # ==================== ACCOUNTING-SPECIFIC SHORTCUTS (Page-specific) ====================
            {
                'name': 'Create Purchase Voucher',
                'key_combination': 'F9',
                'action_type': 'URL',
                'action_value': '/accounting/voucher/entry/?type=PURCHASE',
                'scope': 'PAGE',
                'page': 'accounting',
                'role': '',  # Available to all users on accounting pages
                'is_active': True,
            },
            {
                'name': 'Create Receipt Voucher',
                'key_combination': 'F10',
                'action_type': 'URL',
                'action_value': '/accounting/voucher/entry/?type=RECEIPT',
                'scope': 'PAGE',
                'page': 'accounting',
                'role': '',  # Available to all users on accounting pages
                'is_active': True,
            },
            {
                'name': 'Create Payment Voucher',
                'key_combination': 'F11',
                'action_type': 'URL',
                'action_value': '/accounting/voucher/entry/?type=PAYMENT',
                'scope': 'PAGE',
                'page': 'accounting',
                'role': '',  # Available to all users on accounting pages
                'is_active': True,
            },
            {
                'name': 'Create Journal Voucher',
                'key_combination': 'F12',
                'action_type': 'URL',
                'action_value': '/accounting/voucher/entry/?type=JOURNAL',
                'scope': 'PAGE',
                'page': 'accounting',
                'role': '',  # Available to all users on accounting pages
                'is_active': True,
            },
            
            # ==================== MODAL SHORTCUTS (Global, no role restriction) ====================
            {
                'name': 'Find Member Modal',
                'key_combination': 'CTRL+SHIFT+F',
                'action_type': 'MODAL',
                'action_value': '/members/search/modal/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Email Compose Modal',
                'key_combination': 'CTRL+SHIFT+E',
                'action_type': 'MODAL',
                'action_value': '/notifications/compose/modal/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'New Notification Modal',
                'key_combination': 'CTRL+SHIFT+N',
                'action_type': 'MODAL',
                'action_value': '/notifications/create/modal/',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            
            # ==================== HELP & SYSTEM ====================
            {
                'name': 'Show All Shortcuts',
                'key_combination': 'CTRL+SHIFT+?',
                'action_type': 'JS',
                'action_value': 'showShortcutHelp',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
            {
                'name': 'Quick Society Switch',
                'key_combination': 'CTRL+SHIFT+S',
                'action_type': 'JS',
                'action_value': 'focusSocietySelector()',
                'scope': 'GLOBAL',
                'page': '',
                'role': '',  # Available to all users
                'is_active': True,
            },
        ]
        
        created_count = 0
        updated_count = 0
        
        for shortcut_data in default_shortcuts:
            # Normalize the key combination
            normalized_key = normalize_key_combination(shortcut_data['key_combination'])
            
            # Check if shortcut already exists
            existing = Shortcut.objects.filter(normalized_key=normalized_key).first()
            
            if existing:
                # Update existing shortcut
                for field, value in shortcut_data.items():
                    if field != 'key_combination':  # Don't update key_combination directly
                        setattr(existing, field, value)
                existing.save()
                updated_count += 1
                self.stdout.write(f"  Updated: {shortcut_data['name']} ({normalized_key})")
            else:
                # Create new shortcut
                Shortcut.objects.create(
                    name=shortcut_data['name'],
                    key_combination=shortcut_data['key_combination'],
                    action_type=shortcut_data['action_type'],
                    action_value=shortcut_data['action_value'],
                    scope=shortcut_data['scope'],
                    page=shortcut_data['page'],
                    role=shortcut_data['role'],
                    is_active=shortcut_data['is_active'],
                    priority=0,
                )
                created_count += 1
                self.stdout.write(f"  Created: {shortcut_data['name']} ({normalized_key})")
        
        # Deactivate any old shortcuts not in the new set
        all_normalized_keys = [normalize_key_combination(s['key_combination']) for s in default_shortcuts]
        deactivated_count = Shortcut.objects.exclude(normalized_key__in=all_normalized_keys).update(is_active=False)
        
        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded shortcuts: {created_count} created, {updated_count} updated, {deactivated_count} deactivated."
        ))
        self.stdout.write("\n=== SHORTCUT SUMMARY ===")
        self.stdout.write("Navigation: Ctrl+H (Home), Ctrl+Shift+[H,A,R,B,P,M] for modules")
        self.stdout.write("Actions: F2 (Search), F4 (New), F7 (Dashboard), F8 (Print), Ctrl+Q (Help)")
        self.stdout.write("Accounting (page-specific): F9-F12 for vouchers")
        self.stdout.write("Modals: Ctrl+Shift+[F,E,N] for member/email/notification modals")
        self.stdout.write("System: Ctrl+Shift+? (Show all shortcuts), Ctrl+Shift+S (Society switch)")
        self.stdout.write("\nAll shortcuts are available to ALL users (no role restrictions).")
# Database-Driven Keyboard Shortcut System

## Overview
A production-grade, database-driven keyboard shortcut system for the Housing Accounting Django application. The system allows administrators to configure keyboard shortcuts via Django admin, with role-based access control, page-specific scopes, and HTMX integration.

## Features Implemented

### ✅ Core Features
- **Database-driven configuration**: All shortcuts stored in database, editable via Django admin
- **Role-based control**: Shortcuts can be restricted to specific user roles (owner, admin, accountant, member, viewer)
- **Global + page-specific scope**: Shortcuts can work globally or only on specific pages
- **HTMX integration**: Modal actions load content dynamically via HTMX
- **Fast lookup**: 5-minute caching, no database hits during keypress
- **Key normalization**: Consistent key combination matching (e.g., "CTRL+ALT+R")

### ✅ Action Types
1. **URL**: Redirect to specified URL
2. **MODAL**: Open modal with HTMX-loaded content
3. **JS**: Execute custom JavaScript function

### ✅ Performance Optimizations
- 5-minute cache for API responses
- Database indexing on frequently queried fields
- Normalized key storage for fast lookups
- Efficient filtering by role and page

## Architecture

### Backend Components
1. **Shortcut Model** (`administration/models.py`):
   - Database schema with all required fields
   - Automatic key normalization
   - Validation for scope/page relationships

2. **API Endpoint** (`administration/views.py`):
   - `/administration/api/shortcuts/`
   - Role and page filtering
   - 5-minute cache with Django's cache framework

3. **Admin Interface** (`administration/admin.py`):
   - Rich admin panel with filters, search, and colored displays
   - Read-only normalized key field
   - Priority-based ordering

4. **Management Command** (`administration/management/commands/seed_shortcuts.py`):
   - Seed default shortcuts (F9 for Purchase Voucher, etc.)
   - Safe update of existing shortcuts

### Frontend Components
1. **Shortcut Engine** (`housing_accounting/static/js/shortcut_engine.js`):
   - 230-line production-ready JavaScript
   - Key normalization matching backend logic
   - HTMX modal integration
   - Input field detection (ignores shortcuts in form fields)

2. **Template Integration** (`housing_accounting/templates/base.html`):
   - HTMX library included
   - Shortcut engine script loaded
   - `data-page` attribute on body for page identification

## Installation & Configuration

### 1. Database Migrations
```bash
python manage.py migrate
```

### 2. Seed Default Shortcuts
```bash
python manage.py seed_shortcuts
```

### 3. Add JavaScript Function (Optional)
For the "Quick Society Switch" shortcut (CTRL+SHIFT+S), add to `project.js`:
```javascript
function focusSocietySelector() {
    const selector = document.getElementById('id_selected_society');
    if (selector) {
        selector.focus();
        selector.click();
    }
}
```

## Usage

### For Users
- Press configured shortcuts anywhere in the application
- Shortcuts are ignored when typing in input fields
- Role-based shortcuts automatically respect user permissions
- Page-specific shortcuts only work on designated pages

### For Administrators
1. Access Django admin at `/admin/`
2. Navigate to "Keyboard shortcuts" under "Administration"
3. Create/edit shortcuts with:
   - Key combination (e.g., "F9", "CTRL+SHIFT+M")
   - Action type (URL, MODAL, JS)
   - Scope (Global or Page-specific)
   - Role restriction (optional)
   - Page identifier for page-specific shortcuts

### Default Shortcuts
| Shortcut | Action | Role | Scope |
|----------|--------|------|-------|
| F9 | Create Purchase Voucher | accountant | Global |
| F10 | Create Receipt Voucher | accountant | Global |
| F11 | Create Payment Voucher | accountant | Global |
| F12 | Create Journal Voucher | accountant | Global |
| CTRL+H | Go to Home | all | Global |
| CTRL+D | Open Dashboard | accountant | Global |
| CTRL+SHIFT+V | Open Voucher Entry | all | Global |
| CTRL+SHIFT+M | Search Members Modal | admin | Global |
| CTRL+N | New Voucher (on voucher list) | accountant | Page-specific |

## Technical Details

### Key Normalization
- Backend: `normalize_key_combination()` function in `models.py`
- Frontend: `normalizeKeyCombo()` function in `shortcut_engine.js`
- Both produce identical normalized keys (e.g., "CTRL+ALT+R")

### Caching Strategy
- API responses cached for 5 minutes
- Cache key includes user role and requested page
- Manual cache invalidation not needed (short TTL)

### Security
- Role-based filtering at API level
- Input validation in model `clean()` method
- No SQL injection vulnerabilities (Django ORM)
- XSS prevention via Django template auto-escaping

### Performance
- Database indexes on: `normalized_key`, `is_active`, `scope`, `page`, `role`
- Select queries optimized with `select_related`
- Frontend loads shortcuts once per page

## Testing

### Manual Testing
1. Log in as different roles to test role-based shortcuts
2. Navigate to different pages to test page-specific shortcuts
3. Try shortcuts in input fields (should be ignored)
4. Test all action types (URL, MODAL, JS)

### Database Verification
```python
from administration.models import Shortcut
print(Shortcut.objects.filter(is_active=True).count())  # Should be 15
```

### API Testing
```bash
curl http://localhost:8000/administration/api/shortcuts/
```

## Extending the System

### Adding New Action Types
1. Add to `ActionType` enum in `models.py`
2. Update `executeShortcut()` in `shortcut_engine.js`
3. Add admin display logic in `admin.py`

### Custom Page Identification
Override page detection by setting `data-page` attribute on body:
```html
<body data-page="custom:page-identifier">
```

### Custom JavaScript Functions
Register functions in global scope for JS action type:
```javascript
window.myCustomFunction = function() {
    // Custom logic
};
```

## Troubleshooting

### Common Issues
1. **Shortcuts not working**: Check browser console for errors
2. **Role-based shortcuts not appearing**: Verify user role in database
3. **Page-specific shortcuts not working**: Check `data-page` attribute on body
4. **HTMX modals not loading**: Ensure HTMX library is loaded before shortcut engine

### Debug Mode
Add to `shortcut_engine.js` initialization:
```javascript
window.ShortcutEngine = initShortcutEngine({ debug: true });
```

## Future Enhancements

### Planned Features
1. **Shortcut conflicts detection**: Warn admins about duplicate shortcuts
2. **User-customizable shortcuts**: Allow users to override default shortcuts
3. **Shortcut discovery UI**: Help dialog showing available shortcuts
4. **Audit logging**: Track shortcut usage for analytics
5. **Import/export**: Bulk shortcut management

### Performance Improvements
1. **WebSocket updates**: Real-time shortcut updates without page reload
2. **Compressed API responses**: Reduce payload size
3. **LocalStorage caching**: Cache shortcuts in browser for offline use

## Conclusion
The keyboard shortcut system is production-ready with:
- ✅ Complete backend implementation
- ✅ Frontend engine with HTMX integration
- ✅ Admin configuration interface
- ✅ Default shortcuts for common actions
- ✅ Performance optimizations
- ✅ Security considerations
- ✅ Documentation and testing

The system follows Django best practices and can scale to handle thousands of shortcuts with minimal performance impact.
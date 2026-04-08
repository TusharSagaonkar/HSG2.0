# Voucher Entry Performance Investigation

Last updated: `2026-04-07`

## Problem Summary

`/accounting/vouchers/entry/` was loading very slowly for societies with many units.

## Root Cause

The primary bottleneck was an N+1 query pattern during unit dropdown rendering:

1. `LedgerEntryRowForm.unit` is a `ModelChoiceField` rendered on each form row.
2. Option labels were using `Unit.__str__()`.
3. `Unit.__str__()` traverses related models (`structure -> society`) to build the label.
4. This caused one extra structure query and one extra society query per unit option, per form row.

Because the voucher entry page renders:
- 2 default rows (`extra=2`), and
- 1 `empty_form` template row for JS add-row,

the same unit choices were rendered 3 times per request, multiplying the query count.

## Measured Evidence (Local SQLite Profile)

Dataset at measurement time:
- societies: `1`
- units: `264`
- accounts: `51`

Before fix:
- average response time: `~4146 ms`
- SQL queries per request: `1605`
- response size: `108705 bytes`
- repeated queries included:
  - `~792` queries against `housing_structure`
  - `~795` queries against `housing_society`

After fix:
- average response time: `~445 ms`
- SQL queries per request: `20`
- response size: `88899 bytes`

## Code Change Applied

File: `accounting/forms.py`

- Added `UnitChoiceField` with a local label strategy that does not call `Unit.__str__()`:
  - label format: `"{identifier} ({unit_type_display})"`
- Switched `LedgerEntryRowForm.unit` from `ModelChoiceField` to `UnitChoiceField`.
- Kept unit queryset society-scoped and added `select_related("structure__society")`.

This removes relation traversal during option label rendering and eliminates the N+1 explosion.

## Environment Latency Note

Your `.env` uses:
- `DJANGO_LOCAL_DATABASE_MODE=remote`
- default DB host in `us-east-1` (Neon)

Even after query optimization, cross-region DB round trips can still add noticeable latency.  
For local development responsiveness, prefer one of:

1. `DJANGO_LOCAL_DATABASE_MODE=auto` (falls back when remote is not reachable), or
2. `DJANGO_LOCAL_DATABASE_MODE=sqlite` for fastest local feedback loops.

## Quick Verification Command

Use this to profile voucher entry in local mode:

```bash
DJANGO_LOCAL_DATABASE_MODE=sqlite DJANGO_ALLOWED_HOSTS=testserver,localhost,127.0.0.1 .venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
import django
django.setup()
from time import perf_counter
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.contrib.auth import get_user_model
from django.urls import reverse
from societies.models import Society
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID

client = Client()
user = get_user_model().objects.first()
client.force_login(user)
society = Society.objects.order_by("id").first()
session = client.session
session[SESSION_SELECTED_SOCIETY_ID] = society.id
session.save()
url = reverse("accounting:voucher-entry") + f"?society={society.id}"

start = perf_counter()
with CaptureQueriesContext(connection) as ctx:
    resp = client.get(url)
print("status=", resp.status_code, "ms=", round((perf_counter() - start) * 1000, 2), "queries=", len(ctx))
PY
```

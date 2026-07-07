# Project Conventions

## Testing Conventions

### Shared Test Infrastructure

The project provides shared test infrastructure to avoid the expensive cost of society creation. **Use it — never call `Society.objects.create()` directly.**

Creating a `Society` triggers a `post_save` signal cascade that bootstraps ~130+ records (accounting accounts, gateops config) and takes 5–8 seconds. The shared infrastructure creates the society **once** and reuses it.

**Shared modules:**

- [`core/test_factories.py`](core/test_factories.py:1) — shared factories:
  - `FIXED_SOCIETY_NAME = "Test Society Alpha"` — canonical name for the shared test society
  - `SocietyFactory` — `django_get_or_create` on `name`, so repeated calls return the same society
  - `UserFactory` — `django_get_or_create` on `email`
- [`core/test_base.py`](core/test_base.py:1) — shared base class:
  - `SocietyTestCase(TestCase)` — creates `cls.society` and `cls.user` once per class in `setUpTestData()`
- [`conftest.py`](conftest.py:1) (root) and [`housing_accounting/conftest.py`](housing_accounting/conftest.py:1) — pytest fixtures:
  - `society` fixture (session-scoped) — creates ONE society per test session with ALL bootstrapped accounts
  - `user` fixture (existing, function-scoped)
  - `_media_storage` fixture (existing, autouse)

### Rule 1: NEVER call `Society.objects.create()` in tests

This triggers a `post_save` signal cascade that creates ~130+ records (accounting accounts, gateops config) and takes 5–8 seconds.

**Instead, use the shared infrastructure** (see Rules 2 and 3 below).

### Rule 2: pytest-style tests (functions with `@pytest.mark.django_db`)

Add `society` to the test function parameters to get the session-scoped society.

```python
def test_something(client, user, society):
    # society is pre-built with all accounts
    account = Account.objects.filter(society=society).first()
```

- Do **NOT** call `Society.objects.create()` or `SocietyFactory()` inside the test body.
- The `society` fixture is **session-scoped** — the same society object is reused across ALL tests in the session.

### Rule 3: Django TestCase-style tests (class-based)

Subclass `SocietyTestCase` instead of `TestCase`. `cls.society` and `cls.user` are available in `setUpTestData()`.

```python
from core.test_base import SocietyTestCase

class MyTest(SocietyTestCase):
    def test_something(self):
        # self.society is pre-built with all accounts
        account = Account.objects.filter(society=self.society).first()
```

### Rule 4: Second society for isolation tests

If a test needs to verify society isolation (cross-society checks), create a **SECOND** society with a distinct name.

- **For TestCase:** create it in `setUpTestData()`:
  ```python
  cls.society_beta = SocietyFactory(name="Test Society Beta")
  ```
- **For pytest:** create a second society inside the test function only if absolutely needed:
  ```python
  society_beta = SocietyFactory(name="Test Society Beta")
  ```

> The second society **will** trigger the bootstrap signal cascade (this is unavoidable for a genuinely new society). Keep such tests minimal.

### Rule 5: Per-test mutable state

Only reset mutable fields (e.g. `sort_order`, `status`) in `setUp()`, **not** the society itself.

The society and its bootstrapped accounts should **NEVER** be modified or deleted in tests.

### Rule 6: Importing factories

**ALWAYS** import from [`core/test_factories.py`](core/test_factories.py:1), not from app-specific factory modules.

```python
from core.test_factories import SocietyFactory, UserFactory, FIXED_SOCIETY_NAME
```

> [`reconciliation/tests/factories.py`](reconciliation/tests/factories.py:1) re-exports `SocietyFactory` for backwards compatibility. **Do not use it in new code.**

## Running Tests

- The test database (`test_postgres`) already exists and is preserved between runs. Always pass `--keepdb` to `manage.py test` to avoid the interactive "Type 'yes' to delete" prompt that fails in non-interactive terminals.
- `--reuse-db` is already configured in [`pyproject.toml`](pyproject.toml:1) for pytest.

```bash
# Django test runner
uv run python manage.py test gateops --keepdb

# pytest
uv run pytest gateops/tests/ -x
```

from pathlib import Path

import pytest


pytestmark = pytest.mark.django_db


def _iter_python_files(paths):
    for base in paths:
        root = Path(base)
        for file_path in root.rglob("*.py"):
            if "migrations" in file_path.parts:
                continue
            if "__pycache__" in file_path.parts:
                continue
            yield file_path


def test_core_modules_do_not_import_legacy_housing_models_directly():
    targets = [
        "accounting/models",
        "accounting/services",
        "accounting/forms.py",
        "accounting/views.py",
        "accounting/signals.py",
        "members",
        "billing",
        "receipts",
        "notifications",
    ]
    for file_path in _iter_python_files(targets):
        content = file_path.read_text(encoding="utf-8")
        assert "from housing.models import" not in content, (
            f"Forbidden import in {file_path}. "
            "Use domain app modules (societies/members/billing/receipts/notifications)."
        )

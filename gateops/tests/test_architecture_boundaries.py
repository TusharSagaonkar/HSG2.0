"""Architecture boundary tests for the ``gateops`` app.

Asserts that no module under ``gateops/models/`` or ``gateops/services/``
imports from the legacy ``housing.models`` package. Gateops must depend on
the domain apps (``societies``, ``members``, ``notifications``) instead,
following the same boundary enforced for ``accounting``.
"""

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


def test_gateops_modules_do_not_import_legacy_housing_models_directly():
    targets = [
        "gateops/models",
        "gateops/services",
    ]
    scanned = []
    for file_path in _iter_python_files(targets):
        scanned.append(file_path)
        content = file_path.read_text(encoding="utf-8")
        assert "from housing.models import" not in content, (
            f"Forbidden import in {file_path}. "
            "Use domain app modules (societies/members/notifications)."
        )
    # Ensure the Phase 2 rule-engine modules are actually scanned.
    scanned_names = {str(p) for p in scanned}
    expected = [
        "model_Rule.py",
        "model_RuleCondition.py",
        "model_RuleAction.py",
        "model_RuleEvaluation.py",
        "rule_engine.py",
        "rule_tester.py",
    ]
    for name in expected:
        assert any(name in n for n in scanned_names), (
            f"Phase 2 module {name} was not scanned by the boundary test."
        )

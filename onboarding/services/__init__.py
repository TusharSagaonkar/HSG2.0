"""Service layer for the Society Creation & Accounting Migration Wizard.

All services follow the pattern established in
``gateops/services/contractor_service.py``: ``@staticmethod`` methods,
``@transaction.atomic`` for mutations, explicit society scoping, and audit
logging via :class:`MigrationAuditLog`.

Services exported:
    * :class:`WizardService` — wizard lifecycle management
    * :class:`SocietySetupService` — society, structure, unit, member creation
    * :class:`ModuleConfigurationService` — module enablement
    * :class:`FinancialYearSetupService` — financial year + period creation
    * :class:`StagingService` — staging area: upload, parse, store, delete, approve
    * :class:`ValidationService` — validation engine (per-template + cross-reference)
    * :class:`ReconciliationService` — reconciliation dashboard + validation checklist (Steps 23–24)
    * :class:`MigrationFinalizationService` — opening journal creation + migration lock (Steps 25–27)
"""

from .wizard_service import WizardService
from .society_setup_service import SocietySetupService
from .module_config_service import ModuleConfigurationService
from .financial_year_service import FinancialYearSetupService
from .staging_service import StagingService
from .validation_service import ValidationService
from .reconciliation_service import ReconciliationService
from .finalization_service import MigrationFinalizationService

__all__ = [
    "WizardService",
    "SocietySetupService",
    "ModuleConfigurationService",
    "FinancialYearSetupService",
    "StagingService",
    "ValidationService",
    "ReconciliationService",
    "MigrationFinalizationService",
]

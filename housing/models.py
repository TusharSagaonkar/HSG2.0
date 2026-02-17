"""Compatibility exports for legacy `housing.models` imports.

Canonical model definitions are organized by domain app:
- societies.models
- members.models
- billing.models
- receipts.models
- notifications.models
"""

from billing.models import Bill
from billing.models import BillLine
from billing.models import ChargeTemplate
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from notifications.models import ReminderLog
from receipts.models import PaymentReceipt
from receipts.models import ReceiptAllocation
from societies.models import Society

__all__ = [
    "Society",
    "Structure",
    "Unit",
    "UnitOwnership",
    "UnitOccupancy",
    "Member",
    "ChargeTemplate",
    "Bill",
    "BillLine",
    "PaymentReceipt",
    "ReceiptAllocation",
    "ReminderLog",
]

from .model_Member import Member
from .model_Structure import Structure
from .model_Unit import Unit
from .model_UnitOccupancy import UnitOccupancy
from .model_UnitOwnership import UnitOwnership

Flat = Unit
Ownership = UnitOwnership

__all__ = [
    "Structure",
    "Unit",
    "Flat",
    "UnitOwnership",
    "Ownership",
    "UnitOccupancy",
    "Member",
]

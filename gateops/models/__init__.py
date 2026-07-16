from .model_GateOpsSocietyConfig import GateOpsSocietyConfig
from .model_Gate import Gate
from .model_SecurityGuard import SecurityGuard
from .model_GuardShift import GuardShift
from .model_GuardShiftAssignment import GuardShiftAssignment
from .model_VisitorCategory import VisitorCategory
from .model_VehicleCategory import VehicleCategory
from .model_MaterialCategory import MaterialCategory
from .model_PassType import PassType
from .model_ApprovalType import ApprovalType
from .model_NotificationPreference import NotificationPreference
from .model_GateOpsRole import GateOpsRole
from .model_GateOpsAuditLog import GateOpsAuditLog
from .model_HolidayCalendar import HolidayCalendar
from .model_MasterSettings import MasterSettings

# --- Phase 2: Rule Engine -------------------------------------------------
from .model_Rule import Rule
from .model_RuleCondition import RuleCondition
from .model_RuleAction import RuleAction
from .model_RuleEvaluation import RuleEvaluation

# --- Phase 3: Visitor Lifecycle --------------------------------------------
from .model_Person import Person
from .model_GateEvent import GateEvent
from .model_GateEventApproval import GateEventApproval
from .model_GateEventPhoto import GateEventPhoto
from .model_GateEventDocument import GateEventDocument

# --- Phase 5: Pass Management ----------------------------------------------
from .model_Pass import Pass

# --- Phase 6: Vehicle Module -----------------------------------------------
from .model_GateVehicle import GateVehicle

# --- Phase 7: Material Movement --------------------------------------------
from .model_MaterialMovement import MaterialMovement

# --- Phase 8: Parcel Management --------------------------------------------
from .model_Parcel import Parcel

# --- Phase 9: Contractor Management ----------------------------------------
from .model_Contractor import Contractor
from .model_Contract import Contract
from .model_Worker import Worker
from .model_WorkPermit import WorkPermit

# --- Phase 10: Smart Notification Engine -----------------------------------
from .model_NotificationBundle import NotificationBundle

# --- Phase 11: AI Recommendation Engine ------------------------------------
from .model_VisitorPattern import VisitorPattern
from .model_AnomalyDetection import AnomalyDetection
from .model_PeakHourPrediction import PeakHourPrediction

# --- Phase 12: Exit Management ---------------------------------------------
from .model_ShiftHandover import ShiftHandover
from .model_ShiftHandoverItem import ShiftHandoverItem

# --- Phase 13: Analytics ---------------------------------------------------
from .model_AnalyticsSnapshot import AnalyticsSnapshot

__all__ = [
    "GateOpsSocietyConfig",
    "Gate",
    "SecurityGuard",
    "GuardShift",
    "GuardShiftAssignment",
    "VisitorCategory",
    "VehicleCategory",
    "MaterialCategory",
    "PassType",
    "ApprovalType",
    "NotificationPreference",
    "GateOpsRole",
    "GateOpsAuditLog",
    "HolidayCalendar",
    "MasterSettings",
    # Phase 2: Rule Engine
    "Rule",
    "RuleCondition",
    "RuleAction",
    "RuleEvaluation",
    # Phase 3: Visitor Lifecycle
    "Person",
    "GateEvent",
    "GateEventApproval",
    "GateEventPhoto",
    "GateEventDocument",
    # Phase 5: Pass Management
    "Pass",
    # Phase 6: Vehicle Module
    "GateVehicle",
    # Phase 7: Material Movement
    "MaterialMovement",
    # Phase 8: Parcel Management
    "Parcel",
    # Phase 9: Contractor Management
    "Contractor",
    "Contract",
    "Worker",
    "WorkPermit",
    # Phase 10: Smart Notification Engine
    "NotificationBundle",
    # Phase 11: AI Recommendation Engine
    "VisitorPattern",
    "AnomalyDetection",
    "PeakHourPrediction",
    # Phase 12: Exit Management
    "ShiftHandover",
    "ShiftHandoverItem",
    # Phase 13: Analytics
    "AnalyticsSnapshot",
]

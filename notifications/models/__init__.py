from .model_EmailLog import EmailLog
from .model_EmailQueue import EmailQueue
from .model_EmailSettings import EmailProviderType
from .model_EmailSettings import GlobalEmailSettings
from .model_EmailSettings import SocietyEmailSettings
from .model_EmailTemplate import EmailTemplate
from .model_EmailVerificationToken import EmailVerificationToken
from .model_ReminderLog import ReminderLog

NotificationLog = ReminderLog

__all__ = [
    "EmailLog",
    "EmailProviderType",
    "EmailQueue",
    "EmailTemplate",
    "EmailVerificationToken",
    "GlobalEmailSettings",
    "NotificationLog",
    "ReminderLog",
    "SocietyEmailSettings",
]

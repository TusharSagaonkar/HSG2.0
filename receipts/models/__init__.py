from .model_PaymentReceipt import PaymentReceipt
from .model_ReceiptAllocation import ReceiptAllocation

Receipt = PaymentReceipt
PaymentAllocation = ReceiptAllocation

__all__ = [
    "PaymentReceipt",
    "Receipt",
    "ReceiptAllocation",
    "PaymentAllocation",
]

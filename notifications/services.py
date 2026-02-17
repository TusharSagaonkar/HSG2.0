from datetime import datetime
from datetime import time

from django.db import transaction
from django.utils import timezone

from billing.models import Bill
from notifications.models import ReminderLog


def schedule_payment_reminders(*, society, as_of_date, channels=None, upcoming_days=3):
    channels = channels or [
        ReminderLog.Channel.EMAIL,
        ReminderLog.Channel.SMS,
        ReminderLog.Channel.WHATSAPP,
    ]
    schedule_at = timezone.make_aware(datetime.combine(as_of_date, time(hour=9)))
    scheduled = 0
    bills = (
        Bill.objects.filter(
            society=society,
            status__in=(Bill.BillStatus.OPEN, Bill.BillStatus.PARTIAL, Bill.BillStatus.OVERDUE),
        )
        .select_related("member")
        .order_by("due_date", "id")
    )
    for bill in bills:
        if bill.outstanding_amount <= 0:
            continue
        due_in_days = (bill.due_date - as_of_date).days
        if due_in_days > upcoming_days:
            continue

        for channel in channels:
            exists = ReminderLog.objects.filter(
                society=society,
                member=bill.member,
                bill=bill,
                channel=channel,
                scheduled_for__date=as_of_date,
            ).exists()
            if exists:
                continue
            message = (
                f"Reminder: Bill #{bill.bill_number} of amount {bill.outstanding_amount} "
                f"is due on {bill.due_date}."
            )
            with transaction.atomic():
                ReminderLog.objects.create(
                    society=society,
                    member=bill.member,
                    bill=bill,
                    channel=channel,
                    message=message,
                    scheduled_for=schedule_at,
                )
            scheduled += 1
    return scheduled

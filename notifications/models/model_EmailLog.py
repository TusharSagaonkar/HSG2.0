from django.db import models


class EmailLog(models.Model):
    email_queue = models.ForeignKey(
        "housing.EmailQueue",
        on_delete=models.CASCADE,
        related_name="logs",
    )
    attempt_no = models.PositiveIntegerField()
    smtp_host = models.CharField(max_length=255, blank=True)
    response = models.TextField(blank=True)
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"Email log #{self.id} for queue #{self.email_queue_id}"

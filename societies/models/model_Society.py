from django.db import models


class Society(models.Model):
    name = models.CharField(max_length=200)
    registration_number = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="created_societies",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"

    def __str__(self):
        return self.name

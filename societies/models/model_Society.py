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

    @property
    def share_config(self):
        """
        Returns the related SocietyConfig or creates a default one if it doesn't exist.
        """
        from .model_SocietyConfig import SocietyConfig
        
        config, created = SocietyConfig.objects.get_or_create(
            society=self,
            defaults={
                "share_value": 100.00,
                "default_share_count": 1,
                "entrance_fee": 0,
                "transfer_fee": 0,
                "premium_amount": 0,
                "allow_multiple_nominees": False,
                "require_approval": True,
            }
        )
        return config

from django.db import models


class EmailTemplate(models.Model):
    template_name = models.CharField(max_length=120, unique=True)
    subject_template = models.TextField()
    body_template = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("template_name",)

    def __str__(self):
        return self.template_name

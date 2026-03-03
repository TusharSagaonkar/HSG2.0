import uuid

from django.db import migrations
from django.db import models
from django.utils import timezone


def populate_vehicle_verification_tokens(apps, schema_editor):
    Vehicle = apps.get_model("parking", "Vehicle")
    assigned_tokens = set()
    for vehicle in Vehicle.objects.all().iterator():
        token = vehicle.verification_token
        if token is None or token in assigned_tokens:
            token = uuid.uuid4()
            while token in assigned_tokens:
                token = uuid.uuid4()
            vehicle.verification_token = token
            vehicle.save(update_fields=["verification_token"])
        assigned_tokens.add(token)


class Migration(migrations.Migration):
    dependencies = [
        ("parking", "0003_parkingvehiclelimit"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="valid_from",
            field=models.DateField(default=timezone.localdate),
        ),
        migrations.AddField(
            model_name="vehicle",
            name="valid_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vehicle",
            name="verification_token",
            field=models.UUIDField(
                db_index=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.RunPython(
            code=populate_vehicle_verification_tokens,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="vehicle",
            name="verification_token",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        ),
    ]

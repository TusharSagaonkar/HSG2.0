from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0004_alter_account_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="system_protected",
            field=models.BooleanField(default=False),
        ),
    ]

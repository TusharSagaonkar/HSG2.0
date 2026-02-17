import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0003_accountcategory_society"),
    ]

    operations = [
        migrations.AlterField(
            model_name="account",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="accounts",
                to="accounting.accountcategory",
            ),
        ),
    ]

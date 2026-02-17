from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0001_initial"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="account",
            unique_together={("society", "name", "parent")},
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0013_account_classification_and_ledger_references"),
    ]

    operations = [
        migrations.AlterField(
            model_name="voucher",
            name="voucher_type",
            field=models.CharField(
                choices=[
                    ("GENERAL", "General"),
                    ("RECEIPT", "Receipt"),
                    ("PAYMENT", "Payment"),
                    ("ADJUSTMENT", "Adjustment"),
                    ("OPENING", "Opening Balance"),
                    ("JOURNAL", "Journal"),
                    ("BILL", "Bill"),
                ],
                max_length=20,
            ),
        ),
    ]

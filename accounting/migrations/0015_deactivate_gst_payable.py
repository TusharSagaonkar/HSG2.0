from django.db import migrations


def deactivate_legacy_gst_payable(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(name="GST Payable").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0014_alter_voucher_voucher_type"),
    ]

    operations = [
        migrations.RunPython(deactivate_legacy_gst_payable, migrations.RunPython.noop),
    ]

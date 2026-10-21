from django.db import migrations


def renumber_current_steps(apps, schema_editor):
    Wizard = apps.get_model("onboarding", "OnboardingWizard")
    database = schema_editor.connection.alias
    # Old step 5 was the separate year confirmation screen. Reopen it at
    # step 4 so users confirm creation in the new dialog. Later steps shift
    # down by one; completed step logs remain append-only historical records.
    Wizard.objects.using(database).filter(current_step=5).update(current_step=4)
    for old_step in range(28, 5, -1):
        Wizard.objects.using(database).filter(current_step=old_step).update(
            current_step=old_step - 1
        )


class Migration(migrations.Migration):
    dependencies = [("onboarding", "0001_initial")]

    operations = [migrations.RunPython(renumber_current_steps)]

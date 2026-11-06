from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_console_sign_in")]

    operations = [
        migrations.AddField(
            model_name="adminuser",
            name="role",
            field=models.CharField(
                choices=[
                    ("platform_owner", "Platform Owner"),
                    ("platform_admin", "Platform Administrator"),
                    ("operations_manager", "Operations Manager"),
                    ("support_operator", "Support Operator"),
                    ("read_only_auditor", "Read-only Auditor"),
                ],
                default="read_only_auditor",
                max_length=32,
            ),
        ),
    ]

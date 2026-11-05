"""Persist WhatsApp template values with the queued message (Phase 14.1, D-099).

Additive and backward compatible: existing rows get an empty list. Delivery tasks queued before
this migration still carry their values as a task argument and are honoured once.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0002_append_only_records"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="template_parameters",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

"""Link completed returns and exchanges to independently verified evidence."""

import commerce.tenancy.constraints
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aftersales", "0002_digital"),
        ("orders", "0002_append_only_order_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="aftersalescase",
            name="refund",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="orders.refund",
            ),
        ),
        migrations.AddField(
            model_name="aftersalescase",
            name="replacement_order",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="orders.order",
            ),
        ),
        migrations.AddConstraint(
            model_name="aftersalescase",
            constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(
                field="refund", name="after_case_refund_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="aftersalescase",
            constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(
                field="replacement_order", name="after_case_replacement_tenant"
            ),
        ),
    ]

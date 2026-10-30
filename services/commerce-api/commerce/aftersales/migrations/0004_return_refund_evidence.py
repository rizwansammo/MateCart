"""Add auditable attribution from each refund to its specific return case."""

import commerce.core.ids
import commerce.tenancy.constraints
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aftersales", "0003_claim_evidence"),
        ("orders", "0002_append_only_order_history"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReturnRefundEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=commerce.core.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=False,
                        editable=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="tenancy.tenant",
                    ),
                ),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="refund_evidence",
                        to="aftersales.aftersalescase",
                    ),
                ),
                (
                    "refund",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="return_case_evidence",
                        to="orders.refund",
                    ),
                ),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.AddConstraint(
            model_name="returnrefundevidence",
            constraint=models.UniqueConstraint(
                fields=("tenant", "id"), name="aftersales_returnrefundevidence_tenant_row"
            ),
        ),
        migrations.AddConstraint(
            model_name="returnrefundevidence",
            constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(
                field="case", name="after_return_evidence_case_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="returnrefundevidence",
            constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(
                field="refund", name="after_return_evidence_refund_tenant"
            ),
        ),
    ]

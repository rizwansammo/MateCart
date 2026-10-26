import commerce.core.ids
import commerce.tenancy.constraints
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def base_fields():
    return [
        ("id", models.UUIDField(default=commerce.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
        ("tenant", models.ForeignKey(db_index=False, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="tenancy.tenant")),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("aftersales", "0001_initial"),
        ("media", "0001_initial"),
        ("catalog", "0001_initial"),
        ("orders", "0002_append_only_order_history"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="DigitalProductAsset",
            fields=base_fields() + [
                ("enabled", models.BooleanField(default=True)),
                ("download_limit", models.PositiveSmallIntegerField(default=10)),
                ("variant", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="catalog.productvariant")),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="media.mediaasset")),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.CreateModel(
            name="DigitalEntitlement",
            fields=base_fields() + [
                ("status", models.CharField(choices=[("active","Active"),("revoked","Revoked")], default="active", max_length=16)),
                ("access_count", models.PositiveIntegerField(default=0)),
                ("download_limit", models.PositiveSmallIntegerField(default=10)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("order_item", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="orders.orderitem")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="media.mediaasset")),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.AddConstraint(model_name="digitalproductasset", constraint=models.UniqueConstraint(fields=("tenant", "id"), name="aftersales_digitalproductasset_tenant_row")),
        migrations.AddConstraint(model_name="digitalproductasset", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="variant", name="after_digital_variant_tenant")),
        migrations.AddConstraint(model_name="digitalproductasset", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="asset", name="after_digital_asset_tenant")),
        migrations.AddConstraint(model_name="digitalproductasset", constraint=models.CheckConstraint(condition=models.Q(download_limit__gte=1, download_limit__lte=100), name="after_digital_limit_valid")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=models.UniqueConstraint(fields=("tenant", "id"), name="aftersales_digitalentitlement_tenant_row")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="order_item", name="after_entitlement_item_tenant")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="customer", name="after_entitlement_customer_tenant")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="asset", name="after_entitlement_asset_tenant")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=models.CheckConstraint(condition=models.Q(status__in=["active","revoked"]), name="after_entitlement_status_valid")),
        migrations.AddConstraint(model_name="digitalentitlement", constraint=models.CheckConstraint(condition=models.Q(download_limit__gte=1, download_limit__lte=100), name="after_entitlement_limit_valid")),
    ]

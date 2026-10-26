# Phase 10: customer review and service case foundations.
import commerce.core.ids
import commerce.tenancy.constraints
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def base_fields():
    return [
        ("id", models.UUIDField(default=commerce.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
        ("tenant", models.ForeignKey(db_index=False, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="tenancy.tenant")),
    ]


def fk(target, *, nullable=False, related="+"):
    return models.ForeignKey(
        to=target, on_delete=django.db.models.deletion.PROTECT,
        related_name=related, blank=nullable, null=nullable,
    )


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("tenancy", "0001_initial"),
        ("orders", "0002_append_only_order_history"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ProductReview",
            fields=base_fields() + [
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("rating", models.PositiveSmallIntegerField()),
                ("body", models.TextField(max_length=2000)),
                ("status", models.CharField(choices=[("pending", "Pending moderation"), ("published", "Published"), ("rejected", "Rejected")], default="pending", max_length=16)),
                ("decision_reason", models.CharField(blank=True, max_length=300)),
                ("customer", fk(settings.AUTH_USER_MODEL)),
                ("order_item", fk("orders.orderitem")),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.CreateModel(
            name="AfterSalesCase",
            fields=base_fields() + [
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(max_length=16, choices=[("return","Return"),("exchange","Exchange"),("warranty","Warranty"),("support","Support")])),
                ("status", models.CharField(max_length=24, choices=[("requested","Requested"),("approved","Approved"),("rejected","Rejected"),("received","Goods received"),("inspected","Inspected"),("awaiting_refund","Awaiting verified refund"),("replacement_pending","Replacement pending"),("resolved","Resolved"),("closed","Closed")], default="requested")),
                ("quantity", models.DecimalField(max_digits=12, decimal_places=3, default=1)),
                ("subject", models.CharField(max_length=150)),
                ("reason", models.TextField(max_length=2000)),
                ("decision_reason", models.CharField(max_length=300, blank=True)),
                ("customer", fk(settings.AUTH_USER_MODEL)),
                ("order_item", fk("orders.orderitem", nullable=True)),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.CreateModel(
            name="CaseMessage",
            fields=base_fields() + [
                ("body", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("case", fk("aftersales.aftersalescase", related="messages")),
                ("author", fk(settings.AUTH_USER_MODEL)),
            ],
            options={"base_manager_name": "objects"},
        ),
        migrations.AddConstraint(model_name="productreview", constraint=models.UniqueConstraint(fields=("tenant", "id"), name="aftersales_productreview_tenant_row")),
        migrations.AddConstraint(model_name="productreview", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="customer", name="after_review_customer_tenant")),
        migrations.AddConstraint(model_name="productreview", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="order_item", name="after_review_item_tenant")),
        migrations.AddConstraint(model_name="productreview", constraint=models.UniqueConstraint(fields=("tenant", "order_item"), name="after_review_item_once")),
        migrations.AddConstraint(model_name="productreview", constraint=models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name="after_review_rating_valid")),
        migrations.AddConstraint(model_name="productreview", constraint=models.CheckConstraint(condition=models.Q(status__in=["pending","published","rejected"]), name="after_review_status_valid")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=models.UniqueConstraint(fields=("tenant", "id"), name="aftersales_aftersalescase_tenant_row")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="customer", name="after_case_customer_tenant")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="order_item", name="after_case_item_tenant")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=models.CheckConstraint(condition=models.Q(kind__in=["return","exchange","warranty","support"]), name="after_case_kind_valid")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=models.CheckConstraint(condition=models.Q(status__in=["requested","approved","rejected","received","inspected","awaiting_refund","replacement_pending","resolved","closed"]), name="after_case_status_valid")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0), name="after_case_quantity_positive")),
        migrations.AddConstraint(model_name="aftersalescase", constraint=models.CheckConstraint(condition=models.Q(kind="support") | models.Q(order_item__isnull=False), name="after_case_purchase_required")),
        migrations.AddConstraint(model_name="casemessage", constraint=models.UniqueConstraint(fields=("tenant", "id"), name="aftersales_casemessage_tenant_row")),
        migrations.AddConstraint(model_name="casemessage", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="case", name="after_message_case_tenant")),
        migrations.AddConstraint(model_name="casemessage", constraint=commerce.tenancy.constraints.TenantForeignKeyConstraint(field="author", name="after_message_author_tenant")),
    ]

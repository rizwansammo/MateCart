"""Purchase-linked reviews and controlled customer service cases."""

from typing import Any

from django.db import models
from django.db.models import Q
from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.orders.models import OrderItem
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending moderation"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"


class ProductReview(TenantOwnedModel, TimestampedModel):
    customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="+")
    rating = models.PositiveSmallIntegerField()
    body = models.TextField(max_length=2000)
    status = models.CharField(max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    decision_reason = models.CharField(max_length=300, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="after_review_customer_tenant"),
            TenantForeignKeyConstraint(field="order_item", name="after_review_item_tenant"),
            models.UniqueConstraint(fields=["tenant", "order_item"], name="after_review_item_once"),
            models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name="after_review_rating_valid"),
            models.CheckConstraint(condition=Q(status__in=ReviewStatus.values), name="after_review_status_valid"),
        ]


class CaseKind(models.TextChoices):
    RETURN = "return", "Return"
    EXCHANGE = "exchange", "Exchange"
    WARRANTY = "warranty", "Warranty"
    SUPPORT = "support", "Support"


class CaseStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    RECEIVED = "received", "Goods received"
    INSPECTED = "inspected", "Inspected"
    AWAITING_REFUND = "awaiting_refund", "Awaiting verified refund"
    REPLACEMENT_PENDING = "replacement_pending", "Replacement pending"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class AfterSalesCase(TenantOwnedModel, TimestampedModel):
    customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="+", null=True, blank=True)
    kind = models.CharField(max_length=16, choices=CaseKind.choices)
    status = models.CharField(max_length=24, choices=CaseStatus.choices, default=CaseStatus.REQUESTED)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    request_key_hash = models.CharField(max_length=64)
    subject = models.CharField(max_length=150)
    reason = models.TextField(max_length=2000)
    decision_reason = models.CharField(max_length=300, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="after_case_customer_tenant"),
            TenantForeignKeyConstraint(field="order_item", name="after_case_item_tenant"),
            models.UniqueConstraint(fields=["tenant", "customer", "request_key_hash"], name="after_case_key_unique"),
            models.CheckConstraint(condition=Q(kind__in=CaseKind.values), name="after_case_kind_valid"),
            models.CheckConstraint(condition=Q(status__in=CaseStatus.values), name="after_case_status_valid"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="after_case_quantity_positive"),
            models.CheckConstraint(
                condition=Q(kind=CaseKind.SUPPORT) | Q(order_item__isnull=False),
                name="after_case_purchase_required",
            ),
        ]


class CaseMessage(TenantOwnedModel):
    case = models.ForeignKey(AfterSalesCase, on_delete=models.PROTECT, related_name="messages")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="case", name="after_message_case_tenant"),
            TenantForeignKeyConstraint(field="author", name="after_message_author_tenant"),
        ]

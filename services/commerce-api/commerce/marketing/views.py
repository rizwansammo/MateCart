"""Marketing HTTP endpoints (Phase 12).

Merchant routes (staff realm, CSRF, each with its own permission, D-081 to D-085):
``promotions.manage`` for campaigns, flash sales, coupons and loyalty; ``reports.view`` for
analytics; ``content.manage`` for SEO text (indexing also needs ``store.settings``);
``store.settings`` for tracking and consent settings. Loyalty member records also need
``customers.view``, and point adjustments need a recent sign-in.

Public routes: consent decisions, browser view events, the sitemap feed, and the signed-in
customer's own loyalty account.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.catalog.models import (
    Category,
    Collection,
    Product,
    ProductStatus,
    ProductVariant,
)
from commerce.checkout.models import Coupon
from commerce.content.models import ContentPage, PageStatus
from commerce.core import ratelimit
from commerce.core.http import client_ip
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import (
    CsrfProtectedAPIView,
    CustomerSessionAuthentication,
    HasStaffPermission,
)
from commerce.identity.models import User
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView
from commerce.stores.modules import enabled_modules

from . import analytics, consent, coupons, loyalty, promotions, seo, tracking
from .models import (
    Campaign,
    CampaignTarget,
    ConsentRecord,
    EventName,
    FlashSale,
    FlashSaleItem,
    LoyaltyAccount,
    LoyaltyProgram,
    PromotionStatus,
)
from .serializers import (
    PROMOTION_STATES,
    AnalyticsQuerySerializer,
    AnalyticsReportSerializer,
    CampaignDetailSerializer,
    CampaignInputSerializer,
    CampaignSerializer,
    CampaignUpdateSerializer,
    ConsentInputSerializer,
    ConsentSerializer,
    CouponInputSerializer,
    CouponSerializer,
    CouponUpdateSerializer,
    CustomerLoyaltySerializer,
    EventInputSerializer,
    FlashSaleDetailSerializer,
    FlashSaleInputSerializer,
    FlashSaleSerializer,
    FlashSaleUpdateSerializer,
    LedgerEntrySerializer,
    LiveSummarySerializer,
    LookupRowSerializer,
    LoyaltyAccountRowSerializer,
    LoyaltyAdjustmentSerializer,
    LoyaltyProgramInputSerializer,
    LoyaltyProgramSerializer,
    OverviewSerializer,
    SeoOverrideInputSerializer,
    SeoSettingsInputSerializer,
    SeoSettingsSerializer,
    SitemapQuerySerializer,
    SitemapSerializer,
    StatusActionSerializer,
    TrackingInputSerializer,
    TrackingSerializer,
)

TAG = ["manage-marketing"]
PUBLIC_TAG = ["storefront-marketing"]
CanManagePromotions = HasStaffPermission.of(Permission.PROMOTIONS_MANAGE)
CanViewReports = HasStaffPermission.of(Permission.REPORTS_VIEW)
CanManageContent = HasStaffPermission.of(Permission.CONTENT_MANAGE)
CanChangeSettings = HasStaffPermission.of(Permission.STORE_SETTINGS)
CanViewCustomers = HasStaffPermission.of(Permission.CUSTOMERS_VIEW)
EVENTS_PER_MINUTE = 120
CONSENT_PER_HOUR = 60


def _valid(serializer: serializers.Serializer[Any]) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = dict(serializer.validated_data)
    return data


def _state_filter(value: str) -> Q:
    now = timezone.now()
    live_window = Q(starts_at__lte=now, ends_at__gt=now)
    return {
        "draft": Q(status=PromotionStatus.DRAFT, ends_at__gt=now),
        "scheduled": Q(status=PromotionStatus.ACTIVE, starts_at__gt=now),
        "live": Q(status=PromotionStatus.ACTIVE) & live_window,
        "paused": Q(status=PromotionStatus.PAUSED, ends_at__gt=now),
        "ended": Q(ends_at__lte=now) & ~Q(status=PromotionStatus.CANCELLED),
        "cancelled": Q(status=PromotionStatus.CANCELLED),
    }.get(value, Q())


class ListQuerySerializer(PageQuerySerializer):
    state = serializers.ChoiceField(choices=PROMOTION_STATES, required=False)
    search = serializers.CharField(max_length=100, required=False)


class PromotionsView(StaffView):
    permission_classes = (IsAuthenticated, CanManagePromotions)


# --- Overview -------------------------------------------------------------------------------------


class OverviewView(StaffView):
    @extend_schema(operation_id="marketing_overview", tags=TAG, responses={200: OverviewSerializer})
    def get(self, request: Request) -> Response:
        can = self.staff.permissions
        now = timezone.now()
        modules = enabled_modules()
        promos = Permission.PROMOTIONS_MANAGE in can
        program = LoyaltyProgram.objects.first() if promos else None
        payload: dict[str, Any] = {
            "live_campaigns": Campaign.objects.filter(_state_filter("live")).count()
            if promos
            else None,
            "scheduled_campaigns": Campaign.objects.filter(
                status=PromotionStatus.ACTIVE, starts_at__gt=now
            ).count()
            if promos
            else None,
            "live_flash_sales": FlashSale.objects.filter(_state_filter("live")).count()
            if promos
            else None,
            "scheduled_flash_sales": FlashSale.objects.filter(
                status=PromotionStatus.ACTIVE, starts_at__gt=now
            ).count()
            if promos
            else None,
            "active_coupons": Coupon.objects.filter(is_active=True).count() if promos else None,
            "loyalty_enabled": bool(program and program.is_enabled) if promos else None,
            "loyalty_members": LoyaltyAccount.objects.count() if promos else None,
            "flash_sales_module": "flash_sales" in modules,
            "loyalty_module": "loyalty" in modules,
            "tracking": tracking.status() if Permission.STORE_SETTINGS in can else None,
            "analytics_enabled": tracking.current().analytics_enabled
            if Permission.STORE_SETTINGS in can or Permission.REPORTS_VIEW in can
            else None,
            "live": analytics.live_summary() if Permission.REPORTS_VIEW in can else None,
            "seo_indexing_preference": seo.current().allow_indexing
            if Permission.CONTENT_MANAGE in can
            else None,
        }
        return Response(OverviewSerializer(payload).data)


class LookupQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=["targets", "variants"], default="targets")


class LookupView(PromotionsView):
    @extend_schema(
        operation_id="marketing_lookup",
        tags=TAG,
        parameters=[LookupQuerySerializer],
        responses={200: LookupRowSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(LookupQuerySerializer(data=request.query_params))
        text = params.get("q", "").strip()
        rows: list[dict[str, Any]] = []
        if params["kind"] == "variants":
            variants = (
                ProductVariant.objects.filter(is_active=True)
                .exclude(product__status=ProductStatus.ARCHIVED)
                .select_related("product")
            )
            if text:
                variants = variants.filter(
                    Q(sku__icontains=text) | Q(product__name__icontains=text)
                )
            found = list(variants.order_by("product__name", "position", "pk")[:20])
            prices = promotions.regular_prices([v.pk for v in found])
            for variant in found:
                price = prices.get(variant.pk)
                rows.append(
                    {
                        "id": variant.pk,
                        "kind": "variant",
                        "name": variant.product.name,
                        "detail": " · ".join(filter(None, [variant.title, variant.sku])),
                        "regular_amount": price.amount if price else None,
                        "currency": price.currency if price else "",
                    }
                )
            return Response(LookupRowSerializer(rows, many=True).data)
        match = Q(name__icontains=text) if text else Q()
        for kind, queryset in (
            ("product", Product.objects.exclude(status=ProductStatus.ARCHIVED)),
            ("category", Category.objects.all()),
            ("collection", Collection.objects.all()),
        ):
            for row in queryset.filter(match).order_by("name", "pk")[:10]:
                rows.append(
                    {
                        "id": row.pk,
                        "kind": kind,
                        "name": row.name,
                        "detail": row.slug,
                        "regular_amount": None,
                        "currency": "",
                    }
                )
        return Response(LookupRowSerializer(rows, many=True).data)


# --- Campaigns ------------------------------------------------------------------------------------


def _target_refs(campaign: Campaign) -> list[dict[str, Any]]:
    refs = []
    for target in CampaignTarget.objects.filter(campaign=campaign).select_related(
        "product", "category", "collection"
    ):
        if target.product is not None:
            refs.append({"id": target.product.pk, "name": target.product.name, "kind": "product"})
        elif target.category is not None:
            refs.append(
                {"id": target.category.pk, "name": target.category.name, "kind": "category"}
            )
        elif target.collection is not None:
            refs.append(
                {"id": target.collection.pk, "name": target.collection.name, "kind": "collection"}
            )
    return refs


def _campaign_detail(campaign: Campaign) -> dict[str, Any]:
    campaign.target_refs = _target_refs(campaign)  # type: ignore[attr-defined]
    campaign.preview = promotions.preview_campaign(campaign)  # type: ignore[attr-defined]
    context = {
        "target_counts": {campaign.pk: len(campaign.target_refs)},  # type: ignore[attr-defined]
        "performance": promotions.performance(promotions.KIND_CAMPAIGN, [campaign.pk]),
    }
    return dict(CampaignDetailSerializer(campaign, context=context).data)


def _campaign(pk: Any) -> Campaign:
    campaign = Campaign.objects.filter(pk=pk).first()
    if campaign is None:
        raise NotFound("Campaign not found.")
    return campaign


class CampaignListView(PromotionsView):
    @extend_schema(
        operation_id="marketing_campaigns",
        tags=TAG,
        parameters=[ListQuerySerializer],
        responses={200: paginated(CampaignSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(ListQuerySerializer(data=request.query_params))
        rows: QuerySet[Campaign, Campaign] = Campaign.objects.all()
        if "state" in params:
            rows = rows.filter(_state_filter(params["state"]))
        if params.get("search"):
            rows = rows.filter(name__icontains=params["search"])
        items, meta = paginate(rows.order_by("-starts_at", "-pk"), request)
        ids = [item.pk for item in items]
        counts = dict(
            CampaignTarget.objects.filter(campaign_id__in=ids)
            .values_list("campaign_id")
            .annotate(total=Count("pk"))
        )
        context = {
            "target_counts": counts,
            "performance": promotions.performance(promotions.KIND_CAMPAIGN, ids),
        }
        return Response(
            {**meta, "results": CampaignSerializer(items, many=True, context=context).data}
        )

    @extend_schema(
        operation_id="marketing_campaign_create",
        tags=TAG,
        request=CampaignInputSerializer,
        responses={201: CampaignDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(CampaignInputSerializer(data=request.data))
        campaign = promotions.save_campaign(campaign=None, data=data, actor=self.user)
        return Response(_campaign_detail(campaign), status=status.HTTP_201_CREATED)


class CampaignDetailView(PromotionsView):
    @extend_schema(
        operation_id="marketing_campaign", tags=TAG, responses={200: CampaignDetailSerializer}
    )
    def get(self, request: Request, campaign_id: Any) -> Response:
        return Response(_campaign_detail(_campaign(campaign_id)))

    @extend_schema(
        operation_id="marketing_campaign_update",
        tags=TAG,
        request=CampaignUpdateSerializer,
        responses={200: CampaignDetailSerializer},
    )
    def patch(self, request: Request, campaign_id: Any) -> Response:
        data = _valid(CampaignUpdateSerializer(data=request.data, partial=True))
        campaign = promotions.save_campaign(
            campaign=_campaign(campaign_id), data=data, actor=self.user
        )
        return Response(_campaign_detail(campaign))


class CampaignStatusView(PromotionsView):
    @extend_schema(
        operation_id="marketing_campaign_status",
        tags=TAG,
        request=StatusActionSerializer,
        responses={200: CampaignDetailSerializer},
    )
    def post(self, request: Request, campaign_id: Any) -> Response:
        action = _valid(StatusActionSerializer(data=request.data))["action"]
        campaign = promotions.set_campaign_status(
            campaign_id=_campaign(campaign_id).pk, action=action, actor=self.user
        )
        return Response(_campaign_detail(campaign))


# --- Flash sales ----------------------------------------------------------------------------------


def _flash_detail(sale: FlashSale) -> dict[str, Any]:
    items = list(
        FlashSaleItem.objects.filter(sale=sale)
        .select_related("variant__product")
        .order_by("variant__product__name", "variant__sku")
    )
    prices = promotions.regular_prices([item.variant_id for item in items])
    for item in items:
        price = prices.get(item.variant_id)
        item.regular_amount = price.amount if price else None  # type: ignore[attr-defined]
    sale.item_rows = items  # type: ignore[attr-defined]
    sale.module_enabled = "flash_sales" in enabled_modules()  # type: ignore[attr-defined]
    context = {
        "item_counts": {sale.pk: len(items)},
        "performance": promotions.performance(promotions.KIND_FLASH, [sale.pk]),
    }
    return dict(FlashSaleDetailSerializer(sale, context=context).data)


def _sale(pk: Any) -> FlashSale:
    sale = FlashSale.objects.filter(pk=pk).first()
    if sale is None:
        raise NotFound("Flash sale not found.")
    return sale


class FlashSaleListView(PromotionsView):
    @extend_schema(
        operation_id="marketing_flash_sales",
        tags=TAG,
        parameters=[ListQuerySerializer],
        responses={200: paginated(FlashSaleSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(ListQuerySerializer(data=request.query_params))
        rows: QuerySet[FlashSale, FlashSale] = FlashSale.objects.all()
        if "state" in params:
            rows = rows.filter(_state_filter(params["state"]))
        if params.get("search"):
            rows = rows.filter(name__icontains=params["search"])
        items, meta = paginate(rows.order_by("-starts_at", "-pk"), request)
        ids = [item.pk for item in items]
        counts = dict(
            FlashSaleItem.objects.filter(sale_id__in=ids)
            .values_list("sale_id")
            .annotate(total=Count("pk"))
        )
        context = {
            "item_counts": counts,
            "performance": promotions.performance(promotions.KIND_FLASH, ids),
        }
        return Response(
            {**meta, "results": FlashSaleSerializer(items, many=True, context=context).data}
        )

    @extend_schema(
        operation_id="marketing_flash_sale_create",
        tags=TAG,
        request=FlashSaleInputSerializer,
        responses={201: FlashSaleDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(FlashSaleInputSerializer(data=request.data))
        sale = promotions.save_flash_sale(sale=None, data=data, actor=self.user)
        return Response(_flash_detail(sale), status=status.HTTP_201_CREATED)


class FlashSaleDetailView(PromotionsView):
    @extend_schema(
        operation_id="marketing_flash_sale", tags=TAG, responses={200: FlashSaleDetailSerializer}
    )
    def get(self, request: Request, sale_id: Any) -> Response:
        return Response(_flash_detail(_sale(sale_id)))

    @extend_schema(
        operation_id="marketing_flash_sale_update",
        tags=TAG,
        request=FlashSaleUpdateSerializer,
        responses={200: FlashSaleDetailSerializer},
    )
    def patch(self, request: Request, sale_id: Any) -> Response:
        data = _valid(FlashSaleUpdateSerializer(data=request.data, partial=True))
        sale = promotions.save_flash_sale(sale=_sale(sale_id), data=data, actor=self.user)
        return Response(_flash_detail(sale))


class FlashSaleStatusView(PromotionsView):
    @extend_schema(
        operation_id="marketing_flash_sale_status",
        tags=TAG,
        request=StatusActionSerializer,
        responses={200: FlashSaleDetailSerializer},
    )
    def post(self, request: Request, sale_id: Any) -> Response:
        action = _valid(StatusActionSerializer(data=request.data))["action"]
        sale = promotions.set_flash_sale_status(
            sale_id=_sale(sale_id).pk, action=action, actor=self.user
        )
        return Response(_flash_detail(sale))


# --- Coupons --------------------------------------------------------------------------------------


class CouponQuerySerializer(PageQuerySerializer):
    search = serializers.CharField(max_length=32, required=False)
    active = serializers.BooleanField(required=False, allow_null=True, default=None)


class CouponListView(PromotionsView):
    @extend_schema(
        operation_id="marketing_coupons",
        tags=TAG,
        parameters=[CouponQuerySerializer],
        responses={200: paginated(CouponSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(CouponQuerySerializer(data=request.query_params))
        rows: QuerySet[Coupon, Coupon] = Coupon.objects.all()
        if params.get("search"):
            rows = rows.filter(code__icontains=params["search"].strip().upper())
        if params.get("active") is not None:
            rows = rows.filter(is_active=params["active"])
        items, meta = paginate(rows.order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": CouponSerializer(items, many=True).data})

    @extend_schema(
        operation_id="marketing_coupon_create",
        tags=TAG,
        request=CouponInputSerializer,
        responses={201: CouponSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(CouponInputSerializer(data=request.data))
        coupon = coupons.save_coupon(coupon=None, data=data, actor=self.user)
        return Response(CouponSerializer(coupon).data, status=status.HTTP_201_CREATED)


class CouponDetailView(PromotionsView):
    @extend_schema(
        operation_id="marketing_coupon_update",
        tags=TAG,
        request=CouponUpdateSerializer,
        responses={200: CouponSerializer},
    )
    def patch(self, request: Request, coupon_id: Any) -> Response:
        coupon = Coupon.objects.filter(pk=coupon_id).first()
        if coupon is None:
            raise NotFound("Coupon not found.")
        data = _valid(CouponUpdateSerializer(data=request.data, partial=True))
        coupon = coupons.save_coupon(coupon=coupon, data=data, actor=self.user)
        return Response(CouponSerializer(coupon).data)


# --- Loyalty --------------------------------------------------------------------------------------


class LoyaltyStatsSerializer(serializers.Serializer[Any]):
    program = LoyaltyProgramSerializer()
    module_enabled = serializers.BooleanField()
    members = serializers.IntegerField()
    outstanding_points = serializers.IntegerField()
    outstanding_deficit = serializers.IntegerField()
    pending_awards = serializers.IntegerField()


class LoyaltyProgramView(PromotionsView):
    def _payload(self) -> dict[str, Any]:
        from django.db.models import Sum

        from .models import AwardStatus, LoyaltyAward

        totals = LoyaltyAccount.objects.aggregate(points=Sum("balance"), deficit=Sum("deficit"))
        return dict(
            LoyaltyStatsSerializer(
                {
                    "program": loyalty.program(),
                    "module_enabled": "loyalty" in enabled_modules(),
                    "members": LoyaltyAccount.objects.count(),
                    "outstanding_points": totals["points"] or 0,
                    "outstanding_deficit": totals["deficit"] or 0,
                    "pending_awards": LoyaltyAward.objects.filter(
                        status=AwardStatus.PENDING
                    ).count(),
                }
            ).data
        )

    @extend_schema(
        operation_id="marketing_loyalty", tags=TAG, responses={200: LoyaltyStatsSerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(self._payload())

    @extend_schema(
        operation_id="marketing_loyalty_update",
        tags=TAG,
        request=LoyaltyProgramInputSerializer,
        responses={200: LoyaltyStatsSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _valid(LoyaltyProgramInputSerializer(data=request.data, partial=True))
        loyalty.save_program(data=data, actor=self.user)
        return Response(self._payload())


class MemberQuerySerializer(PageQuerySerializer):
    search = serializers.CharField(max_length=100, required=False)


class LoyaltyMembersView(StaffView):
    permission_classes = (IsAuthenticated, CanManagePromotions, CanViewCustomers)

    @extend_schema(
        operation_id="marketing_loyalty_members",
        tags=TAG,
        parameters=[MemberQuerySerializer],
        responses={200: paginated(LoyaltyAccountRowSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(MemberQuerySerializer(data=request.query_params))
        rows = LoyaltyAccount.objects.select_related("customer")
        if params.get("search"):
            text = params["search"].strip()
            rows = rows.filter(
                Q(customer__email__icontains=text)
                | Q(customer__full_name__icontains=text)
                | Q(customer__phone__icontains=text)
            )
        items, meta = paginate(rows.order_by("-balance", "pk"), request)
        return Response({**meta, "results": LoyaltyAccountRowSerializer(items, many=True).data})


class LoyaltyMemberView(StaffView):
    permission_classes = (IsAuthenticated, CanManagePromotions, CanViewCustomers)

    @extend_schema(
        operation_id="marketing_loyalty_member",
        tags=TAG,
        responses={200: LedgerEntrySerializer(many=True)},
    )
    def get(self, request: Request, customer_id: Any) -> Response:
        from .models import LoyaltyLedger

        entries = (
            LoyaltyLedger.objects.filter(account__customer_id=customer_id)
            .select_related("order")
            .order_by("-occurred_at", "-pk")[:100]
        )
        return Response(LedgerEntrySerializer(entries, many=True).data)


class LoyaltyAdjustView(StaffView):
    """Points change without a purchase: also needs a recent sign-in or password confirmation."""

    permission_classes = (IsAuthenticated, CanManagePromotions, CanViewCustomers)

    @extend_schema(
        operation_id="marketing_loyalty_adjust",
        tags=TAG,
        request=LoyaltyAdjustmentSerializer,
        responses={201: LedgerEntrySerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.staff.session)
        data = _valid(LoyaltyAdjustmentSerializer(data=request.data))
        entry = loyalty.adjust(
            customer_id=data["customer"],
            points=data["points"],
            reason=data["reason"],
            actor=self.user,
            key=data["idempotency_key"],
        )
        return Response(LedgerEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class CustomerLoyaltyView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="account_loyalty", tags=PUBLIC_TAG, responses={200: CustomerLoyaltySerializer}
    )
    def get(self, request: Request) -> Response:
        customer: User = request.user  # type: ignore[assignment]
        summary = loyalty.account_summary(customer)
        program = summary.pop("program")
        payload = {
            **summary,
            "point_value": program.point_value if program else None,
            "currency": program.currency if program else None,
            "earn_points": program.earn_points if program else None,
            "earn_spend": program.earn_spend if program else None,
            "min_redeem_points": program.min_redeem_points if program else None,
            "expiry_days": program.expiry_days if program else None,
        }
        return Response(CustomerLoyaltySerializer(payload).data)


# --- SEO ------------------------------------------------------------------------------------------


class SeoView(StaffView):
    permission_classes = (IsAuthenticated, CanManageContent)

    def _payload(self) -> dict[str, Any]:
        row = seo.current()
        items: list[dict[str, Any]] = []
        for kind, queryset, public in (
            ("category", Category.objects.all(), Q(is_active=True)),
            ("collection", Collection.objects.all(), Q(is_active=True)),
            ("page", ContentPage.objects.all(), Q(status=PageStatus.PUBLISHED)),
        ):
            public_ids = set(queryset.filter(public).values_list("pk", flat=True))
            name_field = "title" if kind == "page" else "name"
            for entry in queryset.order_by(name_field, "pk")[:200]:
                items.append(
                    {
                        "id": entry.pk,
                        "kind": kind,
                        "name": getattr(entry, name_field),
                        "slug": entry.slug,
                        "public": entry.pk in public_ids,
                        "seo_title": entry.seo_title,
                        "seo_description": entry.seo_description,
                    }
                )
        active = Product.objects.filter(status=ProductStatus.ACTIVE)
        from commerce.catalog.serializers import public_media_url

        return dict(
            SeoSettingsSerializer(
                {
                    "home_title": row.home_title,
                    "home_description": row.home_description,
                    "social_image": row.social_image_id,
                    "social_image_url": public_media_url(row.social_image_id)
                    if row.social_image_id
                    else None,
                    "allow_indexing": row.allow_indexing,
                    "can_change_indexing": Permission.STORE_SETTINGS in self.staff.permissions,
                    "items": items,
                    "products_missing_seo": active.filter(seo_title="", seo_description="").count(),
                    "active_products": active.count(),
                }
            ).data
        )

    @extend_schema(operation_id="marketing_seo", tags=TAG, responses={200: SeoSettingsSerializer})
    def get(self, request: Request) -> Response:
        return Response(self._payload())

    @extend_schema(
        operation_id="marketing_seo_update",
        tags=TAG,
        request=SeoSettingsInputSerializer,
        responses={200: SeoSettingsSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _valid(SeoSettingsInputSerializer(data=request.data, partial=True))
        seo.save(
            data=data,
            actor=self.user,
            can_change_indexing=Permission.STORE_SETTINGS in self.staff.permissions,
        )
        return Response(self._payload())


class SeoOverrideView(StaffView):
    permission_classes = (IsAuthenticated, CanManageContent)

    @extend_schema(
        operation_id="marketing_seo_override",
        tags=TAG,
        request=SeoOverrideInputSerializer,
        responses={204: None},
    )
    def patch(self, request: Request, kind: str, item_id: Any) -> Response:
        data = _valid(SeoOverrideInputSerializer(data=request.data, partial=True))
        seo.set_override(kind=kind, pk=item_id, data=data, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SitemapView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    http_method_names = ("get", "head", "options")

    @extend_schema(
        operation_id="storefront_sitemap",
        tags=PUBLIC_TAG,
        parameters=[SitemapQuerySerializer],
        responses={200: SitemapSerializer},
    )
    def get(self, request: Request) -> Response:
        params = _valid(SitemapQuerySerializer(data=request.query_params))
        return Response(SitemapSerializer(seo.sitemap(params["section"], params["page"])).data)


# --- Tracking, consent and analytics --------------------------------------------------------------


class TrackingView(StaffView):
    permission_classes = (IsAuthenticated, CanChangeSettings)

    def _payload(self) -> dict[str, Any]:
        row = tracking.current()
        since = timezone.now() - timedelta(days=30)
        recent = ConsentRecord.objects.filter(recorded_at__gte=since)
        return dict(
            TrackingSerializer(
                {
                    **{field: getattr(row, field) for field in tracking.SETTINGS_FIELDS},
                    "status": tracking.status(row),
                    "consent_records_30d": recent.count(),
                    "consent_marketing_30d": recent.filter(marketing=True).count(),
                    "consent_analytics_30d": recent.filter(analytics=True).count(),
                }
            ).data
        )

    @extend_schema(operation_id="marketing_tracking", tags=TAG, responses={200: TrackingSerializer})
    def get(self, request: Request) -> Response:
        return Response(self._payload())

    @extend_schema(
        operation_id="marketing_tracking_update",
        tags=TAG,
        request=TrackingInputSerializer,
        responses={200: TrackingSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _valid(TrackingInputSerializer(data=request.data, partial=True))
        tracking.save(data=data, actor=self.user)
        return Response(self._payload())


class AnalyticsView(StaffView):
    permission_classes = (IsAuthenticated, CanViewReports)

    @extend_schema(
        operation_id="marketing_analytics",
        tags=TAG,
        parameters=[AnalyticsQuerySerializer],
        responses={200: AnalyticsReportSerializer},
    )
    def get(self, request: Request) -> Response:
        params = _valid(AnalyticsQuerySerializer(data=request.query_params))
        try:
            data = analytics.report(
                range_key=params["range"], start=params.get("start"), end=params.get("end")
            )
        except ValueError as exc:
            raise ValidationError({"range": [str(exc)]}) from exc
        return Response(AnalyticsReportSerializer(data).data)


class LiveAnalyticsView(StaffView):
    permission_classes = (IsAuthenticated, CanViewReports)

    @extend_schema(
        operation_id="marketing_analytics_live", tags=TAG, responses={200: LiveSummarySerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(LiveSummarySerializer(analytics.live_summary()).data)


class PublicMarketingView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes: tuple[type[BasePermission], ...] = (AllowAny,)


class ConsentView(PublicMarketingView):
    @extend_schema(
        operation_id="storefront_consent",
        tags=PUBLIC_TAG,
        request=ConsentInputSerializer,
        responses={200: ConsentSerializer},
    )
    def post(self, request: Request) -> Response:
        ratelimit.consume("consent", client_ip(request), limit=CONSENT_PER_HOUR, window=3600)
        data = _valid(ConsentInputSerializer(data=request.data))
        decision = consent.record(
            request=request,
            analytics=data["analytics"],
            marketing=data["marketing"],
            source=data["origin"],
        )
        response = Response(
            ConsentSerializer(
                {
                    "analytics": decision.analytics,
                    "marketing": decision.marketing,
                    "policy_version": consent.POLICY_VERSION,
                }
            ).data
        )
        consent.set_cookie(response, decision)
        return response


class EventView(PublicMarketingView):
    @extend_schema(
        operation_id="storefront_event",
        tags=PUBLIC_TAG,
        request=EventInputSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        ratelimit.consume("analytics-event", client_ip(request), limit=EVENTS_PER_MINUTE, window=60)
        data = _valid(EventInputSerializer(data=request.data))
        settings_row = tracking.current()
        agent = str(request.META.get("HTTP_USER_AGENT", ""))
        if not settings_row.analytics_enabled or analytics.is_bot(agent):
            return Response(status=status.HTTP_204_NO_CONTENT)
        granted = consent.from_request(request)
        visitor = consent.visitor_id(request, granted)
        utm = (
            analytics.parse_source(request.COOKIES.get(consent.SOURCE_COOKIE))
            if data.get("landing") and data["name"] == EventName.PAGE_VIEW
            else None
        )
        analytics.record_browser_event(
            event=data["name"], object_id=data.get("object_id"), visitor=visitor, utm=utm
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

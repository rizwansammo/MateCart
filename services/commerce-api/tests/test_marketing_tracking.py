"""Phase 12 consent, first-party analytics, Meta Pixel/CAPI and SEO (D-083 to D-085)."""

import hashlib
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Category, Collection, ProductStatus
from commerce.content.models import ContentPage, PageStatus
from commerce.marketing import analytics, tracking
from commerce.marketing.models import (
    ConsentRecord,
    DeliveryStatus,
    EventCounter,
    MetaEventDelivery,
    OrderAttribution,
    RecentEventCounter,
    SeoSettings,
    TrackingSettings,
    VisitorDailyStat,
    VisitorPresence,
)
from commerce.orders.models import Order
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import stored_asset
from .test_checkout import add, product
from .test_marketing_loyalty import confirm
from .test_orders import merchant, place, reviewed, stock

pytestmark = pytest.mark.django_db

EVENTS = "/api/analytics/events/"
CONSENT = "/api/consent/"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"


def browser(*, analytics: bool = False, marketing: bool = False) -> tuple[APIClient, str]:
    """A visitor whose consent decision was recorded through the API."""
    client = APIClient(HTTP_USER_AGENT=BROWSER)
    response = client.post(
        CONSENT, {"analytics": analytics, "marketing": marketing, "origin": "banner"}, format="json"
    )
    assert response.status_code == 200, response.data
    visitor = uuid.uuid4().hex
    if analytics:
        client.cookies["mc_vid"] = visitor
    return client, visitor


def event(client: APIClient, name: str = "page_view", **fields: Any) -> Any:
    return client.post(EVENTS, {"name": name, **fields}, format="json")


def counted(tenant: Tenant, name: str, **filters: Any) -> int:
    with tenant_context(tenant):
        return sum(
            row.count for row in EventCounter.objects.filter(event=name, dimension="", **filters)
        )


# --- Consent --------------------------------------------------------------------------------------


def test_consent_choices_are_recorded_without_identifying_data(store: Any) -> None:
    client = APIClient(HTTP_USER_AGENT=BROWSER)
    rejected = client.post(
        CONSENT, {"analytics": False, "marketing": False, "origin": "banner"}, format="json"
    )
    cookie = client.cookies["mc_consent"].value
    client.post(
        CONSENT, {"analytics": True, "marketing": True, "origin": "preferences"}, format="json"
    )
    later = client.cookies["mc_consent"].value
    invalid = client.post(CONSENT, {"analytics": "yes please"}, format="json")

    assert rejected.data == {"analytics": False, "marketing": False, "policy_version": "1"}
    assert cookie.startswith("1:00:")
    assert later.startswith("1:11:")
    assert cookie.split(":")[2] == later.split(":")[2]  # the same browser keeps its consent id
    assert invalid.status_code == 400
    with tenant_context(store.tenant):
        rows = list(ConsentRecord.objects.order_by("recorded_at"))
    digest = hashlib.sha256(later.split(":")[2].encode()).hexdigest()
    assert [(row.analytics, row.marketing) for row in rows] == [(False, False), (True, True)]
    assert {row.subject_digest for row in rows} == {digest}
    assert later.split(":")[2] not in str([row.__dict__ for row in rows])


def test_rejecting_optional_storage_clears_tracking_cookies(store: Any) -> None:
    client, _ = browser(analytics=True, marketing=True)
    response = client.post(
        CONSENT, {"analytics": False, "marketing": False, "origin": "preferences"}, format="json"
    )
    for name in ("mc_vid", "mc_src", "_fbp", "_fbc"):
        assert response.cookies[name]["max-age"] == 0
    assert response.cookies["mc_consent"]["httponly"] == ""


def test_consent_records_are_never_updated(store: Any) -> None:
    browser()
    with tenant_context(store.tenant), pytest.raises(DatabaseError), transaction.atomic():
        ConsentRecord.objects.update(marketing=True)


def test_strictly_necessary_commerce_works_without_any_consent(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    client = APIClient()
    placed = place(client, reviewed(client, variant), key=f"n-{uuid.uuid4().hex}")
    assert placed.status_code == 201
    with tenant_context(store.tenant):
        assert not VisitorPresence.objects.exists()
        assert not OrderAttribution.objects.exists()
        assert not MetaEventDelivery.objects.exists()


# --- Analytics events -----------------------------------------------------------------------------


def test_unique_visitors_are_consented_and_distinct_from_page_views(store: Any) -> None:
    consented, _ = browser(analytics=True)
    declined, _ = browser(analytics=False)
    declined.cookies["mc_vid"] = uuid.uuid4().hex  # a visitor id without consent is ignored
    for _ in range(3):
        assert event(consented).status_code == 204
    event(declined)
    event(declined)
    other, _ = browser(analytics=True)
    event(other)

    live = analytics_live(store.tenant)
    assert live["page_views"] == 6
    assert live["consented_page_views"] == 4
    assert live["unique_visitors"] == 2
    assert live["consent_coverage"] == Decimal("66.7")
    with tenant_context(store.tenant):
        assert VisitorPresence.objects.count() == 2
        presence = VisitorPresence.objects.first()
        assert presence is not None
        assert len(presence.visitor_digest) == 64


def analytics_live(tenant: Tenant) -> dict[str, Any]:
    with tenant_context(tenant):
        return analytics.live_summary()


def test_event_payloads_are_validated_and_bounded(store: Any) -> None:
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        product_id = str(variant.product_id)
        category = Category.objects.create(name="Hidden", slug="hidden", is_active=False)
    client, _ = browser()

    unknown_name = event(client, "purchase")
    fake_product = event(client, "product_view", object_id=str(uuid.uuid4()))
    real_product = event(client, "product_view", object_id=product_id)
    hidden_category = event(client, "category_view", object_id=str(category.pk))
    money = event(client, "page_view", value="9999", currency="BDT")
    bot = APIClient(HTTP_USER_AGENT="Googlebot/2.1")
    crawled = event(bot)

    assert unknown_name.status_code == 400
    assert {fake_product.status_code, real_product.status_code, hidden_category.status_code} == {
        204
    }
    assert crawled.status_code == 204
    assert money.status_code == 204  # monetary fields are not accepted, only ignored
    assert counted(store.tenant, "product_view") == 1
    assert counted(store.tenant, "category_view") == 0
    assert counted(store.tenant, "page_view") == 1  # the bot was not counted
    with tenant_context(store.tenant):
        assert EventCounter.objects.filter(event="product_view", dimension=product_id).exists()


def test_event_ingest_is_rate_limited(store: Any) -> None:
    client, _ = browser()
    statuses = [event(client).status_code for _ in range(125)]
    assert statuses.count(204) == 120
    assert statuses[-1] == 429


def test_disabled_analytics_records_nothing(store: Any) -> None:
    with tenant_context(store.tenant):
        tracking.current()
        TrackingSettings.objects.update(analytics_enabled=False)
    variant = product(store.tenant)
    client, _ = browser(analytics=True)
    event(client)
    add(client, variant)
    with tenant_context(store.tenant):
        assert not EventCounter.objects.exists()
        assert not VisitorPresence.objects.exists()


def test_cart_and_checkout_events_are_counted_by_the_server(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 5)
    client, _ = browser(analytics=True)
    add(client, variant)
    add(client, variant)
    client.post("/api/checkout/", {}, format="json")
    client.post("/api/checkout/", {}, format="json")  # resumes the same draft checkout

    assert counted(store.tenant, "add_to_cart", consented=True) == 2
    assert counted(store.tenant, "checkout_started") == 1
    with tenant_context(store.tenant):
        assert EventCounter.objects.get(event="add_to_cart", dimension=str(variant.pk)).count == 2


# A fixed "now" 30 seconds into a minute: 24 hours earlier is 12:00:30 the previous day.
NOW = datetime(2026, 10, 2, 12, 0, 30, tzinfo=UTC)


def test_rolling_totals_cover_the_last_24_hours_to_the_minute(store: Any) -> None:
    with tenant_context(store.tenant):
        for at in (
            NOW - timedelta(hours=24, seconds=10),  # 12:00:20 the day before: outside
            NOW - timedelta(hours=24) + timedelta(seconds=40),  # 12:01:10: first whole minute
            NOW - timedelta(hours=23, minutes=30),  # 12:30:30: an hourly window would miss it
            NOW - timedelta(minutes=1),
            NOW,
        ):
            analytics.bump("page_view", consented=False, at=at)
        with mock.patch("django.utils.timezone.now", return_value=NOW):
            live = analytics.live_summary()
            report = analytics.report(range_key="24h")

    assert live["page_views"] == 4
    assert live["window_start"] == datetime(2026, 10, 1, 12, 1, tzinfo=UTC)
    assert live["exact_start"] == NOW - timedelta(hours=24)
    assert report["traffic"]["page_views"] == 4
    assert report["counter_start"] == live["window_start"]
    # Hourly breakdowns are labelled with their own, bucket-based start.
    assert report["breakdown_start"] == datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
    assert sum(point["page_views"] for point in report["series"]) == 2


def test_minute_window_starts_inside_the_24_hours() -> None:
    on_the_minute = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert analytics.minute_window_start(on_the_minute) == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert analytics.minute_window_start(NOW) == datetime(2026, 10, 1, 12, 1, tzinfo=UTC)


def test_visitors_and_orders_use_the_exact_24_hours(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    orders = []
    for index in range(2):
        client = APIClient()
        orders.append(
            place(client, reviewed(client, variant), key=f"edge-{index}-{uuid.uuid4().hex}")
        )
    with tenant_context(store.tenant):
        for digest, seen in (
            ("a" * 64, NOW - timedelta(hours=24)),
            ("b" * 64, NOW - timedelta(hours=24) + timedelta(seconds=1)),
        ):
            VisitorPresence.objects.create(
                visitor_digest=digest, first_seen_at=seen, last_seen_at=seen
            )
        Order.objects.filter(pk=orders[0].data["id"]).update(placed_at=NOW - timedelta(hours=24))
        Order.objects.filter(pk=orders[1].data["id"]).update(
            placed_at=NOW - timedelta(hours=24) + timedelta(seconds=1)
        )
        with mock.patch("django.utils.timezone.now", return_value=NOW):
            live = analytics.live_summary()
            report = analytics.report(range_key="24h")

    # (now - 24 h, now]: the exact boundary is out, one second later is in.
    assert live["unique_visitors"] == 1
    assert live["storefront_orders"] == 1
    assert report["orders"]["storefront_orders"] == 1


def test_minute_counters_are_kept_25_hours_and_hold_no_identifiers(store: Any) -> None:
    client, _ = browser(analytics=True)
    event(client)
    with tenant_context(store.tenant):
        RecentEventCounter.objects.create(
            minute=timezone.now() - timedelta(hours=26), event="page_view", consented=False, count=3
        )
        fields = {field.name for field in RecentEventCounter._meta.get_fields()}
        removed = analytics.prune()
        remaining = list(RecentEventCounter.objects.values_list("event", "consented", "count"))
    assert fields == {"id", "tenant", "minute", "event", "consented", "count"}
    assert removed["recent_counters"] == 1
    assert remaining == [("page_view", True, 1)]


def test_daily_unique_visitors_follow_the_store_time_zone(store: Any) -> None:
    # Asia/Dhaka is UTC+6: 17:30 UTC is 23:30 local, 18:30 UTC is 00:30 the next local day.
    before = datetime(2026, 10, 1, 17, 30, tzinfo=UTC)
    after = datetime(2026, 10, 1, 18, 30, tzinfo=UTC)
    with tenant_context(store.tenant):
        with mock.patch("django.utils.timezone.now", return_value=before):
            analytics.touch_presence("c" * 32)
            analytics.touch_presence("c" * 32)
        with mock.patch("django.utils.timezone.now", return_value=after):
            analytics.touch_presence("c" * 32)
            analytics.touch_presence("c" * 32, ordered=True)
        days = {row.date.isoformat(): row for row in VisitorDailyStat.objects.all()}
    assert days["2026-10-01"].unique_visitors == 1
    assert days["2026-10-02"].unique_visitors == 1
    assert days["2026-10-02"].converting_visitors == 1


def test_retention_removes_only_expired_records(store: Any) -> None:
    now = timezone.now()
    with tenant_context(store.tenant):
        VisitorPresence.objects.create(
            visitor_digest="d" * 64, first_seen_at=now, last_seen_at=now - timedelta(hours=49)
        )
        VisitorPresence.objects.create(visitor_digest="e" * 64, first_seen_at=now, last_seen_at=now)
        EventCounter.objects.create(
            bucket=now - timedelta(days=401), event="page_view", consented=False, count=1
        )
        EventCounter.objects.create(
            bucket=now - timedelta(days=10), event="page_view", consented=False, count=1
        )
        ConsentRecord.objects.create(
            subject_digest="f" * 64,
            analytics=True,
            marketing=False,
            policy_version="1",
            source="banner",
            recorded_at=now - timedelta(days=731),
        )
        removed = analytics.prune()
        assert VisitorPresence.objects.count() == 1
        assert EventCounter.objects.count() == 1
        assert not ConsentRecord.objects.exists()
    assert removed["presence"] == 1
    assert removed["counters"] == 1
    assert removed["consent"] == 1


def test_analytics_is_tenant_isolated(store: Any, tenant_b: Tenant) -> None:
    client, _ = browser(analytics=True)
    event(client)
    with tenant_context(tenant_b):
        assert not EventCounter.objects.exists()
        assert not VisitorPresence.objects.exists()
        assert not ConsentRecord.objects.exists()


def test_orders_are_attributed_only_with_consent_and_tagged_landings(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 5)
    tagged, _ = browser(analytics=True)
    landed = int(timezone.now().timestamp())
    tagged.cookies["mc_src"] = f"1~facebook~cpc~eid-sale~{landed}"
    assert event(tagged, landing=True).status_code == 204
    first = place(tagged, reviewed(tagged, variant), key=f"a-{uuid.uuid4().hex}")
    untagged, _ = browser(analytics=False)
    untagged.cookies["mc_src"] = f"1~facebook~cpc~eid-sale~{landed}"
    second = place(untagged, reviewed(untagged, variant), key=f"b-{uuid.uuid4().hex}")
    forged, _ = browser(analytics=True)
    forged.cookies["mc_src"] = "1~<script>~x~y~1"
    third = place(forged, reviewed(forged, variant), key=f"c-{uuid.uuid4().hex}")

    with tenant_context(store.tenant):
        attribution = OrderAttribution.objects.get()
        report = analytics.report(range_key="24h")
    assert str(attribution.order_id) == first.data["id"]
    assert (attribution.source, attribution.medium, attribution.campaign) == (
        "facebook",
        "cpc",
        "eid-sale",
    )
    assert second.status_code == 201
    assert third.status_code == 201
    assert report["sources"][0]["utm_source"] == "facebook"
    assert report["sources"][0]["orders"] == 1
    assert report["sources"][0]["landings"] == 1
    assert report["funnel"]["storefront_orders"] == 3
    assert report["funnel"]["converting_visitors"] == 2


def test_report_access_ranges_and_definitions(store: Any) -> None:
    url = "/api/manage/marketing/analytics/"
    owner = merchant(store.tenant)
    week = owner.get(url, {"range": "7d"})
    day = owner.get(url, {"range": "24h"})
    custom = owner.get(url, {"range": "custom", "start": "2026-09-01", "end": "2026-09-10"})
    too_long = owner.get(url, {"range": "custom", "start": "2026-01-01", "end": "2026-06-01"})
    live = owner.get(f"{url}live/")

    assert week.status_code == 200
    assert len(week.data["series"]) == 7
    assert day.data["rolling"] is True
    assert len(day.data["series"]) == 24
    assert custom.status_code == 200
    assert len(custom.data["series"]) == 10
    assert too_long.status_code == 400
    assert live.status_code == 200
    for role in (StaffRole.CONTENT_EDITOR, StaffRole.ORDER_HANDLER):
        staff = merchant(store.tenant, role)
        assert staff.get(url).status_code == 403
        assert staff.get(f"{url}live/").status_code == 403


# --- Meta Pixel and Conversions API ---------------------------------------------------------------


class FakeCapi:
    def __init__(self, failure: tracking.MetaDeliveryError | None = None) -> None:
        self.sent: list[Mapping[str, Any]] = []
        self.failure = failure

    def send(
        self, *, pixel_id: str, events: Sequence[Mapping[str, Any]], test_event_code: str
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.extend(events)


@pytest.fixture
def meta(settings: Settings, store: Any) -> Iterator[FakeCapi]:
    settings.META_CAPI_ACCESS_TOKEN = "test-token-not-real"
    settings.MATECART_PUBLIC_BASE_URL = "https://shop.example.test"
    owner = merchant(store.tenant)
    response = owner.patch(
        "/api/manage/marketing/tracking/",
        {"meta_enabled": True, "meta_pixel_id": "123456789012345", "meta_capi_enabled": True},
        format="json",
    )
    assert response.status_code == 200, response.data
    fake = FakeCapi()
    with mock.patch.object(tracking, "capi_client", return_value=fake):
        yield fake


def test_meta_is_off_by_default_and_never_exposes_credentials(
    store: Any, settings: Settings
) -> None:
    owner = merchant(store.tenant, StaffRole.MANAGER)
    config = APIClient().get("/api/storefront/config/").data
    defaults = owner.get("/api/manage/marketing/tracking/").data
    no_pixel = owner.patch("/api/manage/marketing/tracking/", {"meta_enabled": True}, format="json")
    bad_pixel = owner.patch(
        "/api/manage/marketing/tracking/",
        {"meta_enabled": True, "meta_pixel_id": "12ab"},
        format="json",
    )
    no_token = owner.patch(
        "/api/manage/marketing/tracking/",
        {"meta_enabled": True, "meta_pixel_id": "123456789", "meta_capi_enabled": True},
        format="json",
    )
    settings.META_CAPI_ACCESS_TOKEN = "super-secret-capi-token"
    pixel_only = owner.patch(
        "/api/manage/marketing/tracking/",
        {"meta_enabled": True, "meta_pixel_id": "123456789"},
        format="json",
    )
    public = APIClient().get("/api/storefront/config/").data
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    assert config["tracking"]["meta_pixel_id"] is None
    assert defaults["meta_enabled"] is False
    assert defaults["status"]["server_events"] == "off"
    assert no_pixel.status_code == 400
    assert bad_pixel.status_code == 400
    assert no_token.status_code == 409
    assert no_token.data["error"]["code"] == "capi_not_configured"
    assert pixel_only.status_code == 200
    assert public["tracking"]["meta_pixel_id"] == "123456789"
    assert "super-secret" not in str(pixel_only.data) + str(public)
    assert pixel_only.data["status"]["token_configured"] is True
    assert editor.get("/api/manage/marketing/tracking/").status_code == 403
    with tenant_context(store.tenant):
        assert "super-secret" not in str(list(AuditEvent.objects.values("changes")))


def test_purchase_waits_for_verified_payment_then_sends_once(store: Any, meta: FakeCapi) -> None:
    variant = product(store.tenant, tracked=True, amount="750.00")
    stock(store.tenant, variant, 2)
    client, _ = browser(analytics=True, marketing=True)
    client.cookies["_fbp"] = "fb.1.1730000000000.1234567890"
    placed = place(client, reviewed(client, variant), key=f"m-{uuid.uuid4().hex}")
    with tenant_context(store.tenant):
        waiting = MetaEventDelivery.objects.get()
    staff = merchant(store.tenant, StaffRole.MANAGER)
    confirm(staff, placed.data["id"])
    staff.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
    with tenant_context(store.tenant):
        delivery = MetaEventDelivery.objects.get()
        first = tracking.deliver(delivery.pk)
        second = tracking.deliver(delivery.pk)
        delivered = MetaEventDelivery.objects.get()

    assert waiting.status == DeliveryStatus.WAITING
    assert waiting.event_id == f"purchase.{placed.data['id']}"
    assert (first, second) == ("sent", "sent")
    assert len(meta.sent) == 1
    sent = meta.sent[0]
    assert sent["event_name"] == "Purchase"
    assert sent["event_id"] == f"purchase.{placed.data['id']}"
    assert sent["custom_data"]["currency"] == "BDT"
    assert sent["custom_data"]["payment_status"] == "paid"
    assert sent["user_data"]["fbp"] == "fb.1.1730000000000.1234567890"
    assert "em" not in sent["user_data"]  # hashed matching is off by default
    assert "guest@example.test" not in str(sent)
    assert delivered.fbp == ""
    assert delivered.user_agent == ""


def test_no_server_event_without_marketing_consent(store: Any, meta: FakeCapi) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    client, _ = browser(analytics=True, marketing=False)
    place(client, reviewed(client, variant), key=f"n-{uuid.uuid4().hex}")
    with tenant_context(store.tenant):
        assert not MetaEventDelivery.objects.exists()


def test_withdrawing_consent_discards_unsent_events(store: Any, meta: FakeCapi) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    client, _ = browser(analytics=True, marketing=True)
    placed = place(client, reviewed(client, variant), key=f"w-{uuid.uuid4().hex}")
    client.post(
        CONSENT, {"analytics": True, "marketing": False, "origin": "preferences"}, format="json"
    )
    staff = merchant(store.tenant, StaffRole.MANAGER)
    confirm(staff, placed.data["id"])
    staff.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
    with tenant_context(store.tenant):
        delivery = MetaEventDelivery.objects.get()
        result = tracking.deliver(delivery.pk)
    assert delivery.status == DeliveryStatus.DISCARDED
    assert delivery.last_error == "consent_withdrawn"
    assert result == "discarded"
    assert meta.sent == []


def test_order_placed_timing_and_hashed_matching(store: Any, meta: FakeCapi) -> None:
    owner = merchant(store.tenant, StaffRole.MANAGER)
    owner.patch(
        "/api/manage/marketing/tracking/",
        {"meta_purchase_timing": "order_placed", "meta_send_hashed_contact": True},
        format="json",
    )
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    client, _ = browser(marketing=True)
    placed = place(client, reviewed(client, variant), key=f"h-{uuid.uuid4().hex}")
    with tenant_context(store.tenant):
        delivery = MetaEventDelivery.objects.get()
        tracking.deliver(delivery.pk)
    sent = meta.sent[0]
    assert delivery.status == DeliveryStatus.PENDING
    assert sent["custom_data"]["payment_status"] == "unpaid"  # never claimed as paid
    assert sent["user_data"]["em"] == [hashlib.sha256(b"guest@example.test").hexdigest()]
    assert "guest@example.test" not in str(sent)
    assert placed.status_code == 201


def test_temporary_failures_retry_and_permanent_ones_stop(store: Any, meta: FakeCapi) -> None:
    owner = merchant(store.tenant, StaffRole.MANAGER)
    owner.patch(
        "/api/manage/marketing/tracking/", {"meta_purchase_timing": "order_placed"}, format="json"
    )
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    client, _ = browser(marketing=True)
    place(client, reviewed(client, variant), key=f"t-{uuid.uuid4().hex}")
    with tenant_context(store.tenant):
        delivery = MetaEventDelivery.objects.get()
        meta.failure = tracking.MetaDeliveryError("network", retryable=True)
        retry = tracking.deliver(delivery.pk)
        pending = MetaEventDelivery.objects.get()
        meta.failure = tracking.MetaDeliveryError("http_400", retryable=False)
        failed = tracking.deliver(delivery.pk)
        final = MetaEventDelivery.objects.get()
    assert retry == "retry"
    assert (pending.status, pending.attempts, pending.last_error) == ("pending", 1, "network")
    assert failed == "failed"
    assert (final.status, final.attempts, final.last_error) == ("failed", 2, "http_400")
    assert final.fbp == "" and final.user_agent == ""  # noqa: PT018


def test_disabling_meta_discards_queued_events(store: Any, meta: FakeCapi) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    client, _ = browser(marketing=True)
    place(client, reviewed(client, variant), key=f"d-{uuid.uuid4().hex}")
    merchant(store.tenant, StaffRole.MANAGER).patch(
        "/api/manage/marketing/tracking/", {"meta_capi_enabled": False}, format="json"
    )
    with tenant_context(store.tenant):
        delivery = MetaEventDelivery.objects.get()
    assert delivery.status == DeliveryStatus.DISCARDED
    assert delivery.last_error == "integration_disabled"


# --- SEO ------------------------------------------------------------------------------------------


def test_sitemap_lists_only_published_public_records(store: Any) -> None:
    live = product(store.tenant)
    with tenant_context(store.tenant):
        from .catalog_helpers import simple_product

        draft = simple_product(store.tenant, sku="DRAFT-1", name="Secret draft")
        Category.objects.create(name="Shoes", slug="shoes")
        Category.objects.create(name="Old", slug="old", is_active=False)
        Collection.objects.create(name="Eid", slug="eid")
        ContentPage.objects.create(
            slug="about", title="About", status=PageStatus.PUBLISHED, published_at=timezone.now()
        )
        ContentPage.objects.create(slug="draft-page", title="Draft")
        live_slug = live.product.slug
        assert draft.status == ProductStatus.DRAFT
    visitor = APIClient()
    pages = visitor.get("/api/storefront/sitemap/", {"section": "pages"}).data
    products = visitor.get("/api/storefront/sitemap/", {"section": "products", "page": "1"}).data
    invalid = visitor.get("/api/storefront/sitemap/", {"section": "orders"})

    assert sorted((entry["type"], entry["slug"]) for entry in pages["entries"]) == [
        ("category", "shoes"),
        ("collection", "eid"),
        ("page", "about"),
    ]
    assert [entry["slug"] for entry in products["entries"]] == [live_slug]
    assert products["count"] == 1
    assert invalid.status_code == 400


def test_seo_settings_are_plain_text_and_indexing_needs_store_settings(store: Any) -> None:
    url = "/api/manage/marketing/seo/"
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    owner = merchant(store.tenant)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    image = stored_asset(store.tenant)

    saved = editor.patch(
        url,
        {
            "home_title": "Rahim Fashion  Eid",
            "home_description": "Clothes",
            "social_image": str(image.pk),
        },
        format="json",
    )
    markup = editor.patch(url, {"home_title": "<script>alert(1)</script>"}, format="json")
    too_long = editor.patch(url, {"home_title": "x" * 71}, format="json")
    editor_indexing = editor.patch(url, {"allow_indexing": False}, format="json")
    owner_indexing = owner.patch(url, {"allow_indexing": False}, format="json")
    config = APIClient().get("/api/storefront/config/").data
    image_url = config["seo"]["social_image_url"]

    assert saved.status_code == 200
    assert saved.data["home_title"] == "Rahim Fashion Eid"
    assert saved.data["can_change_indexing"] is False
    assert markup.status_code == 400
    assert too_long.status_code == 400
    assert editor_indexing.status_code == 403
    assert owner_indexing.data["allow_indexing"] is False
    assert handler.get(url).status_code == 403
    assert config["seo"]["home_title"] == "Rahim Fashion Eid"
    assert config["seo"]["allow_indexing"] is False
    assert APIClient().get(image_url).status_code == 200
    with tenant_context(store.tenant):
        assert AuditEvent.objects.filter(action="marketing.seo.updated").count() == 2


def test_category_collection_and_page_overrides_reach_the_public_payloads(store: Any) -> None:
    with tenant_context(store.tenant):
        category = Category.objects.create(name="Shoes", slug="shoes")
        collection = Collection.objects.create(name="Eid", slug="eid")
        page = ContentPage.objects.create(
            slug="about", title="About", status=PageStatus.PUBLISHED, published_at=timezone.now()
        )
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    for kind, pk in (("category", category.pk), ("collection", collection.pk), ("page", page.pk)):
        response = editor.patch(
            f"/api/manage/marketing/seo/{kind}/{pk}/",
            {"seo_title": f"{kind} title", "seo_description": "Plain description"},
            format="json",
        )
        assert response.status_code == 204
    unknown = editor.patch(
        f"/api/manage/marketing/seo/product/{category.pk}/", {"seo_title": "x"}, format="json"
    )
    visitor = APIClient()
    tree = visitor.get("/api/catalog/categories/").data
    collection_data = visitor.get("/api/catalog/collections/eid/").data
    page_data = visitor.get("/api/storefront/pages/about/").data

    assert unknown.status_code == 400
    assert tree[0]["seo_title"] == "category title"
    assert tree[0]["id"] == str(category.pk)
    assert collection_data["seo_title"] == "collection title"
    assert collection_data["id"] == str(collection.pk)
    assert page_data["seo_description"] == "Plain description"


def test_seo_settings_are_tenant_isolated(store: Any, tenant_b: Tenant) -> None:
    merchant(store.tenant).patch(
        "/api/manage/marketing/seo/", {"home_title": "Store A"}, format="json"
    )
    with tenant_context(tenant_b):
        assert not SeoSettings.objects.exists()

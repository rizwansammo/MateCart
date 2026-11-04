"""Notification kinds: who receives them, on which channels, with which variables (D-087).

Every kind has default English and Bangla text. Merchants may override the text of a kind per
language (``NotificationTemplate``); they can only use the variables listed for that kind, all of
which come from authoritative server records. Authentication messages (codes, reset links,
invitations) are not in this registry: they keep their secure, synchronous delivery (D-041).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Audience, Category

COMMON_VARIABLES = ("store_name", "customer_name", "link")
ORDER_VARIABLES = (*COMMON_VARIABLES, "order_number", "order_status", "order_total")


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    category: str
    audience: str
    topic: str
    variables: tuple[str, ...]
    subject: dict[str, str]
    heading: dict[str, str]
    body: dict[str, str]
    sms: dict[str, str] = field(default_factory=dict)
    permission: str = ""
    email_default: bool = True
    sms_setting: str = ""

    def text(self, part: str, locale: str) -> str:
        values: dict[str, str] = getattr(self, part)
        return values.get(locale) or values.get("en", "")


def _kind(
    key: str,
    label: str,
    category: str,
    audience: str,
    topic: str,
    variables: tuple[str, ...],
    en: tuple[str, str, str],
    bn: tuple[str, str, str],
    *,
    sms: tuple[str, str] | None = None,
    permission: str = "",
    email_default: bool = True,
    sms_setting: str = "",
) -> Kind:
    return Kind(
        key=key,
        label=label,
        category=category,
        audience=audience,
        topic=topic,
        variables=variables,
        subject={"en": en[0], "bn": bn[0]},
        heading={"en": en[1], "bn": bn[1]},
        body={"en": en[2], "bn": bn[2]},
        sms={"en": sms[0], "bn": sms[1]} if sms else {},
        permission=permission,
        email_default=email_default,
        sms_setting=sms_setting,
    )


TX, SEC, OPS, MKT = (
    Category.TRANSACTIONAL,
    Category.SECURITY,
    Category.OPERATIONAL,
    Category.MARKETING,
)
C, ST = Audience.CUSTOMER, Audience.STAFF

KINDS: tuple[Kind, ...] = (
    # --- Customer: orders and payments ---------------------------------------------------------
    _kind(
        "order.placed",
        "Order placed",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "payment_method"),
        (
            "Order {order_number} received",
            "Thank you for your order",
            "Hi {customer_name},\n\nWe received order {order_number} for {order_total}. "
            "Payment: {payment_method}.\n\nWe will let you know when it is confirmed.",
        ),
        (
            "অর্ডার {order_number} পাওয়া গেছে",
            "আপনার অর্ডারের জন্য ধন্যবাদ",
            "প্রিয় {customer_name},\n\nআমরা {order_total} মূল্যের অর্ডার {order_number} পেয়েছি। "
            "পেমেন্ট: {payment_method}।\n\nঅর্ডার নিশ্চিত হলে আমরা আপনাকে জানাব।",
        ),
        sms=(
            "{store_name}: order {order_number} received, total {order_total}.",
            "{store_name}: অর্ডার {order_number} পাওয়া গেছে, মোট {order_total}।",
        ),
        sms_setting="order_updates",
    ),
    _kind(
        "order.confirmed",
        "Order confirmed",
        TX,
        C,
        "order_updates",
        ORDER_VARIABLES,
        (
            "Order {order_number} confirmed",
            "Your order is confirmed",
            "Hi {customer_name},\n\n{store_name} confirmed order {order_number}. "
            "We are preparing it now.",
        ),
        (
            "অর্ডার {order_number} নিশ্চিত হয়েছে",
            "আপনার অর্ডার নিশ্চিত হয়েছে",
            "প্রিয় {customer_name},\n\n{store_name} অর্ডার {order_number} নিশ্চিত করেছে। "
            "আমরা এখন এটি প্রস্তুত করছি।",
        ),
    ),
    _kind(
        "payment.succeeded",
        "Payment received",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "amount"),
        (
            "Payment received for order {order_number}",
            "Payment received",
            "Hi {customer_name},\n\nWe received {amount} for order {order_number}. Thank you.",
        ),
        (
            "অর্ডার {order_number}-এর পেমেন্ট পাওয়া গেছে",
            "পেমেন্ট পাওয়া গেছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number}-এর জন্য আমরা {amount} পেয়েছি। ধন্যবাদ।",
        ),
    ),
    _kind(
        "payment.failed",
        "Payment not completed",
        TX,
        C,
        "order_updates",
        ORDER_VARIABLES,
        (
            "Payment not completed for order {order_number}",
            "Your payment was not completed",
            "Hi {customer_name},\n\nThe payment for order {order_number} was not completed in "
            "time, so the order was cancelled. No money was taken by {store_name}.",
        ),
        (
            "অর্ডার {order_number}-এর পেমেন্ট সম্পন্ন হয়নি",
            "আপনার পেমেন্ট সম্পন্ন হয়নি",
            "প্রিয় {customer_name},\n\nনির্ধারিত সময়ের মধ্যে অর্ডার {order_number}-এর পেমেন্ট "
            "সম্পন্ন হয়নি, তাই অর্ডারটি বাতিল হয়েছে। {store_name} কোনো টাকা নেয়নি।",
        ),
    ),
    _kind(
        "order.cancelled",
        "Order cancelled",
        TX,
        C,
        "order_updates",
        ORDER_VARIABLES,
        (
            "Order {order_number} cancelled",
            "Your order was cancelled",
            "Hi {customer_name},\n\nOrder {order_number} has been cancelled. "
            "If you have questions, please contact {store_name}.",
        ),
        (
            "অর্ডার {order_number} বাতিল হয়েছে",
            "আপনার অর্ডার বাতিল হয়েছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number} বাতিল করা হয়েছে। "
            "কোনো প্রশ্ন থাকলে {store_name}-এর সাথে যোগাযোগ করুন।",
        ),
    ),
    _kind(
        "shipment.dispatched",
        "Order shipped",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "tracking_reference"),
        (
            "Order {order_number} is on its way",
            "Your order has shipped",
            "Hi {customer_name},\n\nOrder {order_number} has left {store_name}. "
            "Tracking: {tracking_reference}.",
        ),
        (
            "অর্ডার {order_number} পাঠানো হয়েছে",
            "আপনার অর্ডার পাঠানো হয়েছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number} {store_name} থেকে পাঠানো হয়েছে। "
            "ট্র্যাকিং: {tracking_reference}।",
        ),
        sms=(
            "{store_name}: order {order_number} has shipped.",
            "{store_name}: অর্ডার {order_number} পাঠানো হয়েছে।",
        ),
        sms_setting="shipping_updates",
    ),
    _kind(
        "shipment.delivered",
        "Order delivered",
        TX,
        C,
        "order_updates",
        ORDER_VARIABLES,
        (
            "Order {order_number} delivered",
            "Your order was delivered",
            "Hi {customer_name},\n\nOrder {order_number} was delivered. "
            "Thank you for shopping with {store_name}.",
        ),
        (
            "অর্ডার {order_number} পৌঁছে গেছে",
            "আপনার অর্ডার পৌঁছে গেছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number} পৌঁছে দেওয়া হয়েছে। "
            "{store_name}-এ কেনাকাটার জন্য ধন্যবাদ।",
        ),
        sms=(
            "{store_name}: order {order_number} was delivered.",
            "{store_name}: অর্ডার {order_number} পৌঁছে গেছে।",
        ),
        sms_setting="shipping_updates",
    ),
    _kind(
        "shipment.failed",
        "Delivery attempt failed",
        TX,
        C,
        "order_updates",
        ORDER_VARIABLES,
        (
            "Delivery attempt for order {order_number}",
            "We could not deliver your order",
            "Hi {customer_name},\n\nThe courier could not deliver order {order_number}. "
            "{store_name} will contact you to arrange delivery.",
        ),
        (
            "অর্ডার {order_number}-এর ডেলিভারি চেষ্টা",
            "আমরা আপনার অর্ডার পৌঁছাতে পারিনি",
            "প্রিয় {customer_name},\n\nকুরিয়ার অর্ডার {order_number} পৌঁছাতে পারেনি। "
            "{store_name} ডেলিভারির ব্যবস্থা করতে আপনার সাথে যোগাযোগ করবে।",
        ),
    ),
    _kind(
        "refund.initiated",
        "Refund started",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "amount"),
        (
            "Refund started for order {order_number}",
            "Your refund has started",
            "Hi {customer_name},\n\n{store_name} started a refund of {amount} for order "
            "{order_number}. We will tell you when it is completed.",
        ),
        (
            "অর্ডার {order_number}-এর রিফান্ড শুরু হয়েছে",
            "আপনার রিফান্ড শুরু হয়েছে",
            "প্রিয় {customer_name},\n\n{store_name} অর্ডার {order_number}-এর জন্য {amount} "
            "রিফান্ড শুরু করেছে। সম্পন্ন হলে আমরা জানাব।",
        ),
    ),
    _kind(
        "refund.completed",
        "Refund completed",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "amount"),
        (
            "Refund completed for order {order_number}",
            "Your refund is complete",
            "Hi {customer_name},\n\n{store_name} refunded {amount} for order {order_number}.",
        ),
        (
            "অর্ডার {order_number}-এর রিফান্ড সম্পন্ন",
            "আপনার রিফান্ড সম্পন্ন হয়েছে",
            "প্রিয় {customer_name},\n\n{store_name} অর্ডার {order_number}-এর জন্য {amount} "
            "রিফান্ড করেছে।",
        ),
    ),
    # --- Customer: after sales -----------------------------------------------------------------
    _kind(
        "case.submitted",
        "Request received",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "case_kind"),
        (
            "We received your {case_kind} request",
            "Request received",
            "Hi {customer_name},\n\nWe received your {case_kind} request for order "
            "{order_number}. {store_name} will review it soon.",
        ),
        (
            "আপনার {case_kind} অনুরোধ পাওয়া গেছে",
            "অনুরোধ পাওয়া গেছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number}-এর জন্য আপনার {case_kind} অনুরোধ "
            "পাওয়া গেছে। {store_name} শীঘ্রই এটি দেখবে।",
        ),
    ),
    _kind(
        "case.updated",
        "Request updated",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "case_kind", "case_status"),
        (
            "Your {case_kind} request: {case_status}",
            "Your request was updated",
            "Hi {customer_name},\n\nYour {case_kind} request for order {order_number} is now: "
            "{case_status}.",
        ),
        (
            "আপনার {case_kind} অনুরোধ: {case_status}",
            "আপনার অনুরোধ হালনাগাদ হয়েছে",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number}-এর জন্য আপনার {case_kind} অনুরোধের "
            "অবস্থা এখন: {case_status}।",
        ),
    ),
    _kind(
        "case.reply",
        "Reply from the store",
        TX,
        C,
        "order_updates",
        (*ORDER_VARIABLES, "case_kind"),
        (
            "{store_name} replied to your {case_kind} request",
            "You have a new reply",
            "Hi {customer_name},\n\n{store_name} replied to your {case_kind} request for order "
            "{order_number}. Open your account to read it.",
        ),
        (
            "{store_name} আপনার {case_kind} অনুরোধের উত্তর দিয়েছে",
            "আপনার জন্য নতুন উত্তর",
            "প্রিয় {customer_name},\n\nঅর্ডার {order_number}-এর {case_kind} অনুরোধে {store_name} "
            "উত্তর দিয়েছে। পড়তে আপনার অ্যাকাউন্ট খুলুন।",
        ),
    ),
    # --- Customer and staff: security ----------------------------------------------------------
    _kind(
        "security.password_changed",
        "Password changed",
        SEC,
        C,
        "security",
        COMMON_VARIABLES,
        (
            "Your {store_name} password was changed",
            "Your password was changed",
            "Hi {customer_name},\n\nThe password for your {store_name} account was just "
            "changed. If this was not you, reset your password now and contact the store.",
        ),
        (
            "আপনার {store_name} পাসওয়ার্ড পরিবর্তন হয়েছে",
            "আপনার পাসওয়ার্ড পরিবর্তন হয়েছে",
            "প্রিয় {customer_name},\n\nআপনার {store_name} অ্যাকাউন্টের পাসওয়ার্ড এইমাত্র "
            "পরিবর্তন হয়েছে। এটি আপনি না করে থাকলে এখনই পাসওয়ার্ড রিসেট করুন।",
        ),
    ),
    _kind(
        "security.mfa_changed",
        "Two-step verification changed",
        SEC,
        C,
        "security",
        (*COMMON_VARIABLES, "change"),
        (
            "Two-step verification changed on {store_name}",
            "Your sign-in protection changed",
            "Hi {customer_name},\n\nTwo-step verification on your {store_name} account changed: "
            "{change}. If this was not you, sign in and review your security settings now.",
        ),
        (
            "{store_name}-এ দুই ধাপের যাচাই পরিবর্তন হয়েছে",
            "আপনার সাইন-ইন সুরক্ষা পরিবর্তন হয়েছে",
            "প্রিয় {customer_name},\n\nআপনার {store_name} অ্যাকাউন্টে দুই ধাপের যাচাই পরিবর্তন "
            "হয়েছে: {change}। এটি আপনি না করে থাকলে এখনই নিরাপত্তা সেটিংস দেখুন।",
        ),
    ),
    _kind(
        "security.role_changed",
        "Team role changed",
        SEC,
        ST,
        "security",
        (*COMMON_VARIABLES, "role"),
        (
            "Your role at {store_name} changed",
            "Your team role changed",
            "Hi {customer_name},\n\nYour role on the {store_name} team is now {role}. "
            "Contact the store owner if you did not expect this.",
        ),
        (
            "{store_name}-এ আপনার ভূমিকা পরিবর্তন হয়েছে",
            "আপনার টিম ভূমিকা পরিবর্তন হয়েছে",
            "প্রিয় {customer_name},\n\n{store_name} টিমে আপনার ভূমিকা এখন {role}।",
        ),
    ),
    # --- Staff: operations ---------------------------------------------------------------------
    _kind(
        "staff.order.new",
        "New order",
        OPS,
        ST,
        "orders",
        (*ORDER_VARIABLES, "order_source"),
        (
            "New order {order_number} ({order_total})",
            "New order",
            "Order {order_number} for {order_total} was placed ({order_source}).",
        ),
        (
            "নতুন অর্ডার {order_number} ({order_total})",
            "নতুন অর্ডার",
            "{order_total} মূল্যের অর্ডার {order_number} এসেছে ({order_source})।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.order.cancelled",
        "Order cancelled",
        OPS,
        ST,
        "orders",
        ORDER_VARIABLES,
        (
            "Order {order_number} cancelled",
            "Order cancelled",
            "Order {order_number} ({order_total}) was cancelled.",
        ),
        (
            "অর্ডার {order_number} বাতিল",
            "অর্ডার বাতিল",
            "অর্ডার {order_number} ({order_total}) বাতিল হয়েছে।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.order.to_fulfil",
        "Order ready to fulfil",
        OPS,
        ST,
        "fulfillment",
        ORDER_VARIABLES,
        (
            "Order {order_number} is ready to prepare",
            "Order ready to prepare",
            "Order {order_number} was confirmed and needs a shipment.",
        ),
        (
            "অর্ডার {order_number} প্রস্তুত করতে হবে",
            "অর্ডার প্রস্তুত করুন",
            "অর্ডার {order_number} নিশ্চিত হয়েছে, শিপমেন্ট তৈরি করতে হবে।",
        ),
        permission="couriers.manage",
        email_default=False,
    ),
    _kind(
        "staff.payment.problem",
        "Payment problem",
        OPS,
        ST,
        "orders",
        ORDER_VARIABLES,
        (
            "Payment problem on order {order_number}",
            "Payment needs attention",
            "The online payment for order {order_number} was not completed and the order was "
            "cancelled.",
        ),
        (
            "অর্ডার {order_number}-এ পেমেন্ট সমস্যা",
            "পেমেন্ট দেখুন",
            "অর্ডার {order_number}-এর অনলাইন পেমেন্ট সম্পন্ন হয়নি এবং অর্ডার বাতিল হয়েছে।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.cod.collected",
        "Cash collected",
        OPS,
        ST,
        "orders",
        (*ORDER_VARIABLES, "amount"),
        (
            "Cash collected for order {order_number}",
            "Cash on delivery collected",
            "{amount} was recorded as collected for order {order_number}.",
        ),
        (
            "অর্ডার {order_number}-এর নগদ সংগ্রহ",
            "ক্যাশ অন ডেলিভারি সংগ্রহ",
            "অর্ডার {order_number}-এর জন্য {amount} সংগ্রহ রেকর্ড করা হয়েছে।",
        ),
        permission="reports.view",
        email_default=False,
    ),
    _kind(
        "staff.fraud.review",
        "Order needs a risk review",
        OPS,
        ST,
        "fraud",
        (*ORDER_VARIABLES, "score"),
        (
            "Order {order_number} needs a risk review",
            "Risk review needed",
            "Order {order_number} was flagged for review (risk score {score}).",
        ),
        (
            "অর্ডার {order_number}-এর ঝুঁকি যাচাই দরকার",
            "ঝুঁকি যাচাই দরকার",
            "অর্ডার {order_number} যাচাইয়ের জন্য চিহ্নিত হয়েছে (ঝুঁকি স্কোর {score})।",
        ),
        permission="orders.manage",
        email_default=True,
    ),
    _kind(
        "staff.delivery.failed",
        "Delivery failed",
        OPS,
        ST,
        "fulfillment",
        ORDER_VARIABLES,
        (
            "Delivery failed for order {order_number}",
            "Delivery needs attention",
            "The courier could not deliver order {order_number}.",
        ),
        (
            "অর্ডার {order_number}-এর ডেলিভারি ব্যর্থ",
            "ডেলিভারি দেখুন",
            "কুরিয়ার অর্ডার {order_number} পৌঁছাতে পারেনি।",
        ),
        permission="couriers.manage",
        email_default=False,
    ),
    _kind(
        "staff.case.new",
        "New customer request",
        OPS,
        ST,
        "aftersales",
        (*ORDER_VARIABLES, "case_kind"),
        (
            "New {case_kind} request for order {order_number}",
            "New customer request",
            "A customer opened a {case_kind} request for order {order_number}.",
        ),
        (
            "অর্ডার {order_number}-এর নতুন {case_kind} অনুরোধ",
            "নতুন গ্রাহক অনুরোধ",
            "একজন গ্রাহক অর্ডার {order_number}-এর জন্য {case_kind} অনুরোধ করেছেন।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.case.message",
        "New customer message",
        OPS,
        ST,
        "aftersales",
        (*ORDER_VARIABLES, "case_kind"),
        (
            "New message on a {case_kind} request ({order_number})",
            "New customer message",
            "The customer wrote on the {case_kind} request for order {order_number}.",
        ),
        (
            "{case_kind} অনুরোধে নতুন বার্তা ({order_number})",
            "নতুন গ্রাহক বার্তা",
            "অর্ডার {order_number}-এর {case_kind} অনুরোধে গ্রাহক লিখেছেন।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.review.pending",
        "Review waiting for moderation",
        OPS,
        ST,
        "aftersales",
        (*COMMON_VARIABLES, "product_name", "rating"),
        (
            "New review of {product_name} to moderate",
            "Review waiting for moderation",
            "A {rating}-star review of {product_name} is waiting for moderation.",
        ),
        (
            "{product_name}-এর নতুন রিভিউ যাচাই করুন",
            "রিভিউ যাচাইয়ের অপেক্ষায়",
            "{product_name}-এর একটি {rating}-তারকা রিভিউ যাচাইয়ের অপেক্ষায় আছে।",
        ),
        permission="orders.manage",
        email_default=False,
    ),
    _kind(
        "staff.stock.low",
        "Low stock",
        OPS,
        ST,
        "stock",
        (*COMMON_VARIABLES, "product_name", "sku", "available"),
        (
            "Low stock: {product_name} ({sku})",
            "Low stock",
            "{product_name} ({sku}) has {available} sellable left.",
        ),
        (
            "স্টক কম: {product_name} ({sku})",
            "স্টক কম",
            "{product_name} ({sku}) বিক্রয়যোগ্য বাকি {available}।",
        ),
        permission="catalog.manage",
        email_default=False,
    ),
    _kind(
        "staff.stock.out",
        "Out of stock",
        OPS,
        ST,
        "stock",
        (*COMMON_VARIABLES, "product_name", "sku"),
        (
            "Out of stock: {product_name} ({sku})",
            "Out of stock",
            "{product_name} ({sku}) has no sellable stock left.",
        ),
        (
            "স্টক শেষ: {product_name} ({sku})",
            "স্টক শেষ",
            "{product_name} ({sku})-এর কোনো বিক্রয়যোগ্য স্টক নেই।",
        ),
        permission="catalog.manage",
        email_default=False,
    ),
    _kind(
        "staff.summary.daily",
        "Daily summary",
        OPS,
        ST,
        "summaries",
        (*COMMON_VARIABLES, "date", "orders", "order_value", "collected", "to_prepare"),
        (
            "{store_name} summary for {date}",
            "Yesterday at {store_name}",
            "Date: {date}\nOrders placed: {orders}\nOrder value: {order_value}\n"
            "Verified collections: {collected}\nOrders waiting to be prepared: {to_prepare}",
        ),
        (
            "{date} তারিখের {store_name} সারসংক্ষেপ",
            "গতকাল {store_name}-এ",
            "তারিখ: {date}\nঅর্ডার: {orders}\nঅর্ডার মূল্য: {order_value}\n"
            "যাচাইকৃত সংগ্রহ: {collected}\nপ্রস্তুতির অপেক্ষায় অর্ডার: {to_prepare}",
        ),
        permission="reports.view",
        email_default=False,
    ),
    # --- Customer: consented marketing ---------------------------------------------------------
    _kind(
        "marketing.points_reminder",
        "Points reminder",
        MKT,
        C,
        "marketing",
        (*COMMON_VARIABLES, "points"),
        (
            "You have {points} points at {store_name}",
            "Your reward points are waiting",
            "Hi {customer_name},\n\nYou have {points} reward points at {store_name}. "
            "Use them at checkout on your next order.",
        ),
        (
            "{store_name}-এ আপনার {points} পয়েন্ট আছে",
            "আপনার রিওয়ার্ড পয়েন্ট অপেক্ষা করছে",
            "প্রিয় {customer_name},\n\n{store_name}-এ আপনার {points} রিওয়ার্ড পয়েন্ট আছে। "
            "পরের অর্ডারের চেকআউটে ব্যবহার করুন।",
        ),
    ),
)

BY_KEY = {kind.key: kind for kind in KINDS}

# Preference topics: customers may switch off optional channels; essential email stays on.
CUSTOMER_TOPICS = {
    "order_updates": {"label": "Order and delivery updates", "email": "essential", "sms": True},
    "security": {"label": "Security alerts", "email": "essential", "sms": False},
    "marketing": {"label": "Offers and reminders", "email": False, "sms": False},
}
STAFF_TOPICS = {
    "orders": "Orders and payments",
    "fulfillment": "Shipments and deliveries",
    "aftersales": "Customer requests and reviews",
    "stock": "Stock alerts",
    "fraud": "Risk reviews",
    "summaries": "Daily summary",
}

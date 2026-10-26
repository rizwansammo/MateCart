"""Customer-owned requests and tightly permissioned merchant decisions."""

from typing import Any, cast
from django.db.models import Avg, Count
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.catalog.models import Product, ProductStatus
from commerce.core.pagination import paginate, paginated
from commerce.identity.auth import CustomerSessionAuthentication, CsrfProtectedAPIView, HasStaffPermission
from commerce.identity.models import User
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView
from . import services
from .models import AfterSalesCase, ProductReview, ReviewStatus
from .serializers import (
    AfterSalesCaseSerializer, CaseInputSerializer, CaseMessageInputSerializer,
    CaseMessageSerializer, CaseTransitionSerializer, ProductReviewSerializer,
    PublicReviewSerializer, ReviewDecisionSerializer, ReviewInputSerializer,
)

TAGS = ["aftersales"]
MANAGE_TAGS = ["manage-aftersales"]
CanManageOrders = HasStaffPermission.of(Permission.ORDERS_MANAGE)


def validated(schema: Any, data: Any) -> dict[str, Any]:
    serializer = schema(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


class CustomerAfterSalesView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @property
    def customer(self) -> User:
        return cast(User, self.request.user)


class MerchantAfterSalesView(StaffView):
    permission_classes = (IsAuthenticated, CanManageOrders)


class ReviewPublicView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="public_product_reviews", tags=TAGS, responses={200: paginated(PublicReviewSerializer)})
    def get(self, request: Request, product_id: Any) -> Response:
        if not Product.objects.filter(pk=product_id, status=ProductStatus.ACTIVE).exists():
            raise NotFound("Product not found.")
        reviews = ProductReview.objects.filter(
            order_item__variant__product_id=product_id, status=ReviewStatus.PUBLISHED,
        )
        summary = reviews.aggregate(rating=Avg("rating"), review_count=Count("pk"))
        items, meta = paginate(reviews.order_by("-created_at", "-pk"), request)
        return Response({
            **meta, "average_rating": summary["rating"],
            "review_count": summary["review_count"],
            "results": PublicReviewSerializer(items, many=True).data,
        })


@method_decorator(never_cache, name="dispatch")
class CustomerReviewView(CustomerAfterSalesView):
    @extend_schema(operation_id="customer_reviews", tags=TAGS, responses={200: paginated(ProductReviewSerializer)})
    def get(self, request: Request) -> Response:
        items, meta = paginate(
            ProductReview.objects.filter(customer=self.customer).order_by("-created_at", "-pk"), request,
        )
        return Response({**meta, "results": ProductReviewSerializer(items, many=True).data})

    @extend_schema(operation_id="customer_review_submit", tags=TAGS, request=ReviewInputSerializer, responses={201: ProductReviewSerializer})
    def post(self, request: Request) -> Response:
        data = validated(ReviewInputSerializer, request.data)
        review = services.submit_review(
            customer=self.customer, item_id=data["order_item"],
            rating=data["rating"], body=data["body"],
        )
        return Response(ProductReviewSerializer(review).data, status=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class CustomerCaseListView(CustomerAfterSalesView):
    @extend_schema(operation_id="customer_cases", tags=TAGS, responses={200: paginated(AfterSalesCaseSerializer)})
    def get(self, request: Request) -> Response:
        cases, meta = paginate(
            AfterSalesCase.objects.filter(customer=self.customer).prefetch_related("messages").order_by("-created_at", "-pk"),
            request,
        )
        return Response({**meta, "results": AfterSalesCaseSerializer(cases, many=True).data})

    @extend_schema(operation_id="customer_case_submit", tags=TAGS, request=CaseInputSerializer, responses={201: AfterSalesCaseSerializer})
    def post(self, request: Request) -> Response:
        data = validated(CaseInputSerializer, request.data)
        case = services.submit_case(
            customer=self.customer, kind=data["kind"], item_id=data.get("order_item"),
            quantity=data["quantity"], subject=data["subject"],
            reason=data["reason"], idempotency_key=data["idempotency_key"],
        )
        return Response(AfterSalesCaseSerializer(case).data, status=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class CustomerCaseDetailView(CustomerAfterSalesView):
    def target(self, case_id: Any) -> AfterSalesCase:
        case = AfterSalesCase.objects.filter(pk=case_id, customer=self.customer).first()
        if case is None:
            raise NotFound("Request not found.")
        return case

    @extend_schema(operation_id="customer_case_detail", tags=TAGS, responses={200: AfterSalesCaseSerializer})
    def get(self, request: Request, case_id: Any) -> Response:
        return Response(AfterSalesCaseSerializer(self.target(case_id)).data)

    @extend_schema(operation_id="customer_case_reply", tags=TAGS, request=CaseMessageInputSerializer, responses={201: CaseMessageSerializer})
    def post(self, request: Request, case_id: Any) -> Response:
        data = validated(CaseMessageInputSerializer, request.data)
        message = services.send_message(case=self.target(case_id), author=self.customer, body=data["body"])
        return Response(CaseMessageSerializer(message).data, status=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class ManageReviewView(MerchantAfterSalesView):
    @extend_schema(operation_id="manage_reviews", tags=MANAGE_TAGS, responses={200: paginated(ProductReviewSerializer)})
    def get(self, request: Request) -> Response:
        reviews, meta = paginate(ProductReview.objects.order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": ProductReviewSerializer(reviews, many=True).data})


class ManageReviewDecisionView(MerchantAfterSalesView):
    @extend_schema(operation_id="manage_review_decision", tags=MANAGE_TAGS, request=ReviewDecisionSerializer, responses={200: ProductReviewSerializer})
    def post(self, request: Request, review_id: Any) -> Response:
        data = validated(ReviewDecisionSerializer, request.data)
        review = services.moderate_review(review_id=review_id, status=data["status"], reason=data["reason"], actor=self.user)
        return Response(ProductReviewSerializer(review).data)


@method_decorator(never_cache, name="dispatch")
class ManageCaseListView(MerchantAfterSalesView):
    @extend_schema(operation_id="manage_cases", tags=MANAGE_TAGS, responses={200: paginated(AfterSalesCaseSerializer)})
    def get(self, request: Request) -> Response:
        cases, meta = paginate(AfterSalesCase.objects.prefetch_related("messages").order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": AfterSalesCaseSerializer(cases, many=True).data})


@method_decorator(never_cache, name="dispatch")
class ManageCaseDetailView(MerchantAfterSalesView):
    def target(self, case_id: Any) -> AfterSalesCase:
        case = AfterSalesCase.objects.filter(pk=case_id).first()
        if case is None:
            raise NotFound("Request not found.")
        return case

    @extend_schema(operation_id="manage_case_detail", tags=MANAGE_TAGS, responses={200: AfterSalesCaseSerializer})
    def get(self, request: Request, case_id: Any) -> Response:
        return Response(AfterSalesCaseSerializer(self.target(case_id)).data)

    @extend_schema(operation_id="manage_case_transition", tags=MANAGE_TAGS, request=CaseTransitionSerializer, responses={200: AfterSalesCaseSerializer})
    def patch(self, request: Request, case_id: Any) -> Response:
        data = validated(CaseTransitionSerializer, request.data)
        case = services.transition_case(case_id=self.target(case_id).pk, status=data["status"], reason=data["reason"], actor=self.user)
        return Response(AfterSalesCaseSerializer(case).data)

    @extend_schema(operation_id="manage_case_reply", tags=MANAGE_TAGS, request=CaseMessageInputSerializer, responses={201: CaseMessageSerializer})
    def post(self, request: Request, case_id: Any) -> Response:
        data = validated(CaseMessageInputSerializer, request.data)
        message = services.send_message(case=self.target(case_id), author=self.user, body=data["body"])
        return Response(CaseMessageSerializer(message).data, status=status.HTTP_201_CREATED)

"""Customer/guest cart and checkout HTTP endpoints."""

from typing import Any, cast

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.identity.auth import (
    CsrfProtectedAPIView,
    CustomerSessionAuthentication,
)
from commerce.identity.models import User

from . import services
from .models import Address
from .serializers import (
    AddCartItemSerializer,
    AddressInputSerializer,
    AddressSerializer,
    CartSerializer,
    CheckoutCreateSerializer,
    CheckoutReviewInputSerializer,
    CheckoutSerializer,
    CheckoutUpdateSerializer,
    UpdateCartItemSerializer,
    serialize_cart,
    serialize_checkout,
)

CART_TAG = ["cart"]
CHECKOUT_TAG = ["checkout"]


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


class StorefrontAPIView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes: tuple[type[BasePermission], ...] = (AllowAny,)

    def cart_access(self, request: Request) -> services.CartAccess:
        return services.cart_for_request(
            user=request.user, guest_token=request.COOKIES.get(services.GUEST_COOKIE)
        )

    def cart_response(
        self, data: Any, access: services.CartAccess, *, status_code: int = 200
    ) -> Response:
        response = Response(data, status=status_code)
        services.set_guest_cookie(response, access)
        return response


class CartView(StorefrontAPIView):
    @extend_schema(operation_id="cart_get", tags=CART_TAG, responses={200: CartSerializer})
    def get(self, request: Request) -> Response:
        access = self.cart_access(request)
        return self.cart_response(serialize_cart(access.cart), access)


class CartItemsView(StorefrontAPIView):
    @extend_schema(
        operation_id="cart_item_add",
        tags=CART_TAG,
        request=AddCartItemSerializer,
        responses={200: CartSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(AddCartItemSerializer(data=request.data))
        access = self.cart_access(request)
        services.add_item(
            cart=access.cart, variant_id=values["variant"], quantity=values["quantity"]
        )
        return self.cart_response(serialize_cart(access.cart), access)


class CartItemView(StorefrontAPIView):
    @extend_schema(
        operation_id="cart_item_update",
        tags=CART_TAG,
        request=UpdateCartItemSerializer,
        responses={200: CartSerializer},
    )
    def patch(self, request: Request, item_id: Any) -> Response:
        values = _data(UpdateCartItemSerializer(data=request.data))
        access = self.cart_access(request)
        services.update_item(cart=access.cart, item_id=item_id, quantity=values["quantity"])
        return self.cart_response(serialize_cart(access.cart), access)

    @extend_schema(operation_id="cart_item_remove", tags=CART_TAG, responses={200: CartSerializer})
    def delete(self, request: Request, item_id: Any) -> Response:
        access = self.cart_access(request)
        services.remove_item(cart=access.cart, item_id=item_id)
        return self.cart_response(serialize_cart(access.cart), access)


class CheckoutCreateView(StorefrontAPIView):
    @extend_schema(
        operation_id="checkout_create",
        tags=CHECKOUT_TAG,
        request=CheckoutCreateSerializer,
        responses={201: CheckoutSerializer},
    )
    def post(self, request: Request) -> Response:
        access = self.cart_access(request)
        session = services.create_checkout(cart=access.cart)
        return self.cart_response(
            serialize_checkout(session), access, status_code=status.HTTP_201_CREATED
        )


class CheckoutOwnerMixin:
    def session(self, request: Request, session_id: Any) -> Any:
        return services.owned_checkout(
            session_id=session_id,
            user=request.user,
            guest_token=request.COOKIES.get(services.GUEST_COOKIE),
        )


class CheckoutView(CheckoutOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="checkout_get", tags=CHECKOUT_TAG, responses={200: CheckoutSerializer}
    )
    def get(self, request: Request, session_id: Any) -> Response:
        return Response(serialize_checkout(self.session(request, session_id)))

    @extend_schema(
        operation_id="checkout_update",
        tags=CHECKOUT_TAG,
        request=CheckoutUpdateSerializer,
        responses={200: CheckoutSerializer},
    )
    def patch(self, request: Request, session_id: Any) -> Response:
        values = _data(CheckoutUpdateSerializer(data=request.data))
        session = services.update_checkout(self.session(request, session_id), values)
        return Response(serialize_checkout(session))


class CheckoutReviewView(CheckoutOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="checkout_review",
        tags=CHECKOUT_TAG,
        request=CheckoutReviewInputSerializer,
        responses={200: CheckoutSerializer},
    )
    def post(self, request: Request, session_id: Any) -> Response:
        values = _data(CheckoutReviewInputSerializer(data=request.data))
        user = request.user if isinstance(request.user, User) else None
        session = services.review_checkout(
            session=self.session(request, session_id),
            idempotency_key=values["idempotency_key"],
            actor=user,
        )
        return Response(serialize_checkout(session))


class CheckoutReleaseView(CheckoutOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="checkout_release",
        tags=CHECKOUT_TAG,
        request=None,
        responses={200: CheckoutSerializer},
    )
    def post(self, request: Request, session_id: Any) -> Response:
        session = services.release_checkout(session=self.session(request, session_id))
        return Response(serialize_checkout(session))


class AddressListView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="address_list",
        tags=CHECKOUT_TAG,
        responses={200: AddressSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        customer = cast(User, request.user)
        return Response(
            AddressSerializer(Address.objects.filter(customer=customer), many=True).data
        )

    @extend_schema(
        operation_id="address_create",
        tags=CHECKOUT_TAG,
        request=AddressInputSerializer,
        responses={201: AddressSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(AddressInputSerializer(data=request.data))
        address = services.save_address(
            customer=cast(User, request.user), address=None, data=values
        )
        return Response(AddressSerializer(address).data, status=status.HTTP_201_CREATED)


class AddressView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="address_update",
        tags=CHECKOUT_TAG,
        request=AddressInputSerializer,
        responses={200: AddressSerializer},
    )
    def patch(self, request: Request, address_id: Any) -> Response:
        values = _data(AddressInputSerializer(data=request.data))
        customer = cast(User, request.user)
        address = Address.objects.filter(pk=address_id, customer=customer).first()
        if address is None:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("This address is not available.")
        address = services.save_address(customer=customer, address=address, data=values)
        return Response(AddressSerializer(address).data)

    @extend_schema(operation_id="address_delete", tags=CHECKOUT_TAG, responses={204: None})
    def delete(self, request: Request, address_id: Any) -> Response:
        services.delete_address(customer=cast(User, request.user), address_id=address_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

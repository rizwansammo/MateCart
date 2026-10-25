import pytest
from django.test import Client
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from control.core.exceptions import api_exception_handler


class DefaultPolicyView(APIView):
    """A view that relies on the project-wide DRF defaults, like any future endpoint would."""

    def get(self, request: Request) -> Response:
        return Response({"secret": "data"})


def test_endpoints_deny_access_unless_they_opt_in() -> None:
    response = DefaultPolicyView.as_view()(APIRequestFactory().get("/anything/"))

    assert response.status_code == 403
    assert response.data == {
        "error": {
            "code": "permission_denied",
            "message": "You do not have permission to perform this action.",
        }
    }


def test_validation_errors_use_the_envelope_with_field_details() -> None:
    response = api_exception_handler(ValidationError({"email": ["Enter a valid email."]}), {})

    assert response is not None
    assert response.status_code == 400
    assert response.data == {
        "error": {
            "code": "validation_error",
            "message": "Invalid input.",
            "details": {"email": ["Enter a valid email."]},
        }
    }


def test_api_exceptions_keep_their_code_and_message() -> None:
    response = api_exception_handler(NotFound(), {})

    assert response is not None
    assert response.status_code == 404
    assert response.data == {"error": {"code": "not_found", "message": "Not found."}}


def test_unhandled_exceptions_are_left_to_django() -> None:
    assert api_exception_handler(RuntimeError("boom"), {}) is None


def test_unknown_urls_return_the_json_404_envelope(client: Client) -> None:
    response = client.get("/no-such-page/")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


@pytest.mark.urls("tests.error_urls")
def test_unhandled_errors_return_a_generic_json_500_with_request_id() -> None:
    response = Client(raise_request_exception=False).get("/boom/")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "server_error", "message": "Internal server error."}
    }
    assert len(response["X-Request-ID"]) == 32
    assert "internal detail" not in response.content.decode()

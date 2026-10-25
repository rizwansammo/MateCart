"""'Continue with Google' against a controlled fake of Google's endpoints and keys (no network)."""

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connections
from django.utils import timezone
from pytest_django.fixtures import Settings

from commerce.identity import google
from commerce.identity.models import (
    AccountType,
    ExternalIdentity,
    OidcAuthorization,
    User,
)
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user
from .helpers import Api

pytestmark = pytest.mark.usefixtures("store")

CLIENT_ID = "test-client.apps.googleusercontent.com"
KID = "test-key-1"


class FakeGoogle:
    """Signs ID tokens with a local key and answers JWKS and token requests."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict[str, Any] = {}
        self.token_requests: list[dict[str, str]] = []
        self.kid = KID
        self.nonce = ""

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": KID, "use": "sig", "alg": "RS256"}]}

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": "1234567890",
            "email": "rahim@gmail.com",
            "email_verified": True,
            "name": "Rahim Uddin",
            "iat": now,
            "exp": now + 3600,
            "nonce": nonce,
            **self.claims,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def __call__(self, url: str, data: dict[str, str] | None = None) -> Any:
        if url == google.JWKS_URI:
            return self.jwks()
        assert url == google.TOKEN_ENDPOINT
        assert data is not None
        self.token_requests.append(data)
        return {"id_token": self.id_token(self.nonce), "access_token": "unused"}


@pytest.fixture
def fake_google(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    settings.GOOGLE_OIDC_CLIENT_ID = CLIENT_ID
    settings.GOOGLE_OIDC_CLIENT_SECRET = "test-secret"
    fake = FakeGoogle()
    monkeypatch.setattr(google, "http_json", fake)
    monkeypatch.setitem(google._jwks, "set", None)
    return fake


def start(api: Api, fake: FakeGoogle, next_path: str = "/account") -> dict[str, str]:
    response = api.get("/api/account/google/start/", next=next_path)
    assert response.status_code == 302
    query = {
        key: values[0] for key, values in parse_qs(urlparse(response["Location"]).query).items()
    }
    fake.nonce = query["nonce"]
    return query


def callback(api: Api, query: dict[str, str], **overrides: str) -> Any:
    return api.get(
        "/api/account/google/callback/",
        state=overrides.get("state", query["state"]),
        code=overrides.get("code", "auth-code"),
    )


def test_google_is_hidden_until_configured(api: Api) -> None:
    assert api.get("/api/account/methods/").json()["google"] is False
    response = api.get("/api/account/google/start/")

    assert response.status_code == 302
    assert response["Location"] == "/account/sign-in?error=google_unavailable"


def test_start_uses_state_nonce_and_pkce(api: Api, fake_google: FakeGoogle) -> None:
    response = api.get("/api/account/google/start/")
    location = urlparse(response["Location"])
    query = parse_qs(location.query)

    assert f"{location.scheme}://{location.netloc}{location.path}" == google.AUTHORIZATION_ENDPOINT
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["http://testserver/api/account/google/callback/"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 43
    assert len(query["nonce"][0]) >= 43
    binding = response.cookies[google.BINDING_COOKIE]
    assert binding["httponly"]
    assert binding["path"] == google.BINDING_COOKIE_PATH


def test_new_google_account_signs_in(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    query = start(api, fake_google, next_path="/account?tab=orders")

    response = callback(api, query)

    assert response.status_code == 302
    assert response["Location"] == "/account?tab=orders"
    me = api.get("/api/account/me/").json()
    assert me["email"] == "rahim@gmail.com"
    assert me["google_linked"] is True
    assert me["has_password"] is False
    sent = fake_google.token_requests[0]
    assert sent["code"] == "auth-code"
    assert sent["code_verifier"]
    assert sent["client_secret"] == "test-secret"


def test_returning_google_user_is_found_by_subject_not_email(
    api: Api, make_api: Callable[[], Api], fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    callback(api, start(api, fake_google))
    fake_google.claims = {"email": "renamed@gmail.com"}

    second = make_api()
    callback(second, start(second, fake_google))

    with tenant_context(deployment_tenant):
        assert User.objects.count() == 1
    assert second.get("/api/account/me/").json()["email"] == "rahim@gmail.com"


def test_existing_email_account_is_never_merged_automatically(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    make_user(deployment_tenant, account_type=AccountType.CUSTOMER, email="rahim@gmail.com")

    response = callback(api, start(api, fake_google))

    assert response["Location"] == "/account/sign-in?error=google_email_in_use"
    assert api.get("/api/account/me/").status_code == 401
    with tenant_context(deployment_tenant):
        assert not ExternalIdentity.objects.exists()


def test_unverified_google_emails_are_refused(api: Api, fake_google: FakeGoogle) -> None:
    fake_google.claims = {"email_verified": False}

    response = callback(api, start(api, fake_google))

    assert response["Location"] == "/account/sign-in?error=google_email_unverified"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
        {"nonce": "replayed-nonce"},
        {"azp": "someone-else.apps.googleusercontent.com"},
    ],
)
def test_invalid_id_tokens_are_refused(
    api: Api, fake_google: FakeGoogle, claims: dict[str, Any]
) -> None:
    fake_google.claims = claims

    response = callback(api, start(api, fake_google))

    assert response["Location"] == "/account/sign-in?error=google_failed"
    assert api.get("/api/account/me/").status_code == 401


def test_tokens_signed_by_another_key_are_refused(
    api: Api, fake_google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = start(api, fake_google)
    published = fake_google.jwks()
    fake_google.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(fake_google, "jwks", lambda: published)

    response = callback(api, query)

    assert response["Location"] == "/account/sign-in?error=google_failed"


def test_wrong_browser_binding_does_not_consume_valid_state(
    api: Api, make_api: Callable[[], Api], fake_google: FakeGoogle
) -> None:
    query = start(api, fake_google)

    other_browser = make_api()
    stolen = callback(other_browser, query)
    assert stolen["Location"] == "/account/sign-in?error=google_failed"

    original = callback(api, query)
    replay = callback(api, query)

    assert original["Location"] == "/account"
    assert replay["Location"] == "/account/sign-in?error=google_failed"


def test_missing_browser_binding_does_not_consume_valid_state(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    query = start(api, fake_google)
    binding = api.client.cookies[google.BINDING_COOKIE].value
    del api.client.cookies[google.BINDING_COOKIE]

    missing = callback(api, query)

    assert missing["Location"] == "/account/sign-in?error=google_failed"
    with tenant_context(deployment_tenant):
        assert OidcAuthorization.objects.get().used_at is None

    api.client.cookies[google.BINDING_COOKIE] = binding
    assert callback(api, query)["Location"] == "/account"


@pytest.mark.django_db(transaction=True)
def test_concurrent_callbacks_can_claim_an_authorization_only_once(
    fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    with tenant_context(deployment_tenant):
        url, binding = google.begin(purpose="sign_in", next_path="/account")
    state = parse_qs(urlparse(url).query)["state"][0]
    barrier = threading.Barrier(2)

    def claim() -> bool:
        connections.close_all()
        try:
            with tenant_context(deployment_tenant):
                barrier.wait(timeout=5)
                google._claim(state, binding)
            return True
        except google.GoogleSignInFailed:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: claim(), range(2)))

    assert sorted(results) == [False, True]
    with tenant_context(deployment_tenant):
        assert OidcAuthorization.objects.get().used_at is not None


@pytest.mark.parametrize(
    "failure",
    ["timeout", "connection", "invalid_json", "non_object", "empty_keys", "invalid_key"],
)
def test_jwks_provider_failures_are_safe(
    api: Api,
    fake_google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
    deployment_tenant: Tenant,
    failure: str,
) -> None:
    query = start(api, fake_google)

    def provider(url: str, data: dict[str, str] | None = None) -> Any:
        if url != google.JWKS_URI:
            return fake_google(url, data)
        if failure == "timeout":
            raise TimeoutError("provider timed out")
        if failure == "connection":
            raise ConnectionError("provider unavailable")
        if failure == "invalid_json":
            raise json.JSONDecodeError("invalid provider JSON", "", 0)
        if failure == "non_object":
            return []
        if failure == "empty_keys":
            return {"keys": []}
        return {"keys": [{"kid": KID, "kty": "unsupported"}]}

    monkeypatch.setattr(google, "http_json", provider)
    response = callback(api, query)

    assert response.status_code == 302
    assert response["Location"] == "/account/sign-in?error=google_failed"
    assert api.get("/api/account/me/").status_code == 401
    with tenant_context(deployment_tenant):
        assert not ExternalIdentity.objects.exists()


def test_malformed_id_token_is_a_safe_failure(
    api: Api, fake_google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = start(api, fake_google)

    def malformed_token(url: str, data: dict[str, str] | None = None) -> Any:
        if url == google.TOKEN_ENDPOINT:
            return {"id_token": "not-a-jwt"}
        return fake_google(url, data)

    monkeypatch.setattr(google, "http_json", malformed_token)

    response = callback(api, query)

    assert response["Location"] == "/account/sign-in?error=google_failed"
    assert api.get("/api/account/me/").status_code == 401


def test_programmer_errors_are_not_misclassified_as_provider_failures(
    fake_google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = fake_google.id_token("nonce")

    def broken_signing_key(_kid: str) -> Any:
        raise TypeError("sentinel programmer error")

    monkeypatch.setattr(google, "_signing_key", broken_signing_key)

    with pytest.raises(TypeError, match="sentinel programmer error"):
        google.validate_id_token(token, nonce_hash="unused")


def test_code_exchange_transport_failure_is_safe(
    api: Api, fake_google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = start(api, fake_google)

    def unavailable(url: str, data: dict[str, str] | None = None) -> Any:
        if url == google.TOKEN_ENDPOINT:
            raise ConnectionError("provider unavailable")
        return fake_google(url, data)

    monkeypatch.setattr(google, "http_json", unavailable)

    response = callback(api, query)

    assert response["Location"] == "/account/sign-in?error=google_failed"
    assert api.get("/api/account/me/").status_code == 401


def test_expired_authorizations_are_refused(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    query = start(api, fake_google)
    with tenant_context(deployment_tenant):
        OidcAuthorization.objects.update(expires_at=timezone.now())

    response = callback(api, query)

    assert response["Location"] == "/account/sign-in?error=google_failed"


def test_open_redirects_are_neutralised(api: Api, fake_google: FakeGoogle) -> None:
    for target in ("https://evil.example.com", "//evil.example.com", "/\\evil.example.com"):
        query = start(api, fake_google, next_path=target)
        response = callback(api, query)
        assert response["Location"] == "/account"
        api.post("/api/account/logout/")


def test_cancelled_consent_returns_to_sign_in(api: Api, fake_google: FakeGoogle) -> None:
    start(api, fake_google)

    response = api.get("/api/account/google/callback/", error="access_denied")

    assert response["Location"] == "/account/sign-in?error=google_cancelled"


def test_signed_in_customer_links_google_explicitly(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    make_user(
        deployment_tenant,
        account_type=AccountType.CUSTOMER,
        email="rahim@example.com",
        password="correct-horse-battery-9",
    )
    api.post(
        "/api/account/login/",
        {"email": "rahim@example.com", "password": "correct-horse-battery-9"},
    )

    started = api.post("/api/account/link/google/start/")
    assert started.status_code == 200
    query = {k: v[0] for k, v in parse_qs(urlparse(started.json()["url"]).query).items()}
    fake_google.nonce = query["nonce"]
    response = callback(api, query)

    assert response["Location"] == "/account?google=linked"
    assert api.get("/api/account/me/").json()["google_linked"] is True


def test_linking_requires_the_same_signed_in_customer(
    api: Api, fake_google: FakeGoogle, deployment_tenant: Tenant
) -> None:
    make_user(
        deployment_tenant,
        account_type=AccountType.CUSTOMER,
        email="rahim@example.com",
        password="correct-horse-battery-9",
    )
    api.post(
        "/api/account/login/",
        {"email": "rahim@example.com", "password": "correct-horse-battery-9"},
    )
    started = api.post("/api/account/link/google/start/").json()
    query = {k: v[0] for k, v in parse_qs(urlparse(started["url"]).query).items()}
    fake_google.nonce = query["nonce"]
    api.post("/api/account/logout/")

    response = callback(api, query)

    assert response["Location"] == "/account/sign-in?error=google_failed"
    with tenant_context(deployment_tenant):
        assert not ExternalIdentity.objects.exists()

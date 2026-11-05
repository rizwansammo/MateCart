"""Phase 15 outbound HTTPS guard (D-103): SSRF, DNS rebinding, redirects and credentials."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from commerce.core import outbound


def _resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    def fake(host: str, port: int, type: int = 0) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://shop.example.com/", "https_required"),
        ("https://127.0.0.1/", "host_must_be_name"),
        ("https://[::1]/", "host_must_be_name"),
        ("https://localhost/", "host_invalid"),
        ("https://printer.local/", "host_invalid"),
        ("https://user:pass@shop.example.com/", "url_invalid"),
        ("https://shop.example.com:8443/", "port_not_allowed"),
        ("https://intranet.corp/", "host_not_public"),
        ("ftp://shop.example.com/", "https_required"),
    ],
)
def test_unsafe_urls_are_refused_before_any_connection(url: str, code: str) -> None:
    with pytest.raises(outbound.OutboundError) as error:
        outbound.parse_url(url)
    assert error.value.code == code


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "192.168.1.2", "169.254.169.254", "127.0.0.1", "100.64.0.1"]
)
def test_names_resolving_to_private_addresses_are_refused(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _resolve_to(monkeypatch, address)
    calls: list[Any] = []
    monkeypatch.setattr(outbound, "transport", lambda *args: calls.append(args))
    with pytest.raises(outbound.OutboundError) as error:
        outbound.request("GET", "https://rebind.example.com/products")
    assert error.value.code == "host_not_public"
    assert calls == []


def test_connection_is_pinned_to_the_vetted_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, "93.184.215.14")
    seen: list[outbound.Target] = []

    def transport(target: outbound.Target, *args: Any) -> outbound.Response:
        seen.append(target)
        return outbound.Response(200, {}, b"ok", "")

    monkeypatch.setattr(outbound, "transport", transport)
    response = outbound.request("GET", "https://shop.example.com/a?b=1")
    assert response.body == b"ok"
    assert seen[0].address == "93.184.215.14"
    assert seen[0].host == "shop.example.com"
    assert seen[0].path == "/a?b=1"


def test_redirects_are_refused_unless_allowed_and_drop_credentials_across_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, "93.184.215.14")
    hops: list[tuple[str, dict[str, str]]] = []

    def transport(
        target: outbound.Target, method: str, headers: Any, *args: Any
    ) -> outbound.Response:
        hops.append((target.host, dict(headers)))
        if target.host == "shop.example.com":
            return outbound.Response(302, {"location": "https://cdn.example.net/img.png"}, b"", "")
        return outbound.Response(200, {}, b"img", "")

    monkeypatch.setattr(outbound, "transport", transport)
    with pytest.raises(outbound.OutboundError) as error:
        outbound.request(
            "GET", "https://shop.example.com/x", headers={"Authorization": "Basic s3cret"}
        )
    assert error.value.code == "redirect_refused"
    hops.clear()
    response = outbound.request(
        "GET",
        "https://shop.example.com/x",
        headers={"Authorization": "Basic s3cret"},
        policy=outbound.Policy(max_redirects=2),
    )
    assert response.body == b"img"
    assert "Authorization" in hops[0][1]
    assert "Authorization" not in hops[1][1]


def test_redirect_to_a_private_target_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, "93.184.215.14")
    monkeypatch.setattr(
        outbound,
        "transport",
        lambda *args: outbound.Response(
            302, {"location": "http://169.254.169.254/latest"}, b"", ""
        ),
    )
    with pytest.raises(outbound.OutboundError) as error:
        outbound.request(
            "GET", "https://shop.example.com/", policy=outbound.Policy(max_redirects=3)
        )
    assert error.value.code == "https_required"


def test_host_allowlist_applies_to_every_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = outbound.Policy(host_allowed=lambda host: host == "api.groq.com")
    with pytest.raises(outbound.OutboundError) as error:
        outbound.request("GET", "https://evil.example.com/", policy=policy)
    assert error.value.code == "host_not_allowed"

import json
import logging
import re

import pytest
from django.test import Client

from control.core.logging import JsonFormatter, RequestIdFilter
from control.core.request_context import request_id_var


def _record() -> logging.LogRecord:
    return logging.LogRecord("tests", logging.INFO, __file__, 1, "hello %s", ("world",), None)


def test_generates_a_request_id_when_none_is_sent(client: Client) -> None:
    response = client.get("/api/health/live/")

    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_reuses_a_well_formed_incoming_request_id(client: Client) -> None:
    response = client.get("/api/health/live/", headers={"X-Request-ID": "proxy-abc-12345"})

    assert response["X-Request-ID"] == "proxy-abc-12345"


@pytest.mark.parametrize(
    "incoming", ["short", "has spaces in it", "x" * 65, "<script>alert</script>"]
)
def test_replaces_malformed_incoming_request_ids(client: Client, incoming: str) -> None:
    response = client.get("/api/health/live/", headers={"X-Request-ID": incoming})

    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_log_records_carry_the_current_request_id() -> None:
    record = _record()
    token = request_id_var.set("req-12345678")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)

    assert getattr(record, "request_id", None) == "req-12345678"


def test_log_records_outside_requests_use_a_placeholder() -> None:
    record = _record()
    RequestIdFilter().filter(record)

    assert getattr(record, "request_id", None) == "-"


def test_json_formatter_emits_one_parseable_object_per_record() -> None:
    record = _record()
    RequestIdFilter().filter(record)

    payload = json.loads(JsonFormatter(service="control-api").format(record))

    assert payload["service"] == "control-api"
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "-"

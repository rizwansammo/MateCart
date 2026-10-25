"""Controlled test doubles. Never configured outside tests: production settings refuse any
MATECART_SMS_PROVIDER under ``tests.``."""

import uuid
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class SentSms:
    to: str
    body: str
    tenant_id: uuid.UUID
    purpose: str


class FakeSmsProvider:
    outbox: ClassVar[list[SentSms]] = []

    def send(self, *, to: str, body: str, tenant_id: uuid.UUID, purpose: str) -> None:
        self.outbox.append(SentSms(to=to, body=body, tenant_id=tenant_id, purpose=purpose))

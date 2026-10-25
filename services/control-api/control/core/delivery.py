"""Outbound email for console sign-in (activation links, reset links, sign-in codes).

Messages are sent after the surrounding transaction commits, never twice because of a retry, and
their contents (codes, links) are never logged.
"""

import logging
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_email(*, to: str, subject: str, template: str, context: dict[str, Any]) -> None:
    def deliver() -> None:
        text = render_to_string(f"email/{template}.txt", context)
        html = render_to_string(f"email/{template}.html", context)
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
        message.attach_alternative(html, "text/html")
        try:
            message.send()
        except Exception:
            logger.error("Email delivery failed (template=%s)", template)
            return
        logger.info("Email sent (template=%s)", template)

    transaction.on_commit(deliver, robust=True)

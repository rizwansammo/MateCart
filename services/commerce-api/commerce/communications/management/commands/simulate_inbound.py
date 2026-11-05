"""Development only (D-097): feed a customer message from a simulated channel into the inbox.

The payload is built in Meta's documented webhook format and handed to the same processing that
runs after a webhook signature is verified (``inbound.process``), so routing, deduplication,
conversation rules, notifications and real-time events are all exercised. It refuses to run
unless ``MATECART_CHANNEL_SIMULATOR`` is on and the channel has a simulated connection; it never
contacts Meta.

    python manage.py simulate_inbound --channel whatsapp --sender 8801711000001 \
        --name "Sadia Rahman" --text "Is the blue kurti available in M?"
"""

import time
import uuid
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from commerce.communications import inbound
from commerce.communications.models import ChannelConnection
from commerce.tenancy.context import tenant_context
from commerce.tenancy.resolution import get_deployment_tenant


class Command(BaseCommand):
    help = "Simulate an inbound message on a development simulator connection."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--channel", choices=["whatsapp", "messenger", "instagram"], required=True
        )
        parser.add_argument("--sender", required=True, help="Provider-scoped sender id or wa_id.")
        parser.add_argument("--name", default="", help="Profile name the provider would report.")
        parser.add_argument("--text", required=True)
        parser.add_argument("--message-id", default="", help="Reuse an id to test deduplication.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.MATECART_CHANNEL_SIMULATOR:
            raise CommandError("The channel simulator is off (MATECART_CHANNEL_SIMULATOR).")
        channel = options["channel"]
        with tenant_context(get_deployment_tenant()):
            connection = ChannelConnection.objects.filter(
                channel=channel, is_simulated=True, disconnected_at__isnull=True
            ).first()
            if connection is None:
                raise CommandError(
                    f"Connect the {channel} simulator first (Settings > Social channels)."
                )
            mid = options["message_id"] or f"sim.in.{uuid.uuid4().hex}"
            if channel == "whatsapp":
                payload: dict[str, Any] = {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "id": connection.business_account_id,
                            "changes": [
                                {
                                    "field": "messages",
                                    "value": {
                                        "messaging_product": "whatsapp",
                                        "metadata": {
                                            "phone_number_id": connection.external_account_id
                                        },
                                        "contacts": [
                                            {
                                                "profile": {"name": options["name"]},
                                                "wa_id": options["sender"],
                                            }
                                        ],
                                        "messages": [
                                            {
                                                "from": options["sender"],
                                                "id": mid,
                                                "timestamp": str(int(time.time())),
                                                "type": "text",
                                                "text": {"body": options["text"]},
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            else:
                payload = {
                    "object": "page" if channel == "messenger" else "instagram",
                    "entry": [
                        {
                            "id": connection.external_account_id,
                            "messaging": [
                                {
                                    "sender": {"id": options["sender"]},
                                    "timestamp": int(time.time() * 1000),
                                    "message": {"mid": mid, "text": options["text"]},
                                }
                            ],
                        }
                    ],
                }
            result = inbound.process(payload)
        self.stdout.write(
            f"stored={result.stored} duplicates={result.duplicates} ignored={result.ignored}"
        )

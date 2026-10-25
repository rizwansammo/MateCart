from io import StringIO

from django.core.management import call_command

from commerce.core.tasks import ping
from config.celery import app


def test_ping_task_returns_pong() -> None:
    assert ping.delay().get(timeout=5) == "pong"


def test_check_celery_command_reports_the_round_trip() -> None:
    out = StringIO()
    call_command("check_celery", "--timeout", "5", stdout=out)

    assert "Celery round-trip OK" in out.getvalue()


def test_celery_uses_json_and_reliable_acknowledgement() -> None:
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1

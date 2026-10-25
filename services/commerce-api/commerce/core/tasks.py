from celery import shared_task


@shared_task(name="core.ping")
def ping() -> str:
    """Round-trip probe used by `manage.py check_celery`. Idempotent and side-effect free."""
    return "pong"

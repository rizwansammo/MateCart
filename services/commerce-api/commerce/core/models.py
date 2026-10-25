"""Abstract model conventions shared by every MateCart table. See docs/DATA_MODEL.md."""

from django.db import models

from .ids import uuid7


class UUIDModel(models.Model):
    """UUIDv7 primary key (see commerce.core.ids)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    """Creation and last-modification times. QuerySet.update() does not touch updated_at;
    set it explicitly in bulk updates."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

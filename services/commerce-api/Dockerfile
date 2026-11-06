# syntax=docker/dockerfile:1
# MateCart commerce-api.
#   dev  - local development image (bind-mounted source, dev tools, reload; compose.yaml).
#   prod - production image (D-117): locked runtime dependencies only, non-root, read-only
#          source, one image for the ASGI API, Celery worker, Celery beat and the migrate job.
#          The process role is the first argument of bin/matecart-entrypoint.

ARG PYTHON_IMAGE=python:3.13.17-slim-trixie

FROM ${PYTHON_IMAGE} AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system --gid 10001 matecart \
    && useradd --system --uid 10001 --gid matecart --create-home --shell /usr/sbin/nologin matecart \
    && install -d -o matecart -g matecart -m 0750 /var/lib/matecart/media

# Media root for uploads. A new named volume copies this directory's ownership, so the non-root
# user can write to it.

WORKDIR /app

FROM base AS dev

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY . .

USER matecart
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---------------------------------------------------------------- production
FROM base AS prod-deps

COPY pyproject.toml uv.lock .python-version ./
# Runtime dependencies only, exactly as locked; no dev tools (pytest, mypy, ruff) in production.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

FROM ${PYTHON_IMAGE} AS prod

ARG MATECART_VERSION=unversioned
ARG MATECART_GIT_SHA=unknown
LABEL org.opencontainers.image.title="matecart-commerce-api" \
      org.opencontainers.image.vendor="NetaMate Solutions" \
      org.opencontainers.image.version="${MATECART_VERSION}" \
      org.opencontainers.image.revision="${MATECART_GIT_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    MATECART_APPLICATION_VERSION="${MATECART_VERSION}"

RUN groupadd --system --gid 10001 matecart \
    && useradd --system --uid 10001 --gid matecart --no-create-home --shell /usr/sbin/nologin matecart \
    && install -d -o matecart -g matecart -m 0750 /var/lib/matecart/media

COPY --from=prod-deps /opt/venv /opt/venv
WORKDIR /app
# Source is owned by root and read-only for the runtime user; .dockerignore keeps .env files,
# caches and tests out of the image.
COPY --chown=root:root --chmod=0755 bin/matecart-entrypoint /usr/local/bin/matecart-entrypoint
COPY --chown=root:root . .
RUN python -m compileall -q /app/config /app/commerce \
    && rm -rf /app/tests /app/bin

USER matecart
EXPOSE 8000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/matecart-entrypoint"]
CMD ["api"]

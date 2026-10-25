# syntax=docker/dockerfile:1
# MateCart control-api (NetaMate Central Platform Console backend) — local development image.
# The hardened production image is defined in Phase 17.

FROM python:3.13.17-slim-trixie AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system --gid 10001 matecart \
    && useradd --system --uid 10001 --gid matecart --create-home --shell /usr/sbin/nologin matecart

WORKDIR /app

FROM base AS dev

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY . .

USER matecart
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

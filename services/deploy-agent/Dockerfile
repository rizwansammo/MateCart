# syntax=docker/dockerfile:1
# Test/tooling image for the Deployment Agent (compose profile "tools"). The Agent itself is
# installed on Linux hosts by installer/bootstrap.sh and has no runtime dependencies.
FROM python:3.13.17-slim-trixie AS dev
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH="/opt/venv/bin:${PATH}"
RUN groupadd --system --gid 10001 matecart \
    && useradd --system --uid 10001 --gid matecart --create-home --shell /usr/sbin/nologin matecart
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project
COPY . .
USER matecart
CMD ["pytest"]

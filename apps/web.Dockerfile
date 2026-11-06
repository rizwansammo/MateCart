# syntax=docker/dockerfile:1
# MateCart Next.js production image (Phase 17, D-117), built from the repository root:
#   docker build -f apps/web.Dockerfile --build-arg APP=commerce-web     -t <image> .
#   docker build -f apps/web.Dockerfile --build-arg APP=platform-console -t <image> .
#
# Next.js standalone output (MATECART_NEXT_OUTPUT=standalone, see next.config.ts): only the
# traced runtime files are copied. No .env file, no secret and no NEXT_PUBLIC_* value is baked in;
# runtime settings (COMMERCE_API_INTERNAL_URL, STOREFRONT_PUBLIC_URL, CONTROL_API_INTERNAL_URL)
# are read on the server at request time.

ARG NODE_IMAGE=node:24.18.1-trixie-slim

FROM ${NODE_IMAGE} AS build
ARG APP
ENV CI=true \
    NEXT_TELEMETRY_DISABLED=1 \
    MATECART_NEXT_OUTPUT=standalone \
    PNPM_HOME=/pnpm \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN test -n "$APP" && corepack enable
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY packages packages
COPY apps/${APP} apps/${APP}
# Locked install of this app and its workspace dependencies only; install scripts stay denied
# by pnpm-workspace.yaml (allowBuilds).
RUN --mount=type=cache,id=matecart-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter "@matecart/${APP}..."
RUN pnpm --filter "@matecart/${APP}" build \
    && test -f "apps/${APP}/.next/standalone/apps/${APP}/server.js"

FROM ${NODE_IMAGE} AS prod
ARG APP
ARG MATECART_VERSION=unversioned
ARG MATECART_GIT_SHA=unknown
LABEL org.opencontainers.image.title="matecart-${APP}" \
      org.opencontainers.image.vendor="NetaMate Solutions" \
      org.opencontainers.image.version="${MATECART_VERSION}" \
      org.opencontainers.image.revision="${MATECART_GIT_SHA}"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=build --chown=root:root /repo/apps/${APP}/.next/standalone ./
COPY --from=build --chown=root:root /repo/apps/${APP}/.next/static ./apps/${APP}/.next/static
# The only writable path is the Next.js cache (a tmpfs in production Compose).
RUN install -d -o node -g node -m 0750 /app/apps/${APP}/.next/cache
WORKDIR /app/apps/${APP}
USER node
EXPOSE 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]

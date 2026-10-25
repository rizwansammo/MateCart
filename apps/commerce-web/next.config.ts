import type { NextConfig } from 'next';

// Baseline headers only. A Content-Security-Policy is deferred until payment, analytics and
// pixel integrations are known (Phases 08 and 12), so it can be designed once with nonces.
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

// The browser only ever talks to this origin: /api/* is forwarded to commerce-api, so session and
// CSRF cookies stay first-party and no CORS is needed. In deployments the reverse proxy routes
// /api/* to the API directly (Phase 17); this rewrite serves local development and `next start`.
const apiOrigin = process.env.COMMERCE_API_INTERNAL_URL?.trim().replace(/\/+$/, '');

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Authentication URLs can carry single-use link tokens or OIDC codes. Keep them out of
  // dev access logs and disable forwarding browser console output (setup contains secrets).
  logging: {
    incomingRequests: { ignore: [/^\/api\//, /^\/account/, /^\/manage/] },
    browserToTerminal: false,
  },
  // Django's routes end with "/"; keep the slash instead of redirecting it away.
  skipTrailingSlashRedirect: true,
  poweredByHeader: false,
  typedRoutes: true,
  transpilePackages: ['@matecart/shared-ui', '@matecart/shared-contracts'],
  // Root AGENTS.md is the single source of assistant rules; stop `next dev` writing apps/*/AGENTS.md.
  agentRules: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
  async rewrites() {
    if (!apiOrigin) return [];
    return [
      { source: '/api/:path*/', destination: `${apiOrigin}/api/:path*/` },
      { source: '/api/:path*', destination: `${apiOrigin}/api/:path*` },
    ];
  },
};

export default nextConfig;

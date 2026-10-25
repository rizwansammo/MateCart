import type { NextConfig } from 'next';

// Baseline headers only. A Content-Security-Policy is deferred until payment, analytics and
// pixel integrations are known (Phases 08 and 12), so it can be designed once with nonces.
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: true,
  transpilePackages: ['@matecart/shared-ui', '@matecart/shared-contracts'],
  // Root AGENTS.md is the single source of assistant rules; stop `next dev` writing apps/*/AGENTS.md.
  agentRules: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;

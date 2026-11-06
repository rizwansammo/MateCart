import { describe, expect, it } from 'vitest';
import {
  clientPage,
  consoleSession,
  deploymentPage,
  monitoringCredentialPage,
  monitoringObservationPage,
  platformCapabilities,
  platformOverview,
} from './platform';

describe('central platform contracts', () => {
  it('decodes a role-aware console session', () => {
    expect(
      consoleSession({
        id: 'admin-1',
        email: 'owner@example.test',
        full_name: 'Owner',
        role: 'platform_owner',
        permissions: ['clients.read', 'clients.manage'],
      }).role,
    ).toBe('platform_owner');
  });

  it('decodes factual overview totals', () => {
    expect(
      platformOverview({
        clients: 1,
        stores: 2,
        servers: 1,
        deployments: 2,
        healthy: 0,
        degraded: 0,
        unavailable: 0,
        stale: 1,
        unknown: 1,
        critical_alerts: 1,
        pending_operations: 2,
        recent_activity: [],
      }).unknown,
    ).toBe(1);
  });

  it('decodes configuration capabilities and operational monitoring metadata', () => {
    expect(
      platformCapabilities({
        version: 1,
        presets: [{ id: 'general', name: 'General', default_theme: 'general-marketplace' }],
        themes: [{ id: 'general-marketplace', preset: 'general', name: 'General' }],
        modules: [
          {
            key: 'catalog',
            name: 'Catalogue',
            presets: ['general'],
            available: true,
            core: true,
            requires: [],
          },
        ],
      }).modules[0]?.core,
    ).toBe(true);
    expect(
      monitoringCredentialPage({
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            id: 'credential-1',
            deployment_id: 'deployment-1',
            label: 'Primary',
            last_four: '1234',
            created_at: '2026-11-06T00:00:00Z',
            last_used_at: null,
            revoked_at: null,
            status: 'active',
          },
        ],
      }).results[0]?.last_four,
    ).toBe('1234');
    expect(
      monitoringObservationPage({
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            id: 'observation-1',
            deployment_id: 'deployment-1',
            store_name: 'Store',
            observed_at: '2026-11-06T00:00:00Z',
            response_ms: null,
            cpu_percent: null,
            ram_percent: '42.00',
            disk_percent: null,
            visitor_count_24h: 12,
            application_version: 'v1',
            health_state: 'healthy',
            freshness: 'current',
          },
        ],
      }).results[0]?.cpu_percent,
    ).toBeNull();
  });

  it('rejects malformed registry pages', () => {
    expect(() => clientPage({ count: 'one', next: null, previous: null, results: [] })).toThrow();
  });

  it('keeps unknown deployment health distinct from success', () => {
    const result = deploymentPage({
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          id: 'deployment-1',
          store: 'store-1',
          store_name: 'Store',
          server: 'server-1',
          server_name: 'Server',
          environment: 'production',
          desired_version: '',
          observed_version: '',
          state: 'awaiting_deployment_integration',
          health_state: 'unknown',
          last_observed_at: null,
        },
      ],
    });
    expect(result.results[0]?.health_state).toBe('unknown');
  });
});

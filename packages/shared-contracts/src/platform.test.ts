import { describe, expect, it } from 'vitest';
import { clientPage, consoleSession, deploymentPage, platformOverview } from './platform';

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
        stale: 1,
        unknown: 1,
        critical_alerts: 1,
        pending_operations: 2,
        recent_activity: [],
      }).unknown,
    ).toBe(1);
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

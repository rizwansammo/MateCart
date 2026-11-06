import { describe, expect, it } from 'vitest';
import {
  clientPage,
  deploymentAutomation,
  releasePage,
  serverAgentState,
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

describe('deployment automation contracts', () => {
  it('decodes agent, readiness, verified commands and backup policy', () => {
    const value = deploymentAutomation({
      agent: {
        id: 'agent-1',
        server_id: 'server-1',
        status: 'active',
        connection: 'connected',
        agent_version: '1.0.0',
        host_facts: { os: 'Ubuntu 24.04' },
        enrolled_at: '2026-11-06T00:00:00Z',
        last_seen_at: '2026-11-06T00:01:00Z',
        credential_expires_at: '2027-02-04T00:00:00Z',
        revoked_at: null,
      },
      readiness: { ready: false, checks: { agent_connected: true, domain_verified: false } },
      rollback_eligible: true,
      commands: [
        {
          id: 'cmd-1',
          kind: 'update_release',
          status: 'awaiting_verification',
          operation_id: 'op-1',
          target_version: '1.1.0',
          previous_version: '1.0.0',
          created_at: '2026-11-06T00:00:00Z',
          started_at: '2026-11-06T00:00:10Z',
          finished_at: '2026-11-06T00:02:00Z',
          verified_at: null,
          progress: [{ sequence: 1, event: 'started', stage: '', message: '', at: 'x' }],
          failure_category: '',
          failure_detail: '',
        },
      ],
      backup_policy: null,
    });
    expect(value.agent?.connection).toBe('connected');
    expect(value.readiness.checks.domain_verified).toBe(false);
    expect(value.commands[0]?.verified_at).toBeNull();
    expect(value.backup_policy).toBeNull();
  });

  it('rejects malformed release pages and decodes digest references', () => {
    expect(() => releasePage({ count: 1, next: null, previous: null, results: [{}] })).toThrow();
    const page = releasePage({
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          id: 'r-1',
          version: '1.0.0',
          status: 'available',
          git_commit: 'f'.repeat(40),
          created_at: '2026-11-06T00:00:00Z',
          manifest_sha256: 'a'.repeat(64),
          images: { 'commerce-api': `ghcr.io/o/matecart-commerce-api@sha256:${'b'.repeat(64)}` },
        },
      ],
    });
    expect(page.results[0]?.images['commerce-api']).toContain('@sha256:');
    expect(serverAgentState({ agent: null, enrollments: [] }).agent).toBeNull();
  });
});

import { array, boolean, number, object, string, type Decoder } from './auth';

interface PlatformPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

function nullable<T>(decoder: Decoder<T>): Decoder<T | null> {
  return (value) => (value === null ? null : decoder(value));
}

function page<T>(decoder: Decoder<T>): Decoder<PlatformPage<T>> {
  return (value) => {
    const row = object(value);
    return {
      count: number(row.count),
      next: nullable(string)(row.next),
      previous: nullable(string)(row.previous),
      results: array(row.results, decoder),
    };
  };
}

export interface ConsoleSession {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions: string[];
}
export function consoleSession(value: unknown): ConsoleSession {
  const row = object(value);
  return {
    id: string(row.id),
    email: string(row.email),
    full_name: string(row.full_name),
    role: string(row.role),
    permissions: array(row.permissions, string),
  };
}

export interface PlatformOverview {
  clients: number;
  stores: number;
  servers: number;
  deployments: number;
  healthy: number;
  degraded: number;
  unavailable: number;
  stale: number;
  unknown: number;
  critical_alerts: number;
  pending_operations: number;
  recent_activity: { id: string; action: string; resource_type: string; created_at: string }[];
}
export function platformOverview(value: unknown): PlatformOverview {
  const row = object(value);
  return {
    clients: number(row.clients),
    stores: number(row.stores),
    servers: number(row.servers),
    deployments: number(row.deployments),
    healthy: number(row.healthy),
    degraded: number(row.degraded),
    unavailable: number(row.unavailable),
    stale: number(row.stale),
    unknown: number(row.unknown),
    critical_alerts: number(row.critical_alerts),
    pending_operations: number(row.pending_operations),
    recent_activity: array(row.recent_activity, (item) => {
      const activity = object(item);
      return {
        id: string(activity.id),
        action: string(activity.action),
        resource_type: string(activity.resource_type),
        created_at: string(activity.created_at),
      };
    }),
  };
}

export interface ClientRecord {
  id: string;
  display_name: string;
  contact_email: string;
  country: string;
  status: string;
  stores_count: number;
  servers_count: number;
  updated_at: string;
}
export function clientRecord(value: unknown): ClientRecord {
  const row = object(value);
  return {
    id: string(row.id),
    display_name: string(row.display_name),
    contact_email: string(row.contact_email),
    country: string(row.country),
    status: string(row.status),
    stores_count: row.stores_count === undefined ? 0 : number(row.stores_count),
    servers_count: row.servers_count === undefined ? 0 : number(row.servers_count),
    updated_at: string(row.updated_at),
  };
}
export const clientPage = page(clientRecord);

export interface StoreRecord {
  id: string;
  client: string;
  client_name: string;
  name: string;
  handle: string;
  preset: string;
  default_currency: string;
  desired_theme: string;
  desired_modules: string[];
  applied_theme: string;
  applied_modules: string[];
  desired_revision: number;
  applied_revision: number;
  configuration_state: string;
  status: string;
  updated_at: string;
}
export function storeRecord(value: unknown): StoreRecord {
  const row = object(value);
  return {
    id: string(row.id),
    client: string(row.client),
    client_name: string(row.client_name),
    name: string(row.name),
    handle: string(row.handle),
    preset: string(row.preset),
    default_currency: string(row.default_currency),
    desired_theme: string(row.desired_theme),
    desired_modules: array(row.desired_modules, string),
    applied_theme: string(row.applied_theme),
    applied_modules: array(row.applied_modules, string),
    desired_revision: number(row.desired_revision),
    applied_revision: number(row.applied_revision),
    configuration_state: string(row.configuration_state),
    status: string(row.status),
    updated_at: string(row.updated_at),
  };
}
export const storePage = page(storeRecord);

export interface ServerRecord {
  id: string;
  name: string;
  ownership: string;
  owner_client: string | null;
  owner_name: string;
  provider: string;
  hostname: string;
  country: string;
  status: string;
  deployments_count: number;
}
export function serverRecord(value: unknown): ServerRecord {
  const row = object(value);
  return {
    id: string(row.id),
    name: string(row.name),
    ownership: string(row.ownership),
    owner_client: nullable(string)(row.owner_client),
    owner_name: string(row.owner_name),
    provider: string(row.provider),
    hostname: string(row.hostname),
    country: string(row.country),
    status: string(row.status),
    deployments_count: row.deployments_count === undefined ? 0 : number(row.deployments_count),
  };
}
export const serverPage = page(serverRecord);

export interface DeploymentRecord {
  id: string;
  store: string;
  store_name: string;
  server: string;
  server_name: string;
  environment: string;
  desired_version: string;
  observed_version: string;
  state: string;
  health_state: string;
  last_observed_at: string | null;
}
export function deploymentRecord(value: unknown): DeploymentRecord {
  const row = object(value);
  return {
    id: string(row.id),
    store: string(row.store),
    store_name: string(row.store_name),
    server: string(row.server),
    server_name: string(row.server_name),
    environment: string(row.environment),
    desired_version: string(row.desired_version),
    observed_version: string(row.observed_version),
    state: string(row.state),
    health_state: string(row.health_state),
    last_observed_at: nullable(string)(row.last_observed_at),
  };
}
export const deploymentPage = page(deploymentRecord);

export interface PlatformCapabilities {
  version: number;
  presets: { id: string; name: string; default_theme: string }[];
  themes: { id: string; preset: string; name: string }[];
  modules: {
    key: string;
    name: string;
    presets: string[];
    available: boolean;
    core: boolean;
    requires: string[];
  }[];
}
export function platformCapabilities(value: unknown): PlatformCapabilities {
  const row = object(value);
  return {
    version: number(row.version),
    presets: array(row.presets, (item) => {
      const preset = object(item);
      return {
        id: string(preset.id),
        name: string(preset.name),
        default_theme: string(preset.default_theme),
      };
    }),
    themes: array(row.themes, (item) => {
      const theme = object(item);
      return {
        id: string(theme.id),
        preset: string(theme.preset),
        name: string(theme.name),
      };
    }),
    modules: array(row.modules, (item) => {
      const specification = object(item);
      return {
        key: string(specification.key),
        name: string(specification.name),
        presets: array(specification.presets, string),
        available: boolean(specification.available),
        core: boolean(specification.core),
        requires: array(specification.requires, string),
      };
    }),
  };
}

export interface MonitoringCredentialRecord {
  id: string;
  deployment_id: string;
  label: string;
  last_four: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  status: string;
}
export function monitoringCredentialRecord(value: unknown): MonitoringCredentialRecord {
  const row = object(value);
  return {
    id: string(row.id),
    deployment_id: string(row.deployment_id),
    label: string(row.label),
    last_four: string(row.last_four),
    created_at: string(row.created_at),
    last_used_at: nullable(string)(row.last_used_at),
    revoked_at: nullable(string)(row.revoked_at),
    status: string(row.status),
  };
}
export const monitoringCredentialPage = page(monitoringCredentialRecord);

export interface MonitoringObservationRecord {
  id: string;
  deployment_id: string;
  store_name: string;
  observed_at: string;
  response_ms: number | null;
  cpu_percent: string | null;
  ram_percent: string | null;
  disk_percent: string | null;
  visitor_count_24h: number | null;
  application_version: string;
  health_state: string;
  freshness: string;
}
export function monitoringObservationRecord(value: unknown): MonitoringObservationRecord {
  const row = object(value);
  return {
    id: string(row.id),
    deployment_id: string(row.deployment_id),
    store_name: string(row.store_name),
    observed_at: string(row.observed_at),
    response_ms: nullable(number)(row.response_ms),
    cpu_percent: nullable(string)(row.cpu_percent),
    ram_percent: nullable(string)(row.ram_percent),
    disk_percent: nullable(string)(row.disk_percent),
    visitor_count_24h: nullable(number)(row.visitor_count_24h),
    application_version: string(row.application_version),
    health_state: string(row.health_state),
    freshness: string(row.freshness),
  };
}
export const monitoringObservationPage = page(monitoringObservationRecord);

export interface OperationRecord {
  id: string;
  kind: string;
  title: string;
  status: string;
  requested_by_name: string;
  created_at: string;
  updated_at: string;
}
export function operationRecord(value: unknown): OperationRecord {
  const row = object(value);
  return {
    id: string(row.id),
    kind: string(row.kind),
    title: string(row.title),
    status: string(row.status),
    requested_by_name: string(row.requested_by_name),
    created_at: string(row.created_at),
    updated_at: string(row.updated_at),
  };
}
export const operationPage = page(operationRecord);

export interface AlertRecord {
  id: string;
  category: string;
  severity: string;
  status: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export function alertRecord(value: unknown): AlertRecord {
  const row = object(value);
  return {
    id: string(row.id),
    category: string(row.category),
    severity: string(row.severity),
    status: string(row.status),
    title: string(row.title),
    created_at: string(row.created_at),
    updated_at: string(row.updated_at),
  };
}
export const alertPage = page(alertRecord);

export interface AdminRecord {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  mfa_enabled: boolean;
  activated_at: string | null;
}
export function adminRecord(value: unknown): AdminRecord {
  const row = object(value);
  return {
    id: string(row.id),
    email: string(row.email),
    full_name: string(row.full_name),
    role: string(row.role),
    is_active: boolean(row.is_active),
    mfa_enabled: boolean(row.mfa_enabled),
    activated_at: nullable(string)(row.activated_at),
  };
}
export const adminPage = page(adminRecord);

export interface AuditRecord {
  id: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  request_id: string;
  created_at: string;
}
export function auditRecord(value: unknown): AuditRecord {
  const row = object(value);
  return {
    id: string(row.id),
    actor: string(row.actor),
    action: string(row.action),
    resource_type: string(row.resource_type),
    resource_id: nullable(string)(row.resource_id),
    request_id: string(row.request_id),
    created_at: string(row.created_at),
  };
}
export const auditPage = page(auditRecord);

// ---------------------------------------------------------------- Phase 17 deployment automation

export interface AgentRecord {
  id: string;
  server_id: string;
  status: string;
  connection: string;
  agent_version: string;
  enrolled_at: string;
  last_seen_at: string | null;
  credential_expires_at: string;
  revoked_at: string | null;
}
export function agentRecord(value: unknown): AgentRecord {
  const row = object(value);
  return {
    id: string(row.id),
    server_id: string(row.server_id),
    status: string(row.status),
    connection: string(row.connection),
    agent_version: string(row.agent_version),
    enrolled_at: string(row.enrolled_at),
    last_seen_at: nullable(string)(row.last_seen_at),
    credential_expires_at: string(row.credential_expires_at),
    revoked_at: nullable(string)(row.revoked_at),
  };
}

export interface ServerAgentState {
  agent: AgentRecord | null;
  enrollments: { id: string; last_four: string; expires_at: string; used_at: string | null }[];
}
export function serverAgentState(value: unknown): ServerAgentState {
  const row = object(value);
  return {
    agent: nullable(agentRecord)(row.agent),
    enrollments: array(row.enrollments, (item) => {
      const entry = object(item);
      return {
        id: string(entry.id),
        last_four: string(entry.last_four),
        expires_at: string(entry.expires_at),
        used_at: nullable(string)(entry.used_at),
      };
    }),
  };
}

export interface CommandProgress {
  sequence: number;
  event: string;
  stage: string;
  message: string;
  at: string;
}
export interface DeploymentCommandRecord {
  id: string;
  kind: string;
  status: string;
  operation_id: string;
  target_version: string;
  previous_version: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  verified_at: string | null;
  progress: CommandProgress[];
  failure_category: string;
  failure_detail: string;
}
export function deploymentCommandRecord(value: unknown): DeploymentCommandRecord {
  const row = object(value);
  return {
    id: string(row.id),
    kind: string(row.kind),
    status: string(row.status),
    operation_id: string(row.operation_id),
    target_version: string(row.target_version),
    previous_version: string(row.previous_version),
    created_at: string(row.created_at),
    started_at: nullable(string)(row.started_at),
    finished_at: nullable(string)(row.finished_at),
    verified_at: nullable(string)(row.verified_at),
    progress: array(row.progress, (item) => {
      const entry = object(item);
      return {
        sequence: number(entry.sequence),
        event: string(entry.event),
        stage: string(entry.stage),
        message: string(entry.message),
        at: string(entry.at),
      };
    }),
    failure_category: string(row.failure_category),
    failure_detail: string(row.failure_detail),
  };
}

export interface DeploymentAutomation {
  agent: AgentRecord | null;
  readiness: { ready: boolean; checks: Record<string, boolean> };
  rollback_eligible: boolean;
  commands: DeploymentCommandRecord[];
  backup_policy: {
    enabled: boolean;
    schedule_hour_utc: number;
    schedule_minute: number;
    retention_days: number;
    include_media: boolean;
    revision: number;
  } | null;
}
export function deploymentAutomation(value: unknown): DeploymentAutomation {
  const row = object(value);
  const readiness = object(row.readiness);
  const checks = object(readiness.checks);
  return {
    agent: nullable(agentRecord)(row.agent),
    readiness: {
      ready: boolean(readiness.ready),
      checks: Object.fromEntries(Object.entries(checks).map(([key, item]) => [key, boolean(item)])),
    },
    rollback_eligible: boolean(row.rollback_eligible),
    commands: array(row.commands, deploymentCommandRecord),
    backup_policy: nullable((item: unknown) => {
      const policy = object(item);
      return {
        enabled: boolean(policy.enabled),
        schedule_hour_utc: number(policy.schedule_hour_utc),
        schedule_minute: number(policy.schedule_minute),
        retention_days: number(policy.retention_days),
        include_media: boolean(policy.include_media),
        revision: number(policy.revision),
      };
    })(row.backup_policy),
  };
}

export interface ReleaseRecord {
  id: string;
  version: string;
  status: string;
  git_commit: string;
  created_at: string;
  manifest_sha256: string;
  images: Record<string, string>;
}
export function releaseRecord(value: unknown): ReleaseRecord {
  const row = object(value);
  const images = object(row.images);
  return {
    id: string(row.id),
    version: string(row.version),
    status: string(row.status),
    git_commit: string(row.git_commit),
    created_at: string(row.created_at),
    manifest_sha256: string(row.manifest_sha256),
    images: Object.fromEntries(Object.entries(images).map(([key, item]) => [key, string(item)])),
  };
}
export const releasePage = page(releaseRecord);

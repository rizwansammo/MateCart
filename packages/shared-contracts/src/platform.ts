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

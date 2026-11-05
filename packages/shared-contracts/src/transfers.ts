/**
 * Runtime-validated data transfer responses (Phase 15): file imports, exports, store migrations,
 * staged items and read-only imported history. Source connections expose only whether a
 * credential is stored and its last characters, never the credential.
 */
import {
  array,
  boolean,
  number,
  object,
  string,
  strings,
  type Decoder,
  type JsonObject,
} from './auth';
import { decimal, isoDateTime, nullable, type Page } from './catalog';
import { managePage } from './manage';

function integer(value: unknown): number {
  const n = number(value);
  if (!Number.isInteger(n)) throw new Error('Invalid response');
  return n;
}
function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}

export const JOB_KINDS = ['import', 'export', 'migration'] as const;
export type JobKind = (typeof JOB_KINDS)[number];
export const JOB_STATUSES = [
  'analysing',
  'review',
  'queued',
  'running',
  'completed',
  'completed_with_errors',
  'failed',
  'cancelled',
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];
export const CONFLICT_STRATEGIES = ['skip', 'update', 'manual'] as const;
export type ConflictStrategy = (typeof CONFLICT_STRATEGIES)[number];
export const ITEM_ACTIONS = ['create', 'update', 'skip', 'conflict', 'invalid'] as const;
export type ItemAction = (typeof ITEM_ACTIONS)[number];
export const ITEM_STATUSES = ['pending', 'done', 'skipped', 'failed'] as const;
export type ItemStatus = (typeof ITEM_STATUSES)[number];
export const SOURCE_PLATFORMS = ['woocommerce', 'shopify'] as const;
export type SourcePlatform = (typeof SOURCE_PLATFORMS)[number];
export const SOURCE_STATUSES = [
  'unverified',
  'connected',
  'invalid_credentials',
  'permission_missing',
  'unreachable',
  'refused',
] as const;
export type SourceStatus = (typeof SOURCE_STATUSES)[number];
export const MIGRATION_SCOPES = [
  'categories',
  'products',
  'customers',
  'orders',
  'coupons',
] as const;
export type MigrationScope = (typeof MIGRATION_SCOPES)[number];

/** Jobs in these states still change on their own; the dashboard polls them. */
export function isJobActive(status: JobStatus): boolean {
  return status === 'analysing' || status === 'queued' || status === 'running';
}

export interface TransferFile {
  id: string;
  purpose: string;
  display_name: string;
  content_type: string;
  size_bytes: number;
  expires_at: string;
  created_at: string;
}
function transferFile(value: unknown): TransferFile {
  const o = object(value);
  return {
    id: string(o.id),
    purpose: string(o.purpose),
    display_name: string(o.display_name),
    content_type: string(o.content_type),
    size_bytes: integer(o.size_bytes),
    expires_at: isoDateTime(o.expires_at),
    created_at: isoDateTime(o.created_at),
  };
}

export interface JobSummary {
  total: number;
  valid: number;
  invalid: number;
  to_create: number;
  to_update: number;
  to_skip: number;
  conflicts: number;
  undecided: number;
  with_warnings: number;
}
function jobSummary(value: unknown): JobSummary | null {
  const o = object(value);
  if (o.total === undefined) return null;
  return {
    total: integer(o.total),
    valid: integer(o.valid),
    invalid: integer(o.invalid),
    to_create: integer(o.to_create),
    to_update: integer(o.to_update),
    to_skip: integer(o.to_skip),
    conflicts: integer(o.conflicts),
    undecided: integer(o.undecided),
    with_warnings: integer(o.with_warnings),
  };
}

export interface TransferJob {
  id: string;
  kind: JobKind;
  source: string;
  status: JobStatus;
  entity: string;
  file_format: string;
  scopes: string[];
  conflict_strategy: ConflictStrategy;
  options: JsonObject;
  summary: JobSummary | null;
  total_items: number;
  processed_items: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  failed_count: number;
  error_code: string;
  error_text: string;
  sensitive: boolean;
  cancel_requested: boolean;
  input_file: TransferFile | null;
  output_file: TransferFile | null;
  report_file: TransferFile | null;
  created_by_name: string;
  approved_by_name: string;
  approved_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  compensated_at: string | null;
  created_at: string;
  connection_name: string;
  connection_simulated: boolean;
}
export function transferJob(value: unknown): TransferJob {
  const o = object(value);
  const file = nullable(transferFile);
  return {
    id: string(o.id),
    kind: oneOf(JOB_KINDS)(o.kind),
    source: string(o.source),
    status: oneOf(JOB_STATUSES)(o.status),
    entity: string(o.entity),
    file_format: string(o.file_format),
    scopes: strings(o.scopes),
    conflict_strategy: oneOf(CONFLICT_STRATEGIES)(o.conflict_strategy),
    options: object(o.options),
    summary: jobSummary(o.summary),
    total_items: integer(o.total_items),
    processed_items: integer(o.processed_items),
    created_count: integer(o.created_count),
    updated_count: integer(o.updated_count),
    skipped_count: integer(o.skipped_count),
    failed_count: integer(o.failed_count),
    error_code: string(o.error_code),
    error_text: string(o.error_text),
    sensitive: boolean(o.sensitive),
    cancel_requested: boolean(o.cancel_requested),
    input_file: file(o.input_file),
    output_file: file(o.output_file),
    report_file: file(o.report_file),
    created_by_name: string(o.created_by_name),
    approved_by_name: string(o.approved_by_name),
    approved_at: nullable(isoDateTime)(o.approved_at),
    started_at: nullable(isoDateTime)(o.started_at),
    finished_at: nullable(isoDateTime)(o.finished_at),
    compensated_at: nullable(isoDateTime)(o.compensated_at),
    created_at: isoDateTime(o.created_at),
    connection_name: string(o.connection_name),
    connection_simulated: boolean(o.connection_simulated),
  };
}
export const transferJobPage: Decoder<Page<TransferJob>> = managePage(transferJob);

export interface ItemMessage {
  level: 'error' | 'warning' | 'info';
  text: string;
}
export interface ImportItem {
  id: string;
  position: number;
  object_type: string;
  external_id: string;
  key: string;
  title: string;
  action: ItemAction;
  status: ItemStatus;
  decision: string;
  messages: ItemMessage[];
  existing_id: string | null;
  local_id: string | null;
  created_local: boolean;
  preview: JsonObject;
}
export function importItem(value: unknown): ImportItem {
  const o = object(value);
  return {
    id: string(o.id),
    position: integer(o.position),
    object_type: string(o.object_type),
    external_id: string(o.external_id),
    key: string(o.key),
    title: string(o.title),
    action: oneOf(ITEM_ACTIONS)(o.action),
    status: oneOf(ITEM_STATUSES)(o.status),
    decision: string(o.decision),
    messages: array(o.messages, (entry) => {
      const m = object(entry);
      return { level: oneOf(['error', 'warning', 'info'] as const)(m.level), text: string(m.text) };
    }),
    existing_id: nullable(string)(o.existing_id),
    local_id: nullable(string)(o.local_id),
    created_local: boolean(o.created_local),
    preview: object(o.preview),
  };
}
export const importItemPage: Decoder<Page<ImportItem>> = managePage(importItem);

export interface SourceConnection {
  id: string;
  platform: SourcePlatform;
  name: string;
  store_url: string;
  credential_kind: string;
  credential_hint: string;
  credential_set: boolean;
  status: SourceStatus;
  last_verified_at: string | null;
  last_error: string;
  last_error_text: string;
  source_name: string;
  source_currency: string;
  capabilities: Record<string, boolean>;
  counts: Record<string, number | null>;
  is_simulated: boolean;
  created_at: string;
}
export function sourceConnection(value: unknown): SourceConnection {
  const o = object(value);
  return {
    id: string(o.id),
    platform: oneOf(SOURCE_PLATFORMS)(o.platform),
    name: string(o.name),
    store_url: string(o.store_url),
    credential_kind: string(o.credential_kind),
    credential_hint: string(o.credential_hint),
    credential_set: boolean(o.credential_set),
    status: oneOf(SOURCE_STATUSES)(o.status),
    last_verified_at: nullable(isoDateTime)(o.last_verified_at),
    last_error: string(o.last_error),
    last_error_text: string(o.last_error_text),
    source_name: string(o.source_name),
    source_currency: string(o.source_currency),
    capabilities: Object.fromEntries(
      Object.entries(object(o.capabilities)).map(([k, v]) => [k, boolean(v)]),
    ),
    counts: Object.fromEntries(
      Object.entries(object(o.counts)).map(([k, v]) => [k, nullable(integer)(v)]),
    ),
    is_simulated: boolean(o.is_simulated),
    created_at: isoDateTime(o.created_at),
  };
}
export function sourceConnections(value: unknown): SourceConnection[] {
  return array(value, sourceConnection);
}

export interface DataOverview {
  can_import: boolean;
  can_migrate: boolean;
  can_view_customers: boolean;
  can_view_orders: boolean;
  simulator_allowed: boolean;
  /** Template fields and the column names (normalised) that match each one automatically. */
  template_fields: { key: string; name: string; aliases: string[] }[];
  exports: { key: string; name: string; sensitive: boolean; formats: string[]; allowed: boolean }[];
  store_currency: string;
  max_upload_bytes: number;
}
export function dataOverview(value: unknown): DataOverview {
  const o = object(value);
  return {
    can_import: boolean(o.can_import),
    can_migrate: boolean(o.can_migrate),
    can_view_customers: boolean(o.can_view_customers),
    can_view_orders: boolean(o.can_view_orders),
    simulator_allowed: boolean(o.simulator_allowed),
    template_fields: array(o.template_fields, (entry) => {
      const f = object(entry);
      return {
        key: string(f.key),
        name: string(f.name),
        aliases: f.aliases === undefined ? [] : strings(f.aliases),
      };
    }),
    exports: array(o.exports, (entry) => {
      const e = object(entry);
      return {
        key: string(e.key),
        name: string(e.name),
        sensitive: boolean(e.sensitive),
        formats: strings(e.formats),
        allowed: boolean(e.allowed),
      };
    }),
    store_currency: string(o.store_currency),
    max_upload_bytes: integer(o.max_upload_bytes),
  };
}

export interface HistoricalCustomer {
  id: string;
  platform: string;
  external_id: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  city: string;
  country: string;
  source_orders_count: number | null;
  source_created_at: string | null;
  created_at: string;
}
export const historicalCustomerPage: Decoder<Page<HistoricalCustomer>> = managePage((value) => {
  const o = object(value);
  return {
    id: string(o.id),
    platform: string(o.platform),
    external_id: string(o.external_id),
    first_name: string(o.first_name),
    last_name: string(o.last_name),
    email: string(o.email),
    phone: string(o.phone),
    city: string(o.city),
    country: string(o.country),
    source_orders_count: nullable(integer)(o.source_orders_count),
    source_created_at: nullable(isoDateTime)(o.source_created_at),
    created_at: isoDateTime(o.created_at),
  };
});

export interface HistoricalOrder {
  id: string;
  platform: string;
  external_id: string;
  number: string;
  placed_at: string | null;
  source_status: string;
  source_financial_status: string;
  source_fulfillment_status: string;
  currency: string;
  subtotal: string | null;
  shipping_total: string | null;
  tax_total: string | null;
  discount_total: string | null;
  grand_total: string;
  lines: { name: string; sku: string; quantity: string; unit_price: string; total: string }[];
  customer_name: string;
  created_at: string;
}
export const historicalOrderPage: Decoder<Page<HistoricalOrder>> = managePage((value) => {
  const o = object(value);
  const money = nullable(decimal);
  return {
    id: string(o.id),
    platform: string(o.platform),
    external_id: string(o.external_id),
    number: string(o.number),
    placed_at: nullable(isoDateTime)(o.placed_at),
    source_status: string(o.source_status),
    source_financial_status: string(o.source_financial_status),
    source_fulfillment_status: string(o.source_fulfillment_status),
    currency: string(o.currency),
    subtotal: money(o.subtotal),
    shipping_total: money(o.shipping_total),
    tax_total: money(o.tax_total),
    discount_total: money(o.discount_total),
    grand_total: decimal(o.grand_total),
    lines: array(o.lines, (entry) => {
      const line = object(entry);
      const text = (v: unknown) => (v === null || v === undefined ? '' : String(v));
      return {
        name: text(line.name),
        sku: text(line.sku),
        quantity: text(line.quantity),
        unit_price: text(line.unit_price),
        total: text(line.total),
      };
    }),
    customer_name: string(o.customer_name),
    created_at: isoDateTime(o.created_at),
  };
});

export interface Compensation {
  deleted: number;
  archived: number;
  kept: number;
}
export function compensation(value: unknown): Compensation {
  const o = object(value);
  return { deleted: integer(o.deleted), archived: integer(o.archived), kept: integer(o.kept) };
}

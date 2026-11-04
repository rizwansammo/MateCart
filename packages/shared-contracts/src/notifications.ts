/**
 * Runtime-validated notification responses (Phase 13), mirroring the committed OpenAPI schema:
 * in-app inboxes, preferences, delivery history, email connections, SMS and templates.
 *
 * Connection responses carry only *whether* a password or token is stored (`*_set`). Decoders
 * build fresh objects field by field, so an unexpected secret in a response is never kept.
 */
import { array, boolean, number, object, string, strings, type Decoder } from './auth';
import { isoDateTime, nullable, type Page } from './catalog';
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

export const NOTIFICATION_CATEGORIES = [
  'transactional',
  'security',
  'operational',
  'marketing',
] as const;
export type NotificationCategory = (typeof NOTIFICATION_CATEGORIES)[number];
export const NOTIFICATION_CHANNELS = ['email', 'sms', 'in_app'] as const;
export type NotificationChannel = (typeof NOTIFICATION_CHANNELS)[number];
export const NOTIFICATION_STATUSES = [
  'pending',
  'queued',
  'processing',
  'sent',
  'delivered',
  'failed',
  'cancelled',
  'suppressed',
] as const;
export type NotificationStatus = (typeof NOTIFICATION_STATUSES)[number];
export const CONNECTION_STATUSES = [
  'not_configured',
  'unverified',
  'awaiting_approval',
  'connected',
  'failed',
  'revoked',
] as const;
export type ConnectionStatus = (typeof CONNECTION_STATUSES)[number];
export const MAIL_SECURITY = ['starttls', 'tls', 'none'] as const;
export type MailSecurity = (typeof MAIL_SECURITY)[number];
export const MAIL_PROVIDERS = ['smtp', 'matemail'] as const;
export type MailProvider = (typeof MAIL_PROVIDERS)[number];
export const SENDER_STATUSES = ['none', 'pending', 'approved', 'rejected'] as const;
export type SenderStatus = (typeof SENDER_STATUSES)[number];
export const TEMPLATE_LOCALES = ['en', 'bn'] as const;
export type TemplateLocaleCode = (typeof TEMPLATE_LOCALES)[number];

// --- Inbox ----------------------------------------------------------------------------------------

export interface NotificationItem {
  id: string;
  kind: string;
  category: NotificationCategory;
  title: string;
  body: string;
  link: string;
  created_at: string;
  read_at: string | null;
}
export function notificationItem(value: unknown): NotificationItem {
  const o = object(value);
  return {
    id: string(o.id),
    kind: string(o.kind),
    category: oneOf(NOTIFICATION_CATEGORIES)(o.category),
    title: string(o.title),
    body: string(o.body),
    link: string(o.link),
    created_at: isoDateTime(o.created_at),
    read_at: nullable(isoDateTime)(o.read_at),
  };
}
export const notificationPage: Decoder<Page<NotificationItem>> = managePage(notificationItem);

export function unreadCount(value: unknown): number {
  return integer(object(value).unread);
}

/**
 * An in-app link the dashboard or storefront may follow: a same-site path only. Anything else
 * (absolute, protocol-relative, `javascript:`) is dropped. `/manage` links are kept only when
 * `manage` is true.
 */
export function safeNotificationLink(link: string, manage: boolean): string | null {
  if (!link.startsWith('/') || link.startsWith('//') || link.includes('\\')) return null;
  if (manage) return link === '/manage' || link.startsWith('/manage/') ? link : null;
  return link.startsWith('/manage') ? null : link;
}

// --- Preferences ----------------------------------------------------------------------------------

export interface NotificationPreference {
  topic: string;
  title: string;
  channel: NotificationChannel;
  enabled: boolean;
  locked: boolean;
}
export function notificationPreferences(value: unknown): NotificationPreference[] {
  return array(value, (entry) => {
    const o = object(entry);
    return {
      topic: string(o.topic),
      title: string(o.title),
      channel: oneOf(NOTIFICATION_CHANNELS)(o.channel),
      enabled: boolean(o.enabled),
      locked: boolean(o.locked),
    };
  });
}

// --- Delivery history -----------------------------------------------------------------------------

export interface NotificationDelivery {
  id: string;
  kind: string;
  category: NotificationCategory;
  channel: NotificationChannel;
  audience: string;
  recipient: string;
  subject: string;
  status: NotificationStatus;
  attempts: number;
  provider: string;
  failure_code: string;
  created_at: string;
  last_attempt_at: string | null;
  sent_at: string | null;
  can_retry: boolean;
}
export function notificationDelivery(value: unknown): NotificationDelivery {
  const o = object(value);
  return {
    id: string(o.id),
    kind: string(o.kind),
    category: oneOf(NOTIFICATION_CATEGORIES)(o.category),
    channel: oneOf(NOTIFICATION_CHANNELS)(o.channel),
    audience: string(o.audience),
    recipient: string(o.recipient),
    subject: string(o.subject),
    status: oneOf(NOTIFICATION_STATUSES)(o.status),
    attempts: integer(o.attempts),
    provider: string(o.provider),
    failure_code: string(o.failure_code),
    created_at: isoDateTime(o.created_at),
    last_attempt_at: nullable(isoDateTime)(o.last_attempt_at),
    sent_at: nullable(isoDateTime)(o.sent_at),
    can_retry: boolean(o.can_retry),
  };
}
export const deliveryPage: Decoder<Page<NotificationDelivery>> = managePage(notificationDelivery);

export interface DeliveryAttemptRow {
  number: number;
  provider: string;
  outcome: string;
  error_code: string;
  next_retry_at: string | null;
  occurred_at: string;
}
export function deliveryAttempts(value: unknown): DeliveryAttemptRow[] {
  return array(value, (entry) => {
    const o = object(entry);
    return {
      number: integer(o.number),
      provider: string(o.provider),
      outcome: string(o.outcome),
      error_code: string(o.error_code),
      next_retry_at: nullable(isoDateTime)(o.next_retry_at),
      occurred_at: isoDateTime(o.occurred_at),
    };
  });
}

export interface DeliverySummary {
  pending: number;
  sent: number;
  failed: number;
  suppressed: number;
}
export function deliverySummary(value: unknown): DeliverySummary {
  const o = object(value);
  return {
    pending: integer(o.pending),
    sent: integer(o.sent),
    failed: integer(o.failed),
    suppressed: integer(o.suppressed),
  };
}

// --- Email connections ----------------------------------------------------------------------------

export interface MailConnectionState {
  provider: MailProvider;
  is_enabled: boolean;
  status: ConnectionStatus;
  display_name: string;
  smtp_host: string;
  smtp_port: number | null;
  smtp_security: MailSecurity;
  smtp_username: string;
  smtp_password_set: boolean;
  from_email: string;
  from_name: string;
  reply_to: string;
  sender_verified: boolean;
  sender_code_pending: boolean;
  imap_host: string;
  imap_port: number | null;
  imap_security: MailSecurity;
  imap_username: string;
  imap_password_set: boolean;
  imap_status: ConnectionStatus;
  imap_verified_at: string | null;
  imap_error: string;
  matemail_tenant_id: string;
  matemail_authorization_url: string;
  matemail_request_expires_at: string | null;
  matemail_mailbox: string;
  matemail_scopes: string[];
  matemail_token_set: boolean;
  last_verified_at: string | null;
  last_error: string;
}
function httpsOrEmpty(value: unknown): string {
  const text = string(value);
  if (text && !text.startsWith('https://')) throw new Error('Invalid response');
  return text;
}
export function mailConnection(value: unknown): MailConnectionState {
  const o = object(value);
  return {
    provider: oneOf(MAIL_PROVIDERS)(o.provider),
    is_enabled: boolean(o.is_enabled),
    status: oneOf(CONNECTION_STATUSES)(o.status),
    display_name: string(o.display_name),
    smtp_host: string(o.smtp_host),
    smtp_port: nullable(integer)(o.smtp_port),
    smtp_security: oneOf(MAIL_SECURITY)(o.smtp_security),
    smtp_username: string(o.smtp_username),
    smtp_password_set: boolean(o.smtp_password_set),
    from_email: string(o.from_email),
    from_name: string(o.from_name),
    reply_to: string(o.reply_to),
    sender_verified: boolean(o.sender_verified),
    sender_code_pending: boolean(o.sender_code_pending),
    imap_host: string(o.imap_host),
    imap_port: nullable(integer)(o.imap_port),
    imap_security: oneOf(MAIL_SECURITY)(o.imap_security),
    imap_username: string(o.imap_username),
    imap_password_set: boolean(o.imap_password_set),
    imap_status: oneOf(CONNECTION_STATUSES)(o.imap_status),
    imap_verified_at: nullable(isoDateTime)(o.imap_verified_at),
    imap_error: string(o.imap_error),
    matemail_tenant_id: string(o.matemail_tenant_id),
    matemail_authorization_url: httpsOrEmpty(o.matemail_authorization_url),
    matemail_request_expires_at: nullable(isoDateTime)(o.matemail_request_expires_at),
    matemail_mailbox: string(o.matemail_mailbox),
    matemail_scopes: strings(o.matemail_scopes),
    matemail_token_set: boolean(o.matemail_token_set),
    last_verified_at: nullable(isoDateTime)(o.last_verified_at),
    last_error: string(o.last_error),
  };
}

export interface EmailSettings {
  active_provider: MailProvider | null;
  default_sender_available: boolean;
  matemail_available: boolean;
  smtp: MailConnectionState;
  matemail: MailConnectionState;
}
export function emailSettings(value: unknown): EmailSettings {
  const o = object(value);
  return {
    active_provider: nullable(oneOf(MAIL_PROVIDERS))(o.active_provider),
    default_sender_available: boolean(o.default_sender_available),
    matemail_available: boolean(o.matemail_available),
    smtp: mailConnection(o.smtp),
    matemail: mailConnection(o.matemail),
  };
}

// --- SMS ------------------------------------------------------------------------------------------

export interface SmsSettings {
  provider_configured: boolean;
  is_enabled: boolean;
  requested_sender: string;
  approved_sender: string;
  sender_status: SenderStatus;
  order_updates: boolean;
  shipping_updates: boolean;
  ready: boolean;
  balance: { allowance: number; used: number; remaining: number; prepaid_credits: number };
}
export function smsSettings(value: unknown): SmsSettings {
  const o = object(value);
  const b = object(o.balance);
  return {
    provider_configured: boolean(o.provider_configured),
    is_enabled: boolean(o.is_enabled),
    requested_sender: string(o.requested_sender),
    approved_sender: string(o.approved_sender),
    sender_status: oneOf(SENDER_STATUSES)(o.sender_status),
    order_updates: boolean(o.order_updates),
    shipping_updates: boolean(o.shipping_updates),
    ready: boolean(o.ready),
    balance: {
      allowance: integer(b.allowance),
      used: integer(b.used),
      remaining: integer(b.remaining),
      prepaid_credits: integer(b.prepaid_credits),
    },
  };
}

// --- Templates ------------------------------------------------------------------------------------

export interface TemplateLocale {
  locale: TemplateLocaleCode;
  customized: boolean;
  is_enabled: boolean;
  version: number;
  subject: string;
  heading: string;
  body: string;
  sms: string;
  in_app_title: string;
  in_app_body: string;
  default_subject: string;
  default_heading: string;
  default_body: string;
  default_sms: string;
}
export interface NotificationTemplate {
  key: string;
  title: string;
  category: NotificationCategory;
  audience: string;
  variables: string[];
  supports_sms: boolean;
  locales: TemplateLocale[];
}
export function notificationTemplate(value: unknown): NotificationTemplate {
  const o = object(value);
  return {
    key: string(o.key),
    title: string(o.title),
    category: oneOf(NOTIFICATION_CATEGORIES)(o.category),
    audience: string(o.audience),
    variables: strings(o.variables),
    supports_sms: boolean(o.supports_sms),
    locales: array(o.locales, (entry) => {
      const l = object(entry);
      return {
        locale: oneOf(TEMPLATE_LOCALES)(l.locale),
        customized: boolean(l.customized),
        is_enabled: boolean(l.is_enabled),
        version: integer(l.version),
        subject: string(l.subject),
        heading: string(l.heading),
        body: string(l.body),
        sms: string(l.sms),
        in_app_title: string(l.in_app_title),
        in_app_body: string(l.in_app_body),
        default_subject: string(l.default_subject),
        default_heading: string(l.default_heading),
        default_body: string(l.default_body),
        default_sms: string(l.default_sms),
      };
    }),
  };
}
export function notificationTemplates(value: unknown): NotificationTemplate[] {
  return array(value, notificationTemplate);
}

export interface TemplatePreview {
  subject: string;
  text: string;
  html: string;
  sms: string;
  sms_segments: number;
  in_app_title: string;
  in_app_body: string;
}
export function templatePreview(value: unknown): TemplatePreview {
  const o = object(value);
  return {
    subject: string(o.subject),
    text: string(o.text),
    html: string(o.html),
    sms: string(o.sms),
    sms_segments: integer(o.sms_segments),
    in_app_title: string(o.in_app_title),
    in_app_body: string(o.in_app_body),
  };
}

export function unsubscribeResult(value: unknown): boolean {
  return boolean(object(value).unsubscribed);
}

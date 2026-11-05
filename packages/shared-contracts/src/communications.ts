/**
 * Runtime-validated unified inbox, website chat and social channel responses (Phase 14),
 * mirroring the committed OpenAPI schema and the WebSocket events built from the same serializers.
 *
 * Decoders build fresh objects field by field, so a field a response should never contain (a
 * provider token, a note on a visitor payload) is never kept. Links and file URLs are accepted
 * only as same-site paths of the expected shape.
 */
import { array, boolean, number, object, string, strings, type Decoder } from './auth';
import { decimal, isoDateTime, nullable } from './catalog';

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
function optional<T>(decode: Decoder<T>, fallback: T): Decoder<T> {
  return (value) => (value === undefined ? fallback : decode(value));
}
const ATTACHMENT_URL = /^\/api\/(chat|manage\/inbox)\/attachments\/[0-9a-f-]{36}\/$/;
function attachmentUrl(value: unknown): string {
  const text = string(value);
  if (!ATTACHMENT_URL.test(text)) throw new Error('Invalid response');
  return text;
}
/** Same-site paths or the store's own https links; anything else is dropped to "". */
const HTTPS_LINK = /^https:\/\/[A-Za-z0-9.-]+(:\d{1,5})?(\/[^\s\\"'<>]*)?$/;
export function safeProductLink(value: string): string {
  if (value.startsWith('/') && !value.startsWith('//') && !value.includes('\\')) return value;
  return HTTPS_LINK.test(value) ? value : '';
}

export const CHANNELS = ['website', 'messenger', 'whatsapp', 'instagram'] as const;
export type Channel = (typeof CHANNELS)[number];
export const CONVERSATION_STATUSES = ['open', 'pending', 'resolved', 'closed'] as const;
export type ConversationStatus = (typeof CONVERSATION_STATUSES)[number];
export const MESSAGE_KINDS = [
  'text',
  'image',
  'document',
  'product',
  'template',
  'unsupported',
] as const;
export type MessageKind = (typeof MESSAGE_KINDS)[number];
export const DELIVERY_STATUSES = [
  'received',
  'queued',
  'sending',
  'accepted',
  'delivered',
  'read',
  'failed',
  'unknown',
] as const;
export type DeliveryStatus = (typeof DELIVERY_STATUSES)[number];
export const CHANNEL_STATES = [
  'not_configured',
  'configuration_required',
  'pending',
  'connected',
  'permission_missing',
  'revoked',
  'suspended',
  'error',
  'disconnected',
] as const;
export type ChannelState = (typeof CHANNEL_STATES)[number];
export const ATTACHMENT_STATUSES = [
  'pending',
  'stored',
  'failed',
  'unsupported',
  'removed',
] as const;
export type AttachmentStatus = (typeof ATTACHMENT_STATUSES)[number];

const channel = oneOf(CHANNELS);
const direction = oneOf(['inbound', 'outbound'] as const);

// --- Messages -------------------------------------------------------------------------------------

export interface Attachment {
  id: string;
  status: AttachmentStatus;
  content_type: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  filename: string;
  url: string | null;
}
function attachment(value: unknown): Attachment {
  const o = object(value);
  return {
    id: string(o.id),
    status: oneOf(ATTACHMENT_STATUSES)(o.status),
    content_type: string(o.content_type),
    size_bytes: integer(o.size_bytes),
    width: nullable(integer)(o.width),
    height: nullable(integer)(o.height),
    filename: string(o.filename),
    url: nullable(attachmentUrl)(o.url),
  };
}

export interface ProductCard {
  product_id: string;
  variant_id: string;
  name: string;
  variant_title: string;
  sku: string;
  amount: string;
  currency: string;
  in_stock: boolean;
  url: string;
  image_url: string | null;
}
function productCard(value: unknown): ProductCard {
  const o = object(value);
  return {
    product_id: string(o.product_id),
    variant_id: string(o.variant_id),
    name: string(o.name),
    variant_title: string(o.variant_title),
    sku: string(o.sku),
    amount: decimal(o.amount),
    currency: string(o.currency),
    in_stock: boolean(o.in_stock),
    url: safeProductLink(string(o.url)),
    image_url: nullable(string)(o.image_url),
  };
}

/** What a website visitor sees of a message. */
export interface VisitorMessage {
  id: string;
  seq: number;
  direction: 'inbound' | 'outbound';
  kind: MessageKind;
  body: string;
  created_at: string;
  client_key: string;
  sender_name: string;
  product: ProductCard | null;
  attachment: Attachment | null;
}
export function visitorMessage(value: unknown): VisitorMessage {
  const o = object(value);
  return {
    id: string(o.id),
    seq: integer(o.seq),
    direction: direction(o.direction),
    kind: oneOf(MESSAGE_KINDS)(o.kind),
    body: string(o.body),
    created_at: isoDateTime(o.created_at),
    client_key: string(o.client_key),
    sender_name: string(o.sender_name),
    product: nullable(productCard)(o.product),
    attachment: nullable(attachment)(o.attachment),
  };
}

export interface Person {
  id: string;
  name: string;
}
function person(value: unknown): Person {
  const o = object(value);
  return { id: string(o.id), name: string(o.name) };
}

export interface StaffMessage extends VisitorMessage {
  delivery_status: DeliveryStatus;
  status_changed_at: string | null;
  error_code: string;
  provider_timestamp: string | null;
  sender: Person | null;
  template_name: string;
  template_language: string;
  reply_tag: string;
  pricing_category: string;
  attempts: number;
}
export function staffMessage(value: unknown): StaffMessage {
  const o = object(value);
  return {
    ...visitorMessage(value),
    delivery_status: oneOf(DELIVERY_STATUSES)(o.delivery_status),
    status_changed_at: nullable(isoDateTime)(o.status_changed_at),
    error_code: string(o.error_code),
    provider_timestamp: nullable(isoDateTime)(o.provider_timestamp),
    sender: nullable(person)(o.sender),
    template_name: string(o.template_name),
    template_language: string(o.template_language),
    reply_tag: string(o.reply_tag),
    pricing_category: string(o.pricing_category),
    attempts: integer(o.attempts),
  };
}

// --- Conversations --------------------------------------------------------------------------------

export interface ConversationSummary {
  id: string;
  channel: Channel;
  status: ConversationStatus;
  version: number;
  display_name: string;
  connection_name: string;
  is_simulated: boolean;
  assignee: Person | null;
  last_message_at: string | null;
  last_preview: string;
  last_direction: string;
  inbound_count: number;
  /** Per staff member; null on live events (the client adjusts its own count). */
  unread: number | null;
  customer_linked: boolean;
  created_at: string;
}
export function conversationSummary(value: unknown): ConversationSummary {
  const o = object(value);
  return {
    id: string(o.id),
    channel: channel(o.channel),
    status: oneOf(CONVERSATION_STATUSES)(o.status),
    version: integer(o.version),
    display_name: string(o.display_name),
    connection_name: string(o.connection_name),
    is_simulated: boolean(o.is_simulated),
    assignee: nullable(person)(o.assignee),
    last_message_at: nullable(isoDateTime)(o.last_message_at),
    last_preview: string(o.last_preview),
    last_direction: string(o.last_direction),
    inbound_count: integer(o.inbound_count),
    unread: optional(nullable(integer), null)(o.unread),
    customer_linked: boolean(o.customer_linked),
    created_at: isoDateTime(o.created_at),
  };
}

export interface ConversationPage {
  results: ConversationSummary[];
  next_cursor: string | null;
  unread_conversations: number;
}
export function conversationPage(value: unknown): ConversationPage {
  const o = object(value);
  return {
    results: array(o.results, conversationSummary),
    next_cursor: nullable(string)(o.next_cursor),
    unread_conversations: integer(o.unread_conversations),
  };
}

export interface ReplyPolicy {
  can_reply: boolean;
  reason: string;
  message: string;
  tag: string;
  window_ends_at: string | null;
  templates_allowed: boolean;
  template_reason: string;
}
export interface LinkedOrder {
  id: string;
  number: string;
  status: string;
  payment_status: string;
  grand_total: string;
  currency: string;
  order_source: string;
  kind: string;
  created_at: string;
}
export interface CaseSummary {
  id: string;
  kind: string;
  status: string;
  created_at: string;
}
export interface ConversationDetail {
  conversation: ConversationSummary;
  participant: {
    id: string;
    display_name: string;
    channel: Channel;
    phone: string;
    customer_id: string | null;
    customer_linked_at: string | null;
    template_opt_in_at: string | null;
    opted_out_at: string | null;
  };
  policy: ReplyPolicy;
  orders: LinkedOrder[];
  cases: CaseSummary[];
  recent: ConversationSummary[];
  can_create_orders: boolean;
  visitor_read_seq: number;
}
export function linkedOrder(value: unknown): LinkedOrder {
  const o = object(value);
  return {
    id: string(o.id),
    number: string(o.number),
    status: string(o.status),
    payment_status: string(o.payment_status),
    grand_total: decimal(o.grand_total),
    currency: string(o.currency),
    order_source: string(o.order_source),
    kind: string(o.kind),
    created_at: isoDateTime(o.created_at),
  };
}
export function conversationDetail(value: unknown): ConversationDetail {
  const o = object(value);
  const p = object(o.participant);
  const r = object(o.policy);
  return {
    conversation: conversationSummary(o.conversation),
    participant: {
      id: string(p.id),
      display_name: string(p.display_name),
      channel: channel(p.channel),
      phone: string(p.phone),
      customer_id: nullable(string)(p.customer_id),
      customer_linked_at: nullable(isoDateTime)(p.customer_linked_at),
      template_opt_in_at: nullable(isoDateTime)(p.template_opt_in_at),
      opted_out_at: nullable(isoDateTime)(p.opted_out_at),
    },
    policy: {
      can_reply: boolean(r.can_reply),
      reason: string(r.reason),
      message: string(r.message),
      tag: string(r.tag),
      window_ends_at: nullable(isoDateTime)(r.window_ends_at),
      templates_allowed: boolean(r.templates_allowed),
      template_reason: string(r.template_reason),
    },
    orders: array(o.orders, linkedOrder),
    cases: array(o.cases, (entry) => {
      const c = object(entry);
      return {
        id: string(c.id),
        kind: string(c.kind),
        status: string(c.status),
        created_at: isoDateTime(c.created_at),
      };
    }),
    recent: array(o.recent, conversationSummary),
    can_create_orders: boolean(o.can_create_orders),
    visitor_read_seq: integer(o.visitor_read_seq),
  };
}

export interface Note {
  id: string;
  body: string;
  created_at: string;
  author: Person;
}
export function note(value: unknown): Note {
  const o = object(value);
  return {
    id: string(o.id),
    body: string(o.body),
    created_at: isoDateTime(o.created_at),
    author: person(o.author),
  };
}
export interface TimelineEvent {
  id: string;
  kind: string;
  created_at: string;
  from_status: string;
  to_status: string;
  actor: Person | null;
  assignee: Person | null;
  order: { id: string; number: string } | null;
}
function timelineEvent(value: unknown): TimelineEvent {
  const o = object(value);
  return {
    id: string(o.id),
    kind: string(o.kind),
    created_at: isoDateTime(o.created_at),
    from_status: string(o.from_status),
    to_status: string(o.to_status),
    actor: nullable(person)(o.actor),
    assignee: nullable(person)(o.assignee),
    order: nullable((entry) => {
      const e = object(entry);
      return { id: string(e.id), number: string(e.number) };
    })(o.order),
  };
}
export interface Thread {
  messages: StaffMessage[];
  notes: Note[];
  events: TimelineEvent[];
  has_more: boolean;
}
export function thread(value: unknown): Thread {
  const o = object(value);
  return {
    messages: array(o.messages, staffMessage),
    notes: array(o.notes, note),
    events: array(o.events, timelineEvent),
    has_more: boolean(o.has_more),
  };
}

export interface InboxProduct {
  variant_id: string;
  product_id: string;
  name: string;
  variant_title: string;
  sku: string;
  amount: string | null;
  currency: string | null;
  in_stock: boolean;
  available_quantity: string | null;
  image_url: string | null;
}
export function inboxProducts(value: unknown): {
  results: InboxProduct[];
  links_available: boolean;
} {
  const o = object(value);
  return {
    results: array(o.results, (entry) => {
      const p = object(entry);
      return {
        variant_id: string(p.variant_id),
        product_id: string(p.product_id),
        name: string(p.name),
        variant_title: string(p.variant_title),
        sku: string(p.sku),
        amount: nullable(decimal)(p.amount),
        currency: nullable(string)(p.currency),
        in_stock: boolean(p.in_stock),
        available_quantity: nullable(decimal)(p.available_quantity),
        image_url: nullable(string)(p.image_url),
      };
    }),
    links_available: boolean(o.links_available),
  };
}

export interface InboxTeamMember {
  id: string;
  name: string;
  role: string;
}
export function inboxTeam(value: unknown): InboxTeamMember[] {
  return array(value, (entry) => {
    const o = object(entry);
    return { id: string(o.id), name: string(o.name), role: string(o.role) };
  });
}

export interface WhatsAppTemplate {
  id: string;
  name: string;
  language: string;
  category: string;
  status: string;
  body_text: string;
  parameter_count: number;
  sendable: boolean;
}
export function whatsAppTemplates(value: unknown): WhatsAppTemplate[] {
  return array(value, (entry) => {
    const o = object(entry);
    return {
      id: string(o.id),
      name: string(o.name),
      language: string(o.language),
      category: string(o.category),
      status: string(o.status),
      body_text: string(o.body_text),
      parameter_count: integer(o.parameter_count),
      sendable: boolean(o.sendable),
    };
  });
}

// --- Analytics ------------------------------------------------------------------------------------

export interface InboxAnalytics {
  days: number;
  timezone: string;
  start: string;
  end: string;
  new_conversations: number;
  unique_participants: number;
  messages_received: number;
  messages_sent: number;
  resolved_conversations: number;
  open_now: number;
  pending_now: number;
  unassigned_now: number;
  unread_now: number;
  median_first_response_seconds: number | null;
  average_first_response_seconds: number | null;
  average_resolution_seconds: number | null;
  by_channel: { channel: string; conversations: number }[];
  orders_linked: number;
  linked_order_value: string | null;
  linked_order_currency: string | null;
  agents: {
    id: string;
    name: string;
    assigned_open: number;
    resolved: number;
    replies: number;
    median_first_response_seconds: number | null;
  }[];
}
export function inboxAnalytics(value: unknown): InboxAnalytics {
  const o = object(value);
  const n = (key: string) => integer(o[key]);
  return {
    days: n('days'),
    timezone: string(o.timezone),
    start: string(o.start),
    end: string(o.end),
    new_conversations: n('new_conversations'),
    unique_participants: n('unique_participants'),
    messages_received: n('messages_received'),
    messages_sent: n('messages_sent'),
    resolved_conversations: n('resolved_conversations'),
    open_now: n('open_now'),
    pending_now: n('pending_now'),
    unassigned_now: n('unassigned_now'),
    unread_now: n('unread_now'),
    median_first_response_seconds: nullable(integer)(o.median_first_response_seconds),
    average_first_response_seconds: nullable(integer)(o.average_first_response_seconds),
    average_resolution_seconds: nullable(integer)(o.average_resolution_seconds),
    by_channel: array(o.by_channel, (entry) => {
      const c = object(entry);
      return { channel: string(c.channel), conversations: integer(c.conversations) };
    }),
    orders_linked: n('orders_linked'),
    linked_order_value: nullable(decimal)(o.linked_order_value),
    linked_order_currency: nullable(string)(o.linked_order_currency),
    agents: array(o.agents, (entry) => {
      const a = object(entry);
      return {
        id: string(a.id),
        name: string(a.name),
        assigned_open: integer(a.assigned_open),
        resolved: integer(a.resolved),
        replies: integer(a.replies),
        median_first_response_seconds: nullable(integer)(a.median_first_response_seconds),
      };
    }),
  };
}

// --- Social channel settings ----------------------------------------------------------------------

export interface ChannelCard {
  channel: Channel;
  state: ChannelState;
  id: string | null;
  is_enabled: boolean;
  is_simulated: boolean;
  display_name: string;
  display_identifier: string;
  account_id: string;
  connected_at: string | null;
  last_verified_at: string | null;
  last_error: string;
  requirements: string[];
  templates: number;
}
export function channelCard(value: unknown): ChannelCard {
  const o = object(value);
  return {
    channel: channel(o.channel),
    state: oneOf(CHANNEL_STATES)(o.state),
    id: nullable(string)(o.id),
    is_enabled: boolean(o.is_enabled),
    is_simulated: boolean(o.is_simulated),
    display_name: string(o.display_name),
    display_identifier: string(o.display_identifier),
    account_id: string(o.account_id),
    connected_at: nullable(isoDateTime)(o.connected_at),
    last_verified_at: nullable(isoDateTime)(o.last_verified_at),
    last_error: string(o.last_error),
    requirements: strings(o.requirements),
    templates: integer(o.templates),
  };
}

export type ChatHours = Partial<Record<string, [string, string][]>>;
export interface LiveChatSettings {
  welcome_en: string;
  welcome_bn: string;
  button_label_en: string;
  button_label_bn: string;
  away_en: string;
  away_bn: string;
  hours: ChatHours;
  languages: 'en,bn' | 'en' | 'bn';
  position: 'right' | 'left';
}
function hours(value: unknown): ChatHours {
  const o = object(value);
  const result: ChatHours = {};
  for (const [day, ranges] of Object.entries(o)) {
    result[day] = array(ranges, (range) => {
      const [start, end] = strings(range);
      if (!start || !end) throw new Error('Invalid response');
      return [start, end];
    });
  }
  return result;
}
export interface SocialSettings {
  live_chat_enabled: boolean;
  live_chat: LiveChatSettings;
  agents_online: boolean;
  within_hours: boolean;
  timezone: string;
  connections: ChannelCard[];
  simulator_available: boolean;
  webhook_url: string;
  human_agent_approved: boolean;
  product_links_available: boolean;
}
export function socialSettings(value: unknown): SocialSettings {
  const o = object(value);
  const l = object(o.live_chat);
  return {
    live_chat_enabled: boolean(o.live_chat_enabled),
    live_chat: {
      welcome_en: string(l.welcome_en),
      welcome_bn: string(l.welcome_bn),
      button_label_en: string(l.button_label_en),
      button_label_bn: string(l.button_label_bn),
      away_en: string(l.away_en),
      away_bn: string(l.away_bn),
      hours: hours(l.hours),
      languages: oneOf(['en,bn', 'en', 'bn'] as const)(l.languages),
      position: oneOf(['right', 'left'] as const)(l.position),
    },
    agents_online: boolean(o.agents_online),
    within_hours: boolean(o.within_hours),
    timezone: string(o.timezone),
    connections: array(o.connections, channelCard),
    simulator_available: boolean(o.simulator_available),
    webhook_url: string(o.webhook_url),
    human_agent_approved: boolean(o.human_agent_approved),
    product_links_available: boolean(o.product_links_available),
  };
}
export function accountSelection(value: unknown): {
  channel: string;
  accounts: { id: string; name: string; identifier: string }[];
} {
  const o = object(value);
  return {
    channel: string(o.channel),
    accounts: array(o.accounts, (entry) => {
      const a = object(entry);
      return { id: string(a.id), name: string(a.name), identifier: string(a.identifier) };
    }),
  };
}
/** Meta's dialog only: the page never follows any other authorization URL. */
const META_DIALOG = /^https:\/\/www\.facebook\.com\/v\d+\.\d+\/dialog\/oauth\?[^\s"'<>]+$/;
export function authorizeUrl(value: unknown): string {
  const url = string(object(value).authorize_url);
  if (!META_DIALOG.test(url)) throw new Error('Invalid response');
  return url;
}

// --- Website chat ---------------------------------------------------------------------------------

export interface ChatConfig {
  enabled: boolean;
  welcome: string;
  button_label: string;
  away_message: string;
  languages: string[];
  position: 'right' | 'left';
  online: boolean;
  within_hours: boolean;
}
export interface ChatState {
  config: ChatConfig;
  conversation_status: ConversationStatus | null;
  messages: VisitorMessage[];
  signed_in: boolean;
  guest_chat_linkable: boolean;
}
export function chatState(value: unknown): ChatState {
  const o = object(value);
  const c = object(o.config);
  return {
    config: {
      enabled: boolean(c.enabled),
      welcome: string(c.welcome),
      button_label: string(c.button_label),
      away_message: string(c.away_message),
      languages: strings(c.languages),
      position: oneOf(['right', 'left'] as const)(c.position),
      online: boolean(c.online),
      within_hours: boolean(c.within_hours),
    },
    conversation_status: nullable(oneOf(CONVERSATION_STATUSES))(o.conversation_status),
    messages: array(o.messages, visitorMessage),
    signed_in: boolean(o.signed_in),
    guest_chat_linkable: boolean(o.guest_chat_linkable),
  };
}

/** WebSocket frames from the server to the visitor widget. Unknown frames are ignored. */
export type VisitorEvent =
  | { type: 'ready'; online: boolean }
  | { type: 'ack'; client_key: string; message: VisitorMessage }
  | { type: 'nack'; client_key: string; code: string }
  | { type: 'message'; message: VisitorMessage }
  | { type: 'sync'; status: ConversationStatus | null; messages: VisitorMessage[]; online: boolean }
  | { type: 'conversation'; status: ConversationStatus }
  | { type: 'typing'; who: string; on: boolean }
  | { type: 'pong' };
export function visitorEvent(value: unknown): VisitorEvent | null {
  try {
    const o = object(value);
    switch (o.type) {
      case 'ready':
        return { type: 'ready', online: boolean(o.online) };
      case 'ack':
        return {
          type: 'ack',
          client_key: string(o.client_key),
          message: visitorMessage(o.message),
        };
      case 'nack':
        return { type: 'nack', client_key: string(o.client_key), code: string(o.code) };
      case 'message':
        return { type: 'message', message: visitorMessage(o.message) };
      case 'sync':
        return {
          type: 'sync',
          status: nullable(oneOf(CONVERSATION_STATUSES))(o.status),
          messages: array(o.messages, visitorMessage),
          online: boolean(o.online),
        };
      case 'conversation':
        return { type: 'conversation', status: oneOf(CONVERSATION_STATUSES)(o.status) };
      case 'typing':
        return { type: 'typing', who: string(o.who), on: boolean(o.on) };
      case 'pong':
        return { type: 'pong' };
      default:
        return null;
    }
  } catch {
    return null;
  }
}

/** WebSocket frames from the server to a staff inbox. Unknown frames are ignored. */
export type InboxEvent =
  | { type: 'ready' }
  | { type: 'watching'; conversation: string }
  | { type: 'message'; conversation: string; message: StaffMessage }
  | { type: 'note'; conversation: string; note: Note }
  | { type: 'conversation'; conversation: ConversationSummary }
  | { type: 'delivery'; message: string; status: DeliveryStatus; error_code: string }
  | { type: 'typing'; who: string; conversation: string; on: boolean; name: string }
  | { type: 'visitor_read'; conversation: string; seq: number }
  | { type: 'attachment'; message: string }
  | { type: 'read'; conversation: string }
  | { type: 'pong' };
export function inboxEvent(value: unknown): InboxEvent | null {
  try {
    const o = object(value);
    switch (o.type) {
      case 'ready':
        return { type: 'ready' };
      case 'watching':
        return { type: 'watching', conversation: string(o.conversation) };
      case 'message':
        return {
          type: 'message',
          conversation: string(o.conversation),
          message: staffMessage(o.message),
        };
      case 'note':
        return { type: 'note', conversation: string(o.conversation), note: note(o.note) };
      case 'conversation':
        return { type: 'conversation', conversation: conversationSummary(o.conversation) };
      case 'delivery':
        return {
          type: 'delivery',
          message: string(o.message),
          status: oneOf(DELIVERY_STATUSES)(o.status),
          error_code: string(o.error_code),
        };
      case 'typing':
        return {
          type: 'typing',
          who: string(o.who),
          conversation: string(o.conversation),
          on: boolean(o.on),
          name: optional(string, '')(o.name),
        };
      case 'visitor_read':
        return {
          type: 'visitor_read',
          conversation: string(o.conversation),
          seq: integer(o.seq),
        };
      case 'attachment':
        return { type: 'attachment', message: string(o.message) };
      case 'read':
        return { type: 'read', conversation: string(o.conversation) };
      case 'pong':
        return { type: 'pong' };
      default:
        return null;
    }
  } catch {
    return null;
  }
}

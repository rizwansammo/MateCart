# Social commerce and unified inbox (Phase 14)

Status: implemented locally (D-092 to D-098). Website live chat is complete and accepted in a
real browser over real WebSockets. Messenger, WhatsApp and Instagram are implemented against
Meta's documented APIs and verified with scripted fakes and a labelled development simulator;
**no live Meta connection has been made** (see §10).

Code: `services/commerce-api/commerce/communications/`. UI:
`apps/commerce-web/src/components/manage/{inbox,inbox-analytics,social-settings}.tsx` and
`components/store/live-chat.tsx`. Contracts: `packages/shared-contracts/src/communications.ts`.

## 1. Concepts and tables

| Concept               | Table                   | Notes                                                                                                                                                      |
| --------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Channel connection    | `ChannelConnection`     | One authorised Page, WhatsApp number or Instagram account. Encrypted token. One live binding per channel; disconnected rows keep history.                  |
| External participant  | `Participant`           | Provider-scoped id (PSID, `wa_id`, IGSID) or a website visitor. **Never a customer account**; `customer` is set only by verified association (§6).         |
| Conversation          | `Conversation`          | One participant, one connection. `open`, `pending`, `resolved`, `closed`. At most one non-closed conversation per participant (partial unique constraint). |
| Message               | `Message`               | Stable `seq` per conversation, direction, kind, body, provider id (unique per connection), client key (unique per conversation), delivery state.           |
| Delivery attempt      | `DeliveryAttempt`       | Append-only per provider call (trigger refuses UPDATE).                                                                                                    |
| Attachment            | `MessageAttachment`     | Validated bytes under `tenant_storage_path`; never public.                                                                                                 |
| Internal note         | `ConversationNote`      | Staff-only table: no delivery or visitor path reads it.                                                                                                    |
| Timeline              | `ConversationEvent`     | Assignment, status, order link, opt-in/out. Append-only (trigger refuses UPDATE and DELETE).                                                               |
| Staff read state      | `ConversationReadState` | Per staff member: inbound messages read. Unread = `inbound_count - inbound_read`.                                                                          |
| Order link            | `ConversationOrderLink` | `created` (from this conversation) or `linked` (by staff). A reference, not attribution.                                                                   |
| WhatsApp template     | `WhatsAppTemplate`      | Synchronised from Meta with Meta's status; never authored here.                                                                                            |
| Website chat settings | `LiveChatSettings`      | Welcome, away and button texts (en/bn), hours, languages, launcher position. Plain text only.                                                              |

Every table is `TenantOwnedModel` with `TenantForeignKeyConstraint`s; cross-tenant references
fail in PostgreSQL.

## 2. Conversation lifecycle

- A customer message joins the participant's active conversation. On `pending` or `resolved`
  it reopens it (`open`) and alerts staff (`staff.conversation.reply`).
- After `closed`, a new customer message starts a **new** conversation; the old one keeps its
  history. Resolved conversations without activity for 7 days close automatically (hourly job).
- Staff transitions: open → pending, resolved, closed; pending → open, resolved, closed;
  resolved → open, closed; closed → open (refused when a newer conversation is active).
- Staff changes carry the `version` they saw; a stale version answers `409
conversation_changed` instead of overwriting a newer change. Assignment and status changes
  write `ConversationEvent` and audit events.
- Ordering: `seq` is allocated under the conversation's row lock and restarts at 1 in every
  conversation, so a sequence is only meaningful with its conversation id (D-100). Provider
  timestamps are kept as facts (`provider_timestamp`) but never reorder the thread.
- A website visitor sees one conversation: the active one (where their next message goes, which
  may be an older conversation staff reopened), otherwise the latest. Earlier conversations stay
  in the inbox; the widget never mixes them.

## 3. Website live chat

- **Module**: `live_chat` (store modules registry, off by default, `store.settings`). Off: the
  widget is not rendered, `/api/chat/*` answers `403 live_chat_disabled`, sockets are refused
  and open sockets close at the next re-check (≤ 30 s). History stays in the inbox.
- **Guest identity**: an opaque 256-bit credential in `mc_chat` (HttpOnly, SameSite=Lax, path
  `/api/chat`, 30 days sliding), stored only as SHA-256. Issued on the first message, never on
  page view. Not a session: it reaches no other route. Signed-in customers chat as themselves
  (`mc_customer`); a guest chat joins their account only when they click "Add that chat to my
  account" while holding both credentials.
- **HTTP**: `GET /api/chat/config/?locale=` (settings, availability, and the current
  conversation's `conversation_id`, status and history; optional `conversation` + `after_seq`
  for only the newer messages of that conversation),
  `POST /api/chat/messages/` (`client_key`, `body`), `POST /api/chat/attachments/` (multipart),
  `GET /api/chat/attachments/<id>/` (own conversation only), `POST /api/chat/link-account/`.
- **Availability** is genuine: "Online" needs the opening hours (store time zone) **and** a staff
  member with the inbox open now (socket heartbeat). Otherwise the away message shows.
- **Widget**: theme tokens of the active preset, 4 px radii, English and Bangla, keyboard and
  screen-reader support, full-screen sheet on phones, hidden on `/checkout`, below the consent
  banner (z-index 40 vs 50). Messages render as text; product cards and files use same-site
  links only.

## 4. WebSockets

ASGI (`config.asgi`) routes `websocket` to Channels; HTTP stays Django. Development serves both
with uvicorn; Next.js proxies `/api/*` including upgrades, so the browser only talks to its own
origin. Redis database 3 (`MATECART_CHANNEL_LAYER_URL`) carries events between workers.

| Endpoint                | Who                                     | Frames from the client                                                                                                                             | Frames from the server                                                                                                                       |
| ----------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/chat/ws/`         | Visitor with `mc_chat` or `mc_customer` | `ping`, `message {client_key, body}`, `sync {conversation, after_seq}`, `read {conversation, seq}`, `delivered {conversation, seq}`, `typing {on}` | `ready {online}`, `ack`, `nack`, `message`, `sync {conversation, status, messages}`, `conversation {conversation, status}`, `typing`, `pong` |
| `/api/manage/ws/inbox/` | Staff session with `messages.manage`    | `ping`, `watch {conversation}`, `unwatch`, `typing {on}`                                                                                           | `ready`, `watching`, `conversation`, `message`, `note`, `delivery`, `typing`, `visitor_read`, `read`, `attachment`, `pong`                   |

- **Handshake**: exact Origin allowlist (`MATECART_PUBLIC_BASE_URL`, CSRF trusted origins,
  `MATECART_WEBSOCKET_ORIGINS`; no Origin = refused), cookie authentication, tenant from
  configuration, 120 connects per minute per client address, at most 8 visitor or 12 staff
  sockets per identity.
- **Limits**: 16 KiB text frames (uvicorn `--ws-max-size 65536`), 40 frames per 10 s, idle
  close after 90 s without a frame (clients ping every 25 s), session and permission re-check
  every 30 s. Server-initiated closes are logged with a reason, never with identifiers.
- **Membership is server-controlled**: a visitor socket joins only its own participant group; a
  staff `watch` joins a conversation group only after the server found it in this store.
  Audiences are separate groups: visitor groups never receive notes, assignments or staff ids.
- **Durability**: messages are committed before `ack`; events are published after commit. A
  broker failure never fails the operation; clients reconcile on reconnect (`sync`, list reload).
- **Conversation identity (D-100)**: visitor messages carry `conversation`. `sync` returns the
  messages after `after_seq` only for the conversation it names; naming another conversation
  returns the current one from the start. Receipts apply only to the visitor's own conversation
  they name. Frames without a conversation (older clients) behave as before.
  The inbox polls every 30 s only while its socket is down.

## 5. Meta channels

One endpoint receives every Meta webhook: `GET/POST /api/webhooks/meta/`.

- `GET` answers the verification challenge only with `META_WEBHOOK_VERIFY_TOKEN`.
- `POST` is refused (401) unless `X-Hub-Signature-256` is the HMAC-SHA256 of the raw body with
  `META_APP_SECRET`, refused (413) above 1 MiB, then normalised (`meta.parse_webhook`).
- Events are routed by the account id they name (Page id, Instagram account id, WhatsApp phone
  number id) to a **live connection of this store**; unknown accounts are ignored. Nothing in
  the payload chooses a tenant, connection or customer. Echoes of business messages are ignored.
- Inbound messages are unique per connection and provider id; replays return the stored row and
  alert nobody. Receipts move states forward only (`accepted` < `delivered` < `read`), with
  WhatsApp's pricing category kept for display (never billing).
- Media: WhatsApp media ids are resolved through the Graph API; Messenger/Instagram give
  temporary CDN URLs. Downloads go only to Meta CDN hosts over HTTPS, to public addresses
  (pinned socket, TLS verified), without redirects, at most `MATECART_MEDIA_MAX_BYTES`, then the
  bytes are validated (JPEG, PNG, WebP or PDF). Anything else is `unsupported`; failures are
  `failed`. Provider references are cleared once settled.

### Sending and messaging rules (`policy.reply_policy`)

| Channel          | Free-form reply                                             | Outside the window                                                                                                                        |
| ---------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Website chat     | While not closed                                            | —                                                                                                                                         |
| Messenger        | 24 h after the customer's last message (`RESPONSE`)         | Up to 7 days with `HUMAN_AGENT` only if `META_HUMAN_AGENT_APPROVED`; otherwise refused (`window_closed`)                                  |
| Instagram Direct | Same as Messenger (Messenger Platform with the linked Page) | Same as Messenger                                                                                                                         |
| WhatsApp         | 24 h customer service window                                | Only an `APPROVED` **utility** template, only after staff recorded the customer's agreement, never after an opt-out (`template_required`) |

- The inbox shows the reason and disables free text when a reply is not allowed. Templates are
  synchronised from Meta; marketing templates are never offered. No bulk or unsolicited sends.
- Delivery integrity (D-099): template values are stored with the queued message
  (`template_parameters`), so a retry or a sweeper recovery after a broker outage sends exactly
  the original values; they are cleared once the message settles. Immediately before the
  provider call, under the claim lock, `policy.delivery_check` checks the rules again (window,
  Human Agent approval, opt-out and opt-in, closed conversation, connection state, template
  still approved and utility, value count). A message that is no longer allowed is `failed`
  with that reason and a `blocked` attempt: never sent, never retried.
- Delivery (`tasks.deliver_message`): claims `queued → sending`, then `accepted` (provider id
  stored), `failed` (definite refusal, with reason; revoked/permission/suspension also update the
  connection), bounded retries for `not_sent` and rate limits (4 attempts), or `unknown`
  (timeouts, 5xx, a lost worker): **never resent automatically** because Meta's send APIs have no
  idempotency key. Staff may resend deliberately after a confirmation. No failover to SMS or
  email.
- External channels receive text, products (name, price, link) and WhatsApp templates. Files
  from staff are supported in website chat only; the UI says so.

### Connecting accounts (`connections.py`, `store.settings` + recent sign-in)

1. `POST /api/manage/communications/connections/start/` stores a single-use state bound to the
   staff member and session and returns Meta's dialog URL (Facebook Login, or WhatsApp Embedded
   Signup when `META_WHATSAPP_CONFIG_ID` is set).
2. Meta redirects to `/api/manage/communications/oauth/callback/` (same staff cookie). The code
   is exchanged server-side; required scopes are checked with `debug_token`.
3. The choices are only what Meta lists for that token: Pages with a messaging task, Pages with
   a linked Instagram professional account, or phone numbers in the granted WhatsApp Business
   Accounts. They are cached encrypted for 15 minutes.
4. `select` stores the token (Fernet), subscribes the app to the account's webhooks and only
   then marks the connection `connected`.

Verify (`/verify/`), switch sending on or off (`/enabled/`; incoming messages are still
recorded), sync templates, and disconnect (`/disconnect/`, recent sign-in: sending stops at once,
the token is erased, history stays). States shown: not configured, configuration required,
authorization pending, connected, permission missing, revoked, suspended, error, disconnected.

## 6. Identity and privacy

- A WhatsApp number, PSID, IGSID or guest id never becomes a customer account, and nothing is
  merged by name, phone or email. Verified association: the signed-in customer's own chat, or
  the visitor's explicit confirmation while holding both credentials.
- Messaging consent is separate from Phase 12 tracking consent and Phase 13 email/SMS
  marketing consent. Starting a support chat consents to nothing else.
- Retention (hourly, `retention.apply`): closed conversations inactive for 365 days and guest
  website conversations inactive for 180 days lose message text, product snapshots, provider ids,
  files and notes; rows and counts stay. Expired guest credentials are cleared; delivery attempts
  older than 90 days are deleted; tokens are erased at disconnect. This is data minimisation,
  not a claim of legal compliance.
- Message bodies are never logged. Notifications carry the customer's display name and channel,
  never the message.

## 7. Orders from conversations

`POST /api/manage/inbox/conversations/<id>/orders/` (needs `messages.manage` **and**
`orders.manage`) calls `commerce.orders.assisted.place_assisted_order` unchanged: COD only,
`customer_request_attested` must be true, reviewed totals must match, stock is reserved by the
normal checkout, idempotency per request key, no customer account. The source follows the
channel (Messenger → `facebook`, WhatsApp → `whatsapp`, Instagram → `instagram`, website chat →
`live_chat`; the last two were added to `OrderSource`). The order is linked to the conversation
(`ConversationOrderLink`, timeline event, audit). Nothing is sent to the customer
automatically, and the link never exposes the order to the participant.

Existing orders can be linked by number (`/orders/link/`, same permissions). Product sharing uses
published products, the promotion engine's public price and real sellable stock; external
channels get a link only when `MATECART_PUBLIC_BASE_URL` is a public `https://` address.

## 8. Notifications and analytics

- Kinds (topic `conversations`, staff, `messages.manage`): `staff.conversation.new` (once per
  conversation), `staff.conversation.reply` (customer wrote on a pending or resolved
  conversation; to the assignee when there is one, skipped when they are viewing it), and
  `staff.conversation.assigned` (only the assignee; email on by default). Replays notify nobody.
- `GET /api/manage/inbox/analytics/?days=7|30|90`: conversations, unique participants, messages
  in and out, resolved, open, pending, unassigned and unread now, median and average first
  response (first customer message to first human reply), average resolution, conversations by
  channel, linked orders, and the paid value of linked orders (only with `reports.view`). Agent
  view: assigned, resolved, replies, median first response. Store time zone; no keystroke or
  presence tracking.

## 9. Operations

- Beat: `communications.sweep` (every minute: re-queue lost deliveries, which keep their stored
  template values, and media; settle sends a dead worker left as `unknown`) and `communications.housekeeping` (hourly: auto-close, retention).
- Settings: `MATECART_CHANNEL_LAYER_URL` (required in production), `META_APP_ID`,
  `META_APP_SECRET` (set both or neither), `META_WEBHOOK_VERIFY_TOKEN`, `META_LOGIN_CONFIG_ID`,
  `META_WHATSAPP_CONFIG_ID`, `META_HUMAN_AGENT_APPROVED`, `META_GRAPH_API_VERSION`,
  `MATECART_WEBSOCKET_ORIGINS`, `MATECART_CONVERSATION_RETENTION_DAYS`,
  `MATECART_GUEST_CHAT_RETENTION_DAYS`, `MATECART_CHANNEL_SIMULATOR` (refused in production).
- Development simulator (D-097): "Use simulator" in Social channels creates a clearly labelled
  connection whose sends never leave the machine. Inbound messages:
  `docker compose exec commerce-api python manage.py simulate_inbound --channel whatsapp
--sender 8801711000001 --name "Sadia" --text "Hello"` (runs the post-signature processing).

## 10. External prerequisites (not available locally)

- A Meta app per store deployment (or an approved shared-app design, see DECISIONS Q8) with the
  webhook URL `https://<store>/api/webhooks/meta/`, app secret and verify token.
- App Review for `pages_messaging` (Messenger), `instagram_manage_messages` and
  `instagram_basic` (Instagram professional accounts linked to a Page), and
  `whatsapp_business_messaging` and `whatsapp_business_management` (WhatsApp, with Embedded
  Signup configuration). Human Agent replies need Meta's separate approval.
- Instagram: only professional (business or creator) accounts linked to a Facebook Page are
  supported by this adapter; personal accounts cannot be connected.
- WhatsApp templates are created and approved in Meta's tools; a business phone number must be
  registered there. Meta's conversation charges are billed by Meta; MateCart only displays the
  pricing category Meta reports.
- Facebook Shop or catalogue synchronisation is not built (separate catalogue permissions);
  MateCart stays the source of truth for products, prices, stock and orders.

# Mail and notifications (Phase 13)

How a MateCart store sends email, SMS and in-app notifications. Decisions:
[DECISIONS.md](DECISIONS.md) D-086 to D-090. Code: `services/commerce-api/commerce/notifications/`.

Everything runs inside the store's own deployment: its records, credentials, templates, queue and
logs. Nothing depends on the NetaMate Central Console, and the Control API keeps its own separate
email settings (D-090).

## 1. Engine and lifecycle (D-087)

```text
business change (order, payment, shipment, request, security event)
  └─ notification rows written in the same transaction (savepoint; never breaks the change)
       └─ after commit: Celery task queued   ← the beat sweeper re-queues anything missed
            └─ deliver(): claim → send outside locks → append DeliveryAttempt → final state
```

| Status       | Meaning                                                                             |
| ------------ | ----------------------------------------------------------------------------------- |
| `pending`    | Recorded, waiting to be queued or for its retry time                                |
| `queued`     | Handed to the broker                                                                |
| `processing` | A worker is sending it                                                              |
| `sent`       | The provider accepted it. This is not proof of inbox delivery                       |
| `delivered`  | Reserved for providers that confirm delivery; no current provider does              |
| `failed`     | Permanent error, retries exhausted, or outcome unknown (see below)                  |
| `suppressed` | Not sent on purpose: consent withdrawn, SMS unavailable, allowance used, rate limit |
| `cancelled`  | Withdrawn before sending                                                            |

- **Idempotency.** `event_key` is the event's business key (for example
  `order.placed:<order id>`). It is unique together with the channel and a hash of the recipient
  (database constraint), so retries, replays and repeated callbacks create nothing new.
- **Retries.** At most 5 attempts, with backoff of 1, 5, 15 and 30 minutes. Only temporary
  errors are retried: connection problems, TLS failures, timeouts before DATA, SMTP 4xx, and
  MateMail 429 or 5xx.
- **Outcome unknown.**
  - Triggers: SMTP fails after the message data was sent, MateMail answers 409 for an unresolved
    key, or a worker dies while sending.
  - The message is marked `failed / outcome_unknown` and never resent automatically, because the
    provider may already have accepted it.
  - Staff can resend it deliberately, which is audited and rate limited. MateCart does not claim
    exactly-once delivery.
- **One transport.**
  - The store's enabled, connected connection (SMTP or MateMail) is used when there is one.
    Otherwise the deployment default is used (`EMAIL_URL`; Mailpit locally).
  - A failure never fails over to a second provider.
- **Durability.** If Redis is down when an event commits, the row stays `pending` and the beat
  sweeper (every minute) queues it later. A row left `queued` for 5 minutes is queued again;
  delivery claims rows, so a duplicate task does nothing.
- **Commerce safety.** Notifications only observe committed changes. They never change totals,
  payment state, stock, refunds, invoices, loyalty or accounts. Their failure never fails an order.

## 2. Events

| Audience  | Events                                                                                                                                                                                                                            |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Customer  | Order placed, confirmed, cancelled; payment received (verified only); payment not completed (window expired); shipped; delivered; delivery failed; refund started; refund completed; request received, updated, replied           |
| Staff     | New order (manual orders show their source); cancelled; ready to fulfil; payment problem; cash collected; risk review; delivery failed; new request; customer message; review to moderate; low stock; out of stock; daily summary |
| Account   | Password changed, two-step verification changed, team role changed                                                                                                                                                                |
| Marketing | Points reminder: explicit opt-in only, at most every 30 days                                                                                                                                                                      |

- **COD.** A cash-on-delivery order is never reported as paid until cash is recorded. "Payment
  received" needs `payment_status = paid`.
- **Courier callbacks.** Only staff transitions and adapter-verified callbacks change shipments;
  repeating a status sends nothing.
- **Staff routing.** Staff receive in-app copies only for events their role's permission covers,
  and the inbox re-checks the permission when it is read. An order handler sees order alerts but
  not cash or finance alerts; a content editor receives none of either. Staff email copies are
  opt-in per topic.
- **Stock alerts** compare each variant's level (ok, low, out) from sellable stock (expired batches
  excluded) with the last alert, every 15 minutes, and alert only when stock gets worse. Recovery
  resets the state silently.

## 3. Templates and rendering (D-088)

- Every event has default English and Bangla text. Merchants can override the subject, heading,
  body, SMS text and in-app title and body for each language, and switch an event off. Security
  events cannot be switched off.
- Text is plain. Placeholders like `{order_number}` must come from that event's allowlist, and
  values come from server records. Nothing is evaluated. HTML values are escaped, headers are
  single-line, and links point only at the store's own public URL.
- The branded layout (logo, brand colour, button, contact footer, unsubscribe link for marketing)
  is built by the server. Merchants cannot add HTML.
- Language: the recipient's language when known, otherwise the store default. A missing
  translation falls back to the English override, then to the default text.
- **Versioning.** Content is rendered when the event happens and stored with the template
  version, so a later edit never changes a committed message.
- **Retention** (daily):
  - message content and recipient address are cleared after 90 days;
  - read in-app notifications are deleted after 180 days;
  - notification records are deleted after 400 days;
  - SMS usage and consent evidence are kept as accounting and legal records.

## 4. Email connections (D-086)

### Standard SMTP

Fields: host, port (25, 465, 587 or 2525), STARTTLS or TLS, username, password, From address and
name, Reply-To.

- The password is encrypted with the existing `commerce.core.security` key. The dashboard only
  shows "password saved".
- **Connected** requires two things:
  1. **Test connection** logs in successfully.
  2. **Verify sender**: a six-digit code is sent to the From address through that server, and
     entering it proves the store controls that mailbox. This stops a store sending as an
     address it does not own.
- Changing credentials or the From address returns the connection to `unverified`.
- **Send test to me** goes only to the signed-in staff member's verified address, so it cannot be
  used as a relay. It is rate limited.
- **Network safety:**
  - Host names are resolved once and every address must be public. The socket is pinned to that
    address while TLS verifies the host name, which defeats DNS rebinding.
  - TLS 1.2 or later with certificate validation, and a 15-second timeout.
  - Errors are reduced to safe codes such as `authentication_failed` or `certificate_invalid`.
  - Only development settings allow the local `mailpit` host, a private address and plain SMTP.
- Credential changes need `store.settings` and a recent sign-in, and are audited with field
  names only.

### IMAP (capability boundary)

- The store can save IMAP settings and verify them: MateCart logs in and opens INBOX read-only,
  then logs out. Nothing is read, moved or deleted.
- MateCart does **not** process inbound mail. Bounce parsing from a general mailbox would mean
  reading private mail, and a dedicated bounce mailbox with credential isolation is a deployment
  decision. Customer conversations belong to Phase 14.

### MateMail Connected App

Read-only audit of the MateMail repository (`docs/CONNECTED_APPS.md` and
`backend/apps/integrations`), done during Phase 13:

- A MateMail admin creates a Connected App scoped to **one mailbox** and grants scopes. Sending
  needs `mail.send`. MateMail shows a Tenant ID and a one-time Integration Secret.
- `POST /api/integrations/connect/start/` returns a 10-minute authorization URL and a poll token.
- The merchant approves in MateMail (password and 2FA). `POST /api/integrations/connect/status/`
  then returns a revocable `mmc_` access token once.
- `POST /api/integrations/external/send/`:
  - Authentication: `Bearer mmc_…` plus `X-MateMail-Tenant`.
  - Requires the `mail.send` scope.
  - **The From address is always the approved mailbox**, enforced by MateMail.
  - A required `idempotency_key`: a replay returns the stored result, and an unresolved key
    returns 409 "will not be sent again".
  - A per-mailbox rate limit returns 429; a revoked token returns 401.
- `GET …/external/profile/` reports scopes; `POST …/external/disconnect/` revokes the token.

How MateCart uses it:

- Setup: the merchant pastes the Tenant ID and Integration Secret, opens the authorization link,
  then presses "Check approval". The secret is used once and never stored. The token is stored
  encrypted.
- **Connected** requires `mail.send`. "Verify" re-checks the profile; a 401 marks the connection
  `revoked`.
- The idempotency key is `matecart-<notification id>` on every attempt, and a 409 is treated as
  outcome unknown.
- MateMail's address is the deployment setting `MATEMAIL_BASE_URL` (HTTPS), never merchant input.
- **Gaps (owner information, not blockers):**
  - MateMail access tokens have no expiry or refresh API. Rotation means reconnecting: MateCart
    then disconnects the old token.
  - There is no delivery-status webhook, so MateMail messages stay `sent` (accepted).
  - Live acceptance needs a MateMail instance and a Connected App. Local development leaves
    `MATEMAIL_BASE_URL` empty, so the option shows as unavailable. Tests use a fake.

### Deliverability (merchant and deployment checklist)

MateCart does not manage DNS.

- For SMTP the sending domain needs **SPF**, which includes the provider; **DKIM**, signed by the
  provider; and a **DMARC** policy. The From domain must align with the SPF or DKIM domain.
- Use the provider's authenticated submission (port 587 STARTTLS or 465 TLS) with an app
  password.
- Reply-To may differ from the From address; replies go there.
- MateMail handles signing for its own domains (see its deliverability documents).

## 5. SMS (D-089)

- **Provider.** It is set per deployment (`MATECART_SMS_PROVIDER`, an adapter class), and is
  the same boundary as authentication SMS.
- **Sender id.**
  - The merchant can only **request** one; its status becomes `pending`.
  - NetaMate records the provider's decision with
    `manage.py approve_sms_sender <id> [--reject] [--allowance N] [--prepaid N]`.
  - SMS cannot be switched on until it is approved, and only the approved id is passed to the
    provider.
- **What is sent.** Order placed, shipped and delivered, each with a store toggle. A signed-in
  customer can switch SMS order updates off. Phone numbers are E.164 from checkout.
- **Accounting.**
  - Each submission records its segments: GSM-7 160 characters (153 per part), otherwise UCS-2
    70 (67 per part). Bangla is UCS-2.
  - The monthly allowance (store time zone) is used first, then administratively provisioned
    prepaid credits.
  - With neither left, SMS is suppressed (`sms_allowance_exhausted`). Authentication SMS is
    counted but never blocked by the allowance.
  - No purchase or recharge exists (deferred billing).
- **Limits.** 6 messages per recipient per hour and 500 per store per hour.
- **Locally.** No SMS provider is configured, so SMS shows as unavailable. Tests use the fake
  provider.

## 6. Authentication messages

Codes, reset links and invitations keep the Phase 03 path (D-041):

- They are rendered in memory and sent synchronously after commit.
- They are never put in Celery, a notification record, a delivery log or an audit entry.
- They are never delayed behind other notifications.

Phase 13 changes only the transport: the store's connected connection, or the deployment default.

## 7. In-app notifications, preferences and consent (D-090)

- **Staff.**
  - A bell with an unread count, refreshed every 60 seconds while the tab is visible, plus the
    `/manage/notifications` page.
  - Rows belong to one user, and links are dashboard paths behind the same permission.
- **Customers.**
  - Their own notifications, in their account only.
  - Guest and merchant-assisted orders never create or link accounts. Guest emails go to the
    checkout contact; manual orders carry no order link.
- **Preferences.**
  - Order and security email is essential and stays on.
  - Customers may switch SMS order updates off.
  - Staff choose email per topic: orders, fulfilment, requests, stock, risk, daily summary.
- **Marketing.**
  - Off until the customer opts in from their account. Each opt-in and withdrawal is appended to
    `CommunicationConsent`, an append-only table protected by a trigger.
  - Phase 12 cookie or tracking consent never counts.
  - Every marketing email carries a signed, store-bound unsubscribe link (90 days). Opening it
    does nothing until the visitor presses Unsubscribe; using it again is harmless.
  - A queued marketing message is suppressed if consent was withdrawn before sending.
- **Daily summary.** Sent after 08:00 store time for staff with reports access who opted in.
  Figures come from orders and verified payments only.

## 8. Scheduler (`commerce-beat`)

A MateCart-only Celery beat service (`compose.yaml`, project `matecart-dev`) runs these jobs.
Every job is idempotent.

| Job                   | Frequency        | Purpose                                        |
| --------------------- | ---------------- | ---------------------------------------------- |
| `notifications.sweep` | every minute     | Queue due and stranded notifications           |
| Stock alerts          | every 15 minutes | Threshold transitions                          |
| Daily summary         | hourly           | Sends once per store day after 08:00           |
| Points reminders      | daily            | Opted-in customers only, at most every 30 days |
| Retention clean-up    | daily            | Section 3                                      |

Production deployments run one beat per store (Phase 17 packages it).

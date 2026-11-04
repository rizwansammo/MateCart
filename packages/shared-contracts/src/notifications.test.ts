import { describe, expect, it } from 'vitest';

import {
  deliveryAttempts,
  deliveryPage,
  deliverySummary,
  emailSettings,
  notificationPage,
  notificationPreferences,
  notificationTemplates,
  safeNotificationLink,
  smsSettings,
  templatePreview,
  unreadCount,
  unsubscribeResult,
} from './notifications';

const connection = (provider: 'smtp' | 'matemail' = 'smtp') => ({
  provider,
  is_enabled: false,
  status: 'unverified',
  display_name: '',
  smtp_host: 'smtp.example.com',
  smtp_port: 587,
  smtp_security: 'starttls',
  smtp_username: 'shop@example.com',
  smtp_password_set: true,
  from_email: 'shop@example.com',
  from_name: 'Shop',
  reply_to: '',
  sender_verified: false,
  sender_code_pending: false,
  imap_host: '',
  imap_port: null,
  imap_security: 'tls',
  imap_username: '',
  imap_password_set: false,
  imap_status: 'not_configured',
  imap_verified_at: null,
  imap_error: '',
  matemail_tenant_id: '',
  matemail_authorization_url: '',
  matemail_request_expires_at: null,
  matemail_mailbox: '',
  matemail_scopes: [],
  matemail_token_set: false,
  last_verified_at: null,
  last_error: '',
});

const item = {
  id: '00000000-0000-7000-8000-000000000001',
  kind: 'staff.order.new',
  category: 'operational',
  title: 'New order',
  body: 'Order ORD-000001 was placed.',
  link: '/manage/orders/00000000-0000-7000-8000-000000000002',
  created_at: '2026-11-04T05:00:00Z',
  read_at: null,
};

describe('notification contracts', () => {
  it('decodes inbox pages and unread counts', () => {
    const page = notificationPage({ count: 1, page: 1, page_size: 25, results: [item] });
    expect(page.results[0]?.title).toBe('New order');
    expect(unreadCount({ unread: 3 })).toBe(3);
    expect(() => unreadCount({ unread: 1.5 })).toThrow();
    expect(() =>
      notificationPage({ count: 1, page: 1, page_size: 25, results: [{ ...item, category: 'x' }] }),
    ).toThrow();
  });

  it('keeps email connection secrets out: only the "is set" flags are decoded', () => {
    const decoded = emailSettings({
      active_provider: null,
      default_sender_available: true,
      matemail_available: false,
      smtp: { ...connection(), smtp_password: 'hunter2', matemail_access_token: 'mmc_x' },
      matemail: connection('matemail'),
    });
    expect(decoded.smtp.smtp_password_set).toBe(true);
    expect(JSON.stringify(decoded)).not.toContain('hunter2');
    expect(JSON.stringify(decoded)).not.toContain('mmc_x');
    expect(() =>
      emailSettings({
        active_provider: 'smtp',
        default_sender_available: true,
        matemail_available: true,
        smtp: { ...connection(), status: 'great' },
        matemail: connection('matemail'),
      }),
    ).toThrow();
    // An authorization link must be HTTPS.
    expect(() =>
      emailSettings({
        active_provider: null,
        default_sender_available: true,
        matemail_available: true,
        smtp: connection(),
        matemail: { ...connection('matemail'), matemail_authorization_url: 'http://evil' },
      }),
    ).toThrow();
  });

  it('decodes deliveries, attempts, summary, SMS, templates, previews and preferences', () => {
    const delivery = {
      id: item.id,
      kind: 'order.placed',
      category: 'transactional',
      channel: 'email',
      audience: 'customer',
      recipient: 'r***@example.com',
      subject: 'Order received',
      status: 'failed',
      attempts: 5,
      provider: 'smtp',
      failure_code: 'retries_exhausted',
      created_at: item.created_at,
      last_attempt_at: item.created_at,
      sent_at: null,
      can_retry: true,
    };
    expect(deliveryPage({ count: 1, page: 1, page_size: 25, results: [delivery] }).count).toBe(1);
    expect(() =>
      deliveryPage({ count: 1, page: 1, page_size: 25, results: [{ ...delivery, status: 'ok' }] }),
    ).toThrow();
    expect(
      deliveryAttempts([
        {
          number: 1,
          provider: 'smtp',
          outcome: 'retryable',
          error_code: 'timeout',
          next_retry_at: item.created_at,
          occurred_at: item.created_at,
        },
      ])[0]?.error_code,
    ).toBe('timeout');
    expect(deliverySummary({ pending: 1, sent: 2, failed: 0, suppressed: 0 }).sent).toBe(2);
    const sms = smsSettings({
      provider_configured: false,
      is_enabled: false,
      requested_sender: 'SHOP',
      approved_sender: '',
      sender_status: 'pending',
      order_updates: true,
      shipping_updates: true,
      ready: false,
      balance: { allowance: 100, used: 2, remaining: 98, prepaid_credits: 0 },
    });
    expect(sms.balance.remaining).toBe(98);
    const templates = notificationTemplates([
      {
        key: 'order.placed',
        title: 'Order placed',
        category: 'transactional',
        audience: 'customer',
        variables: ['order_number'],
        supports_sms: true,
        locales: [
          {
            locale: 'bn',
            customized: false,
            is_enabled: true,
            version: 0,
            subject: '',
            heading: '',
            body: '',
            sms: '',
            in_app_title: '',
            in_app_body: '',
            default_subject: 'অর্ডার',
            default_heading: '',
            default_body: '',
            default_sms: '',
          },
        ],
      },
    ]);
    expect(templates[0]?.locales[0]?.default_subject).toBe('অর্ডার');
    expect(
      templatePreview({
        subject: 's',
        text: 't',
        html: '<p>h</p>',
        sms: '',
        sms_segments: 0,
        in_app_title: 'a',
        in_app_body: 'b',
      }).html,
    ).toBe('<p>h</p>');
    expect(
      notificationPreferences([
        { topic: 'marketing', title: 'Offers', channel: 'email', enabled: false, locked: false },
      ])[0]?.topic,
    ).toBe('marketing');
    expect(unsubscribeResult({ unsubscribed: false })).toBe(false);
  });

  it('only follows same-site links', () => {
    expect(safeNotificationLink('/manage/orders/1', true)).toBe('/manage/orders/1');
    expect(safeNotificationLink('/orders/1', true)).toBeNull();
    expect(safeNotificationLink('/orders/1', false)).toBe('/orders/1');
    expect(safeNotificationLink('/manage/orders/1', false)).toBeNull();
    for (const bad of ['https://evil.test/manage', '//evil.test', 'javascript:alert(1)', '']) {
      expect(safeNotificationLink(bad, true)).toBeNull();
      expect(safeNotificationLink(bad, false)).toBeNull();
    }
  });
});

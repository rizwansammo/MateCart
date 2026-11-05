import { describe, expect, it } from 'vitest';
import {
  authorizeUrl,
  channelCard,
  chatState,
  conversationPage,
  inboxEvent,
  safeProductLink,
  staffMessage,
  visitorEvent,
  visitorMessage,
} from './communications';

const message = {
  id: '01a1a838-0000-7000-8000-000000000001',
  seq: 3,
  direction: 'outbound',
  kind: 'product',
  body: 'Kantha kurti\nBDT 1,250.00',
  created_at: '2026-11-05T10:00:00Z',
  client_key: '',
  sender_name: 'Hasan',
  product: {
    product_id: 'p-1',
    variant_id: 'v-1',
    name: 'Kantha kurti',
    variant_title: 'M',
    sku: 'KK-M',
    amount: '1250.00',
    currency: 'BDT',
    in_stock: true,
    url: 'javascript:alert(1)',
    image_url: null,
  },
  attachment: null,
};

describe('communications contracts', () => {
  it('keeps only safe product links', () => {
    expect(safeProductLink('/products/kurti')).toBe('/products/kurti');
    expect(safeProductLink('https://shop.example.com/products/kurti')).toBe(
      'https://shop.example.com/products/kurti',
    );
    for (const bad of [
      'javascript:alert(1)',
      '//evil.example/x',
      'http://shop.example.com/x',
      '/\\evil',
    ]) {
      expect(safeProductLink(bad)).toBe('');
    }
    expect(visitorMessage(message).product?.url).toBe('');
  });

  it('accepts only same-site attachment URLs', () => {
    const file = {
      id: 'a',
      status: 'stored',
      content_type: 'image/png',
      size_bytes: 10,
      width: 4,
      height: 3,
      filename: 'photo.png',
      url: '/api/chat/attachments/01a1a838-0000-7000-8000-000000000009/',
    };
    expect(
      visitorMessage({ ...message, kind: 'image', product: null, attachment: file }).attachment
        ?.url,
    ).toBe(file.url);
    expect(() =>
      visitorMessage({
        ...message,
        kind: 'image',
        attachment: { ...file, url: 'https://evil.example/x' },
      }),
    ).toThrow();
  });

  it('decodes staff messages with their delivery state', () => {
    const staff = staffMessage({
      ...message,
      delivery_status: 'accepted',
      status_changed_at: null,
      error_code: '',
      provider_timestamp: null,
      sender: { id: 'u-1', name: 'Hasan Ali' },
      template_name: '',
      template_language: '',
      reply_tag: '',
      pricing_category: '',
      attempts: 1,
    });
    expect(staff.delivery_status).toBe('accepted');
    expect(() => staffMessage({ ...staff, delivery_status: 'teleported' })).toThrow();
  });

  it('follows only Meta dialog URLs', () => {
    const ok = 'https://www.facebook.com/v26.0/dialog/oauth?client_id=1&state=x';
    expect(authorizeUrl({ authorize_url: ok })).toBe(ok);
    for (const bad of [
      'https://evil.example/v26.0/dialog/oauth?x=1',
      'http://www.facebook.com/v26.0/dialog/oauth?x=1',
      'https://www.facebook.com.evil.example/v26.0/dialog/oauth?x=1',
    ]) {
      expect(() => authorizeUrl({ authorize_url: bad })).toThrow();
    }
  });

  it('never keeps unexpected fields such as tokens', () => {
    const card = channelCard({
      channel: 'whatsapp',
      state: 'connected',
      id: 'c-1',
      is_enabled: true,
      is_simulated: false,
      display_name: 'Kantha',
      display_identifier: '+880 1700',
      account_id: '1066',
      connected_at: null,
      last_verified_at: null,
      last_error: '',
      requirements: [],
      templates: 0,
      access_token: 'EAAG-secret',
    });
    expect(JSON.stringify(card)).not.toContain('EAAG');
  });

  it('decodes inbox pages and live frames, ignoring unknown ones', () => {
    const page = conversationPage({ results: [], next_cursor: null, unread_conversations: 2 });
    expect(page.unread_conversations).toBe(2);
    expect(inboxEvent({ type: 'pong' })).toEqual({ type: 'pong' });
    expect(inboxEvent({ type: 'mystery' })).toBeNull();
    expect(inboxEvent({ type: 'message', conversation: 'c', message: {} })).toBeNull();
    expect(visitorEvent({ type: 'nack', client_key: 'k', code: 'rate_limited' })).toEqual({
      type: 'nack',
      client_key: 'k',
      code: 'rate_limited',
    });
    expect(visitorEvent('not an object')).toBeNull();
  });

  it('decodes the visitor chat state', () => {
    const state = chatState({
      config: {
        enabled: true,
        welcome: 'Hi',
        button_label: 'Chat',
        away_message: 'Away',
        languages: ['en', 'bn'],
        position: 'right',
        online: false,
        within_hours: true,
      },
      conversation_status: null,
      messages: [],
      signed_in: false,
      guest_chat_linkable: false,
    });
    expect(state.config.online).toBe(false);
  });
});

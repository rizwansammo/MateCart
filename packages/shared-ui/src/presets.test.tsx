import { render, screen } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { createApiClient } from './api';
import {
  PRESET_IDS,
  THEMES,
  contrast,
  isReadable,
  onColor,
  resolveTheme,
  themeVariables,
} from './presets';
import { StoreThemeScope, ThemePreview } from './theme-ui';

describe('preset themes', () => {
  it.each([
    ['fashion', '#FBF8F3', '#2A2620', '#9C4A2B', 'instrument-serif'],
    ['grocery', '#F6F8F4', '#00B050', '#FFF3DC', 'geist'],
    ['electronics', '#0B0F14', '#2F6BFF', '#F3F5F8', 'geist'],
    ['general', '#FAFAF9', '#0D0F0E', '#C8312B', 'geist'],
    ['books', '#F7F2E8', '#6E1F2A', '#1B1A17', 'newsreader'],
  ] as const)('%s resolves to the approved identity', (id, swatch, primary, accent, display) => {
    const theme = resolveTheme(id);
    expect(theme.theme.swatches).toEqual([swatch, primary, accent]);
    expect(theme.colors.primary).toBe(primary);
    expect(theme.colors.accent).toBe(accent);
    expect(theme.theme.fonts.display).toBe(display);
  });

  it('keeps the distinctive layout and type choices', () => {
    expect(THEMES['fashion-editorial'].layout.cardAspect).toBe('3/4');
    expect(THEMES['grocery-quickshop'].layout.quantityStepper).toBe(true);
    expect(THEMES['grocery-quickshop'].layout.density).toBe('compact');
    expect(THEMES['electronics-spec-first'].layout.darkHeader).toBe(true);
    expect(THEMES['electronics-spec-first'].fonts.spec).toBe('geist-mono');
    expect(THEMES['books-literary'].layout.cardAspect).toBe('2/3');
  });

  it.each(PRESET_IDS)('%s call-to-action text meets WCAG AA', (id) => {
    const { colors } = resolveTheme(id);
    expect(contrast(colors.action, colors.onAction)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.header, colors.onHeader)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.canvas, colors.ink)).toBeGreaterThanOrEqual(4.5);
  });

  it('applies valid brand overrides to the brand and action colours', () => {
    const theme = resolveTheme('books', { primaryColor: '#1f6f4a', accentColor: '#C8312B' });
    expect(theme.colors.primary).toBe('#1F6F4A');
    expect(theme.colors.action).toBe('#1F6F4A');
    expect(theme.colors.onAction).toBe('#FFFFFF');
    expect(theme.colors.accent).toBe('#C8312B');
  });

  it.each(['red', '#12345', '#777777', '', null, 'var(--x)', '#000000;background:url(x)'])(
    'ignores the unsafe or unreadable override %j',
    (value) => {
      const theme = resolveTheme('fashion', { primaryColor: value });
      expect(theme.colors.primary).toBe(THEMES['fashion-editorial'].colors.primary);
      expect(theme.colors.action).toBe(THEMES['fashion-editorial'].colors.action);
    },
  );

  it.each([undefined, null, 'marketplace', 42])('falls back to General for %j', (preset) => {
    expect(resolveTheme(preset).preset.id).toBe('general');
  });

  it('matches the server colour rules', () => {
    expect(onColor('#00B050')).toBe('#0D0F0E');
    expect(onColor('#2A2620')).toBe('#FFFFFF');
    expect(isReadable('#777777')).toBe(false);
  });

  it('exposes every token as a CSS variable', () => {
    const vars = themeVariables(resolveTheme('electronics'));
    expect(vars['--store-header']).toBe('#0B0F14');
    expect(vars['--store-on-header']).toBe('#FFFFFF');
    expect(vars['--store-font-spec']).toContain('--font-geist-mono');
    expect(Object.values(vars).every((value) => !value.includes(';'))).toBe(true);
  });
});

describe('theme scope and preview', () => {
  it('renders the preset on the server, with no client state', () => {
    const html = renderToString(
      <StoreThemeScope theme={resolveTheme('fashion')}>
        <p>Woven slowly</p>
      </StoreThemeScope>,
    );
    expect(html).toContain('data-preset="fashion"');
    expect(html).toContain('--store-canvas:#FBF8F3');
  });

  it('shows an accessible sample for each preset', () => {
    render(<ThemePreview theme={resolveTheme('books')} storeName="Northlight Books" />);
    expect(screen.getByText('Northlight Books')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /2\/3 ratio/ })).toBeInTheDocument();
    expect(screen.getByText('Shop now')).toBeInTheDocument();
  });

  it('uses the logo when one is set', () => {
    render(<ThemePreview theme={resolveTheme('general')} storeName="Store" logoUrl="/logo.png" />);
    expect(screen.getByRole('img', { name: 'Store logo' })).toHaveAttribute('src', '/logo.png');
  });
});

describe('API client writes', () => {
  function client() {
    document.cookie = 'mc_csrf=token-1';
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    return { api: createApiClient({ csrfCookie: 'mc_csrf', fetchImpl }), fetchImpl };
  }

  it('sends PUT and DELETE with the CSRF token', async () => {
    const { api, fetchImpl } = client();
    await api.put('/api/x/', { a: 1 });
    await api.delete('/api/x/');
    const calls = fetchImpl.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([, init]) => init.method)).toEqual(['PUT', 'DELETE']);
    expect((calls[0]?.[1].headers as Record<string, string>)['X-CSRFToken']).toBe('token-1');
  });

  it('uploads multipart forms without forcing a JSON content type', async () => {
    const { api, fetchImpl } = client();
    const form = new FormData();
    form.append('file', new Blob(['x']), 'x.png');
    await api.upload('/api/upload/', form);
    const [, init] = (fetchImpl.mock.calls as unknown as [string, RequestInit][])[0] ?? [];
    expect(init?.body).toBe(form);
    expect((init?.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });
});

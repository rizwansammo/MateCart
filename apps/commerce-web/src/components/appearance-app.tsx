'use client';

import { useCallback, useEffect, useId, useState } from 'react';
import type { ReactNode, TextareaHTMLAttributes } from 'react';
import type { StoreAppearance, StoreModuleState } from '@matecart/shared-contracts';
import { storeAppearance, storeModuleState } from '@matecart/shared-contracts';
import {
  Alert,
  Button,
  Loading,
  PRESET_IDS,
  TextField,
  ThemePreview,
  errorMessage,
  isHexColor,
  isReadable,
  resolveTheme,
  translationLanguage,
  withTranslation,
} from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

const BASE = '/api/manage/store';

export function TextArea({
  label,
  hint,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: string }) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <label className="text-[13px] font-semibold" htmlFor={id}>
        {label}
      </label>
      <textarea
        {...props}
        id={id}
        aria-describedby={hint ? `${id}-help` : undefined}
        className="min-h-24 w-full rounded-base border border-line-strong bg-surface p-3 text-[15px] leading-6 outline-none focus:border-brand-deep focus:ring-3 focus:ring-brand-tint"
      />
      {hint && (
        <p id={`${id}-help`} className="text-xs leading-5 text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

export function Panel({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-base border border-line bg-surface p-5 sm:p-6">
      <h2 className="text-lg font-bold tracking-tight">{title}</h2>
      {intro && <p className="mt-1 text-sm leading-6 text-muted">{intro}</p>}
      <div className="mt-5 grid gap-4">{children}</div>
    </section>
  );
}

export async function uploadImage(file: File, alt: string): Promise<string> {
  const form = new FormData();
  form.append('file', file);
  form.append('alt_text', alt);
  const asset = await api.upload<{ id: string }>('/api/manage/media/assets/upload/', form);
  return asset.id;
}

type Draft = Pick<
  StoreAppearance,
  | 'store_name'
  | 'primary_color'
  | 'accent_color'
  | 'tagline'
  | 'description'
  | 'announcement'
  | 'footer_text'
  | 'show_name_with_logo'
> & { tagline_translation: string };

function translatedTagline(a: StoreAppearance): string {
  const language = translationLanguage(a.languages, a.default_language);
  return language ? (a.translations[language.code]?.tagline ?? '') : '';
}

function draftOf(a: StoreAppearance): Draft {
  return {
    store_name: a.store_name,
    primary_color: a.primary_color,
    accent_color: a.accent_color,
    tagline: a.tagline,
    description: a.description,
    announcement: a.announcement,
    footer_text: a.footer_text,
    show_name_with_logo: a.show_name_with_logo,
    tagline_translation: translatedTagline(a),
  };
}

function ColorField({
  label,
  value,
  fallback,
  onChange,
}: {
  label: string;
  value: string;
  fallback: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const valid = value === '' || (isHexColor(value) && isReadable(value.toUpperCase()));
  return (
    <div className="grid gap-1.5">
      <label className="text-[13px] font-semibold" htmlFor={id}>
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} picker`}
          value={isHexColor(value) ? value.toLowerCase() : fallback.toLowerCase()}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="h-[46px] w-14 cursor-pointer rounded-base border border-line-strong bg-surface p-1"
        />
        <input
          id={id}
          value={value}
          placeholder={`${fallback} (preset)`}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          aria-invalid={!valid || undefined}
          className={`h-[46px] w-full rounded-base border bg-surface px-3 font-mono text-sm outline-none focus:border-brand-deep ${valid ? 'border-line-strong' : 'border-danger-fg'}`}
        />
      </div>
      <p className={`text-xs ${valid ? 'text-muted' : 'text-danger-fg'}`}>
        {valid
          ? 'Leave empty to use the preset colour.'
          : 'Use #RRGGBB. Text on very mid-tone colours is hard to read; pick a lighter or darker shade.'}
      </p>
    </div>
  );
}

function ImageSlot({
  label,
  hint,
  asset,
  onUpload,
  onRemove,
}: {
  label: string;
  hint: string;
  asset: StoreAppearance['logo'];
  onUpload: (file: File) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const id = useId();
  return (
    <div className="grid gap-2 rounded-base border border-line p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">{label}</p>
          <p className="text-xs text-muted">{hint}</p>
        </div>
        {asset && (
          // eslint-disable-next-line @next/next/no-img-element -- private, API-streamed asset
          <img
            src={asset.url}
            alt=""
            className="h-10 w-auto max-w-28 rounded-badge object-contain"
          />
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <label
          htmlFor={id}
          className="inline-flex min-h-10 cursor-pointer items-center rounded-base border border-line-strong px-3 text-sm font-semibold hover:border-ink"
        >
          {busy ? 'Uploading…' : asset ? 'Replace' : 'Upload'}
        </label>
        <input
          id={id}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (!file) return;
            setBusy(true);
            try {
              await onUpload(file);
            } finally {
              setBusy(false);
            }
          }}
        />
        {asset && (
          <Button variant="secondary" type="button" onClick={() => void onRemove()}>
            Remove
          </Button>
        )}
      </div>
    </div>
  );
}

function Modules({ canManage }: { canManage: boolean }) {
  const [rows, setRows] = useState<StoreModuleState[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!canManage) return;
    void api
      .get<unknown[]>(`${BASE}/modules/`)
      .then((v) => setRows(v.map(storeModuleState)))
      .catch((e) => setError(errorMessage(e)));
  }, [canManage]);
  if (!canManage) return null;
  async function toggle(row: StoreModuleState) {
    setError('');
    try {
      const next = storeModuleState(
        await api.put<unknown>(`${BASE}/modules/${row.key}/`, { enabled: !row.enabled }),
      );
      setRows((current) => current?.map((r) => (r.key === next.key ? next : r)) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Panel
      title="Store features"
      intro="Recommended for your preset. Features marked “Coming later” stay off until their release."
    >
      {error && <Alert>{error}</Alert>}
      {!rows ? (
        <Loading>Loading features…</Loading>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {rows
            .filter((r) => r.applicable)
            .map((row) => (
              <li
                key={row.key}
                className="flex items-start justify-between gap-3 rounded-base border border-line p-3"
              >
                <div>
                  <p className="text-sm font-semibold">{row.name}</p>
                  <p className="text-xs leading-5 text-muted">{row.description}</p>
                  {!row.available && (
                    <p className="mt-1 text-[11px] font-bold tracking-wide text-warning-fg uppercase">
                      Coming later · Phase {row.phase}
                    </p>
                  )}
                </div>
                {row.core ? (
                  <span className="text-xs font-semibold text-success-fg">Always on</span>
                ) : (
                  <button
                    type="button"
                    role="switch"
                    aria-checked={row.enabled}
                    aria-label={row.name}
                    disabled={!row.available}
                    onClick={() => void toggle(row)}
                    className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${row.enabled ? 'bg-brand-deep' : 'bg-line-strong'}`}
                  >
                    <span
                      className={`absolute top-0.5 h-5 w-5 rounded-full bg-surface transition-all ${row.enabled ? 'left-[22px]' : 'left-0.5'}`}
                    />
                  </button>
                )}
              </li>
            ))}
        </ul>
      )}
    </Panel>
  );
}

export function AppearanceSettings({ canManageSettings }: { canManageSettings: boolean }) {
  const [data, setData] = useState<StoreAppearance | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const apply = useCallback((value: unknown) => {
    const next = storeAppearance(value);
    setData(next);
    setDraft(draftOf(next));
  }, []);
  useEffect(() => {
    void api
      .get<unknown>(`${BASE}/appearance/`)
      .then(apply)
      .catch((e) => setError(errorMessage(e)));
  }, [apply]);
  if (!data || !draft)
    return error ? <Alert>{error}</Alert> : <Loading>Loading appearance…</Loading>;

  const translation = translationLanguage(data.languages, data.default_language);
  // The assigned theme with the draft colours on top (D-069). The theme itself is read-only here.
  const theme = resolveTheme(
    data.preset,
    { primaryColor: draft.primary_color, accentColor: draft.accent_color },
    data.active_theme,
  );
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });

  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      apply(await action());
      setNotice(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function save() {
    if (!draft || !data) return;
    const body: Record<string, unknown> = {
      primary_color: draft.primary_color,
      accent_color: draft.accent_color,
      tagline: draft.tagline,
      description: draft.description,
      announcement: draft.announcement,
      footer_text: draft.footer_text,
      show_name_with_logo: draft.show_name_with_logo,
      translations: translation
        ? withTranslation(data.translations, translation.code, {
            tagline: draft.tagline_translation,
          })
        : data.translations,
    };
    if (data.can_rename) body.store_name = draft.store_name;
    void run(() => api.patch(`${BASE}/appearance/`, body), 'Your appearance is saved.');
  }
  const image = (field: 'logo' | 'logo_dark' | 'favicon') => ({
    asset: data[field],
    onUpload: (file: File) =>
      run(async () => {
        const id = await uploadImage(file, `${data.store_name} ${field.replace('_', ' ')}`);
        return api.patch(`${BASE}/appearance/`, { [field]: id });
      }, 'Image saved.'),
    onRemove: () =>
      run(() => api.patch(`${BASE}/appearance/`, { [field]: null }), 'Image removed.'),
  });

  return (
    <div className="grid gap-6">
      <div>
        <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">Online store</p>
        <h1 className="mt-2 text-[28px] font-bold tracking-tight">Store appearance</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">
          Your preset sets the look of your store. Add your logo, brand colours and a few words
          about your shop; changes here never affect products, prices or stock.
        </p>
      </div>
      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="grid gap-6">
          <Panel
            title={`${data.preset_label} preset`}
            intro={`${theme.theme.name} theme. ${theme.theme.description} Your preset and theme are set by NetaMate; your colours, logos and texts apply on top.`}
          >
            <div className="flex gap-2">
              {theme.theme.swatches.map((color) => (
                <span
                  key={color}
                  title={color}
                  className="h-8 w-8 rounded-badge border border-line"
                  style={{ backgroundColor: color }}
                />
              ))}
            </div>
          </Panel>
          <Panel title="Brand">
            <TextField
              label="Store name"
              value={draft.store_name}
              maxLength={120}
              disabled={!data.can_rename}
              hint={
                data.can_rename ? undefined : 'Only the owner or a manager can rename the store.'
              }
              onChange={(e) => set({ store_name: e.target.value })}
            />
            <TextField
              label="Tagline"
              value={draft.tagline}
              maxLength={160}
              onChange={(e) => set({ tagline: e.target.value })}
            />
            {translation && (
              <TextField
                label={`Tagline in ${translation.name}`}
                lang={translation.code}
                className={translation.code === 'bn' ? 'font-bangla' : ''}
                value={draft.tagline_translation}
                maxLength={160}
                onChange={(e) => set({ tagline_translation: e.target.value })}
              />
            )}
            <TextArea
              label="Short description"
              value={draft.description}
              maxLength={500}
              onChange={(e) => set({ description: e.target.value })}
            />
            <TextField
              label="Announcement bar"
              hint="A short message shown above your store's header."
              value={draft.announcement}
              maxLength={160}
              onChange={(e) => set({ announcement: e.target.value })}
            />
            <TextField
              label="Footer text"
              value={draft.footer_text}
              maxLength={300}
              onChange={(e) => set({ footer_text: e.target.value })}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.show_name_with_logo}
                onChange={(e) => set({ show_name_with_logo: e.target.checked })}
              />
              Show the store name next to the logo
            </label>
          </Panel>
          <Panel title="Colours" intro="Buttons and highlights use these colours.">
            <div className="grid gap-4 sm:grid-cols-2">
              <ColorField
                label="Brand colour"
                value={draft.primary_color}
                fallback={theme.theme.colors.primary}
                onChange={(v) => set({ primary_color: v })}
              />
              <ColorField
                label="Accent colour"
                value={draft.accent_color}
                fallback={theme.theme.colors.accent}
                onChange={(v) => set({ accent_color: v })}
              />
            </div>
          </Panel>
          <Panel
            title="Logos and icon"
            intro="PNG, JPEG or WebP, up to 10 MB. SVG files are not accepted."
          >
            <ImageSlot label="Logo" hint="At least 32 px tall." {...image('logo')} />
            <ImageSlot label="Logo for dark backgrounds" hint="Optional." {...image('logo_dark')} />
            <ImageSlot label="Browser icon" hint="Square, 32 to 1024 px." {...image('favicon')} />
          </Panel>
          <div className="flex flex-wrap gap-3">
            <Button busy={busy} onClick={save}>
              Save changes
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void run(() => api.post(`${BASE}/appearance/reset/`), 'Preset colours restored.')
              }
            >
              Restore preset colours
            </Button>
          </div>
        </div>
        <div className="grid gap-6 xl:sticky xl:top-6">
          <Panel
            title="Preview"
            intro="How your theme looks. Your full storefront arrives in the next release."
          >
            <ThemePreview
              theme={theme}
              storeName={draft.store_name}
              tagline={draft.tagline}
              logoUrl={data.logo?.url ?? null}
            />
          </Panel>
          <Panel
            title="The five MateCart presets"
            intro="Each store uses one; shown here for reference."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              {PRESET_IDS.map((id) => (
                <div key={id} className="grid gap-1">
                  {id === data.preset && (
                    <span className="text-[11px] font-bold tracking-wide text-brand-deep uppercase">
                      Your preset
                    </span>
                  )}
                  <ThemePreview theme={resolveTheme(id)} storeName={data.store_name} compact />
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
      <Modules canManage={canManageSettings} />
    </div>
  );
}

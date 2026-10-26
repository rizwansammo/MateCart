'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ContentPage, HomeSection, SectionType } from '@matecart/shared-contracts';
import { contentPage, homeSection } from '@matecart/shared-contracts';
import type { TranslationLanguage } from '@matecart/shared-ui';
import {
  Alert,
  Button,
  ConfirmDialog,
  Dialog,
  Loading,
  TextField,
  errorMessage,
  translationLanguage,
  withTranslation,
} from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';
import { Panel, TextArea, uploadImage } from './appearance-app';

const BASE = '/api/manage/content';
const SECTION_LABELS: Record<SectionType, string> = {
  hero: 'Hero banner',
  category_showcase: 'Category showcase',
  collection_highlight: 'Collection highlight',
  brand_story: 'Brand story',
  trust_strip: 'Trust strip',
};
const TEMPLATES: { title: string; slug: string; page_type: 'page' | 'policy' }[] = [
  { title: 'About us', slug: 'about', page_type: 'page' },
  { title: 'Contact us', slug: 'contact', page_type: 'page' },
  { title: 'Privacy policy', slug: 'privacy-policy', page_type: 'policy' },
  { title: 'Terms and conditions', slug: 'terms', page_type: 'policy' },
  { title: 'Return and refund policy', slug: 'returns', page_type: 'policy' },
  { title: 'Shipping and delivery', slug: 'shipping', page_type: 'policy' },
];

interface Collection {
  id: string;
  name: string;
}

function SectionEditor({
  section,
  collections,
  onClose,
  onSaved,
}: {
  section: HomeSection;
  collections: Collection[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
    title: section.title,
    subtitle: section.subtitle,
    body: section.body,
    cta_label: section.cta_label,
    cta_url: section.cta_url,
    collection: section.collection?.id ?? '',
    image: section.image?.id ?? '',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    setError('');
    try {
      await api.patch(`${BASE}/sections/${section.id}/`, {
        ...form,
        collection: form.collection || null,
        image: form.image || null,
      });
      await onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title={SECTION_LABELS[section.section_type]} onClose={onClose}>
      <div className="grid gap-4">
        {error && <Alert>{error}</Alert>}
        <TextField
          label="Title"
          maxLength={120}
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
        />
        <TextField
          label="Subtitle"
          maxLength={200}
          value={form.subtitle}
          onChange={(e) => setForm({ ...form, subtitle: e.target.value })}
        />
        <TextArea
          label="Text"
          maxLength={1000}
          value={form.body}
          onChange={(e) => setForm({ ...form, body: e.target.value })}
        />
        {section.section_type === 'collection_highlight' && (
          <label className="grid gap-1.5 text-[13px] font-semibold">
            Collection
            <select
              value={form.collection}
              onChange={(e) => setForm({ ...form, collection: e.target.value })}
              className="h-[46px] rounded-base border border-line-strong bg-surface px-3 text-[15px] font-normal"
            >
              <option value="">Choose a collection</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Button label"
            maxLength={40}
            value={form.cta_label}
            onChange={(e) => setForm({ ...form, cta_label: e.target.value })}
          />
          <TextField
            label="Button link"
            hint="A page of your store, e.g. /collections/new-in"
            maxLength={200}
            value={form.cta_url}
            onChange={(e) => setForm({ ...form, cta_url: e.target.value })}
          />
        </div>
        <label className="grid gap-1.5 text-[13px] font-semibold">
          Image {form.image ? '(chosen)' : '(optional)'}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="text-sm font-normal"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              try {
                setForm({
                  ...form,
                  image: await uploadImage(file, form.title || 'Homepage image'),
                });
              } catch (err) {
                setError(errorMessage(err));
              }
            }}
          />
        </label>
        <div className="flex gap-3">
          <Button busy={busy} onClick={() => void save()}>
            Save section
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function Sections() {
  const [sections, setSections] = useState<HomeSection[] | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [editing, setEditing] = useState<HomeSection | null>(null);
  const [confirming, setConfirming] = useState<HomeSection | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    const rows = await api.get<unknown[]>(`${BASE}/sections/`);
    setSections(rows.map(homeSection));
  }, []);
  useEffect(() => {
    void Promise.resolve()
      .then(load)
      .catch((e) => setError(errorMessage(e)));
    void api
      .get<{ results: Collection[] }>('/api/manage/catalog/collections/?page_size=100')
      .then((page) =>
        setCollections(page.results.map((c) => ({ id: String(c.id), name: String(c.name) }))),
      )
      .catch(() => setCollections([]));
  }, [load]);
  async function act(action: () => Promise<unknown>) {
    setError('');
    try {
      await action();
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  function move(index: number, by: number) {
    if (!sections) return;
    const order = sections.map((s) => s.id);
    const [moved] = order.splice(index, 1);
    if (moved === undefined) return;
    order.splice(index + by, 0, moved);
    void act(() => api.put(`${BASE}/sections/order/`, { sections: order }));
  }
  if (!sections) return error ? <Alert>{error}</Alert> : <Loading>Loading homepage…</Loading>;
  return (
    <Panel title="Homepage sections" intro="Choose what your homepage shows and in which order.">
      {error && <Alert>{error}</Alert>}
      {sections.length === 0 ? (
        <div className="rounded-base border border-dashed border-line-strong p-6 text-center">
          <p className="text-sm text-muted">Your homepage has no sections yet.</p>
          <Button
            className="mt-3"
            onClick={() => void act(() => api.post(`${BASE}/sections/defaults/`))}
          >
            Use the suggested sections for my preset
          </Button>
        </div>
      ) : (
        <ol className="grid gap-2">
          {sections.map((section, index) => (
            <li
              key={section.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-base border border-line p-3"
            >
              <div className="min-w-0">
                <p className="text-xs font-bold tracking-wide text-muted uppercase">
                  {SECTION_LABELS[section.section_type]}
                </p>
                <p className="truncate text-sm font-semibold">{section.title || 'Untitled'}</p>
                {section.section_type === 'collection_highlight' && !section.collection && (
                  <p className="text-xs text-warning-fg">
                    Choose a collection to show this section.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={section.is_enabled}
                    onChange={() =>
                      void act(() =>
                        api.patch(`${BASE}/sections/${section.id}/`, {
                          is_enabled: !section.is_enabled,
                        }),
                      )
                    }
                  />
                  Shown
                </label>
                <Button
                  variant="secondary"
                  aria-label="Move up"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </Button>
                <Button
                  variant="secondary"
                  aria-label="Move down"
                  disabled={index === sections.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </Button>
                <Button variant="secondary" onClick={() => setEditing(section)}>
                  Edit
                </Button>
                <Button
                  variant="secondary"
                  aria-label={`Delete ${section.title || 'section'}`}
                  onClick={() => setConfirming(section)}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
      <div className="flex flex-wrap gap-2">
        {(Object.keys(SECTION_LABELS) as SectionType[]).map((type) => (
          <Button
            key={type}
            variant="secondary"
            onClick={() =>
              void act(() =>
                api.post(`${BASE}/sections/`, {
                  section_type: type,
                  // Hidden until it has a title (and, for highlights, a collection).
                  is_enabled: false,
                }),
              )
            }
          >
            + {SECTION_LABELS[type]}
          </Button>
        ))}
      </div>
      {confirming && (
        <ConfirmDialog
          title="Delete this section?"
          onCancel={() => setConfirming(null)}
          onConfirm={async () => {
            const target = confirming;
            setConfirming(null);
            await act(() => api.delete(`${BASE}/sections/${target.id}/`));
          }}
        >
          “{confirming.title || SECTION_LABELS[confirming.section_type]}” will be removed from your
          homepage permanently. This cannot be undone.
        </ConfirmDialog>
      )}
      {editing && (
        <SectionEditor
          section={editing}
          collections={collections}
          onClose={() => setEditing(null)}
          onSaved={load}
        />
      )}
    </Panel>
  );
}

function PageEditor({
  page,
  translation,
  onClose,
  onSaved,
}: {
  page: ContentPage | (typeof TEMPLATES)[number];
  translation: TranslationLanguage | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const existing = 'id' in page ? page : null;
  const [form, setForm] = useState({
    title: page.title,
    slug: page.slug,
    page_type: page.page_type,
    body: existing?.body ?? '',
    show_in_footer: existing?.show_in_footer ?? true,
    title_translation: translation ? (existing?.translations[translation.code]?.title ?? '') : '',
    body_translation: translation ? (existing?.translations[translation.code]?.body ?? '') : '',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    setError('');
    const body = {
      title: form.title,
      slug: form.slug,
      page_type: form.page_type,
      body: form.body,
      show_in_footer: form.show_in_footer,
      translations: translation
        ? withTranslation(existing?.translations ?? {}, translation.code, {
            title: form.title_translation,
            body: form.body_translation,
          })
        : (existing?.translations ?? {}),
    };
    try {
      await (existing
        ? api.patch(`${BASE}/pages/${existing.id}/`, body)
        : api.post(`${BASE}/pages/`, body));
      await onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title={existing ? 'Edit page' : 'New page'} onClose={onClose}>
      <div className="grid gap-4">
        {error && <Alert>{error}</Alert>}
        <TextField
          label="Title"
          maxLength={160}
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
        />
        <TextField
          label="Address"
          hint={`Shown as /pages/${form.slug || 'your-page'}. Lowercase letters, numbers and hyphens.`}
          maxLength={80}
          value={form.slug}
          onChange={(e) => setForm({ ...form, slug: e.target.value })}
        />
        <TextArea
          label="Page text"
          hint="Plain text. Leave a blank line between paragraphs."
          rows={8}
          maxLength={20000}
          value={form.body}
          onChange={(e) => setForm({ ...form, body: e.target.value })}
        />
        {translation && (
          <>
            <TextField
              label={`Title in ${translation.name}`}
              lang={translation.code}
              className={translation.code === 'bn' ? 'font-bangla' : ''}
              value={form.title_translation}
              onChange={(e) => setForm({ ...form, title_translation: e.target.value })}
            />
            <TextArea
              label={`Page text in ${translation.name}`}
              lang={translation.code}
              className={translation.code === 'bn' ? 'font-bangla' : ''}
              rows={5}
              value={form.body_translation}
              onChange={(e) => setForm({ ...form, body_translation: e.target.value })}
            />
          </>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.show_in_footer}
            onChange={(e) => setForm({ ...form, show_in_footer: e.target.checked })}
          />
          Link this page in the store footer
        </label>
        <div className="flex gap-3">
          <Button busy={busy} onClick={() => void save()}>
            Save as {existing?.status === 'published' ? 'published' : 'draft'}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function Pages({ translation }: { translation: TranslationLanguage | null }) {
  const [pages, setPages] = useState<ContentPage[] | null>(null);
  const [editing, setEditing] = useState<ContentPage | (typeof TEMPLATES)[number] | null>(null);
  const [confirming, setConfirming] = useState<ContentPage | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setPages((await api.get<unknown[]>(`${BASE}/pages/`)).map(contentPage));
  }, []);
  useEffect(() => {
    void Promise.resolve()
      .then(load)
      .catch((e) => setError(errorMessage(e)));
  }, [load]);
  async function act(action: () => Promise<unknown>) {
    setError('');
    try {
      await action();
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  if (!pages) return error ? <Alert>{error}</Alert> : <Loading>Loading pages…</Loading>;
  const missing = TEMPLATES.filter((t) => !pages.some((p) => p.slug === t.slug));
  return (
    <Panel title="Pages and policies" intro="Customers see a page only after you publish it.">
      {error && <Alert>{error}</Alert>}
      {pages.length > 0 && (
        <ul className="grid gap-2">
          {pages.map((page) => (
            <li
              key={page.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-base border border-line p-3"
            >
              <div>
                <p className="text-sm font-semibold">{page.title}</p>
                <p className="font-mono text-xs text-muted">/pages/{page.slug}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-badge px-2 py-0.5 text-[11px] font-bold uppercase ${page.status === 'published' ? 'bg-success-bg text-success-fg' : 'bg-canvas text-muted'}`}
                >
                  {page.status}
                </span>
                <Button variant="secondary" onClick={() => setEditing(page)}>
                  Edit
                </Button>
                <Button
                  variant="secondary"
                  onClick={() =>
                    void act(() =>
                      api.post(`${BASE}/pages/${page.id}/status/`, {
                        status: page.status === 'published' ? 'draft' : 'published',
                      }),
                    )
                  }
                >
                  {page.status === 'published' ? 'Unpublish' : 'Publish'}
                </Button>
                <Button
                  variant="secondary"
                  aria-label={`Delete ${page.title}`}
                  onClick={() => setConfirming(page)}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div>
        <p className="text-sm font-semibold">Add a page</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {missing.map((template) => (
            <Button key={template.slug} variant="secondary" onClick={() => setEditing(template)}>
              + {template.title}
            </Button>
          ))}
          <Button
            variant="secondary"
            onClick={() => setEditing({ title: '', slug: '', page_type: 'page' })}
          >
            + Blank page
          </Button>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          title="Delete this page?"
          onCancel={() => setConfirming(null)}
          onConfirm={async () => {
            const target = confirming;
            setConfirming(null);
            await act(() => api.delete(`${BASE}/pages/${target.id}/`));
          }}
        >
          “{confirming.title}” (/pages/{confirming.slug}) will be deleted permanently, including its
          translations. This cannot be undone.
        </ConfirmDialog>
      )}
      {editing && (
        <PageEditor
          page={editing}
          translation={translation}
          onClose={() => setEditing(null)}
          onSaved={load}
        />
      )}
    </Panel>
  );
}

export function ContentSettings() {
  const [tab, setTab] = useState<'home' | 'pages'>('home');
  const [translation, setTranslation] = useState<TranslationLanguage | null>(null);
  useEffect(() => {
    void api
      .get<{ languages?: string[]; default_language?: string }>('/api/manage/store/appearance/')
      .then((a) => setTranslation(translationLanguage(a.languages ?? [], a.default_language ?? '')))
      .catch(() => setTranslation(null));
  }, []);
  return (
    <div className="grid gap-6">
      <div>
        <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">Online store</p>
        <h1 className="mt-2 text-[28px] font-bold tracking-tight">Content</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">
          Your homepage sections and store pages. The storefront that shows them arrives in the next
          release.
        </p>
      </div>
      <div role="tablist" aria-label="Content" className="flex gap-1 border-b border-line">
        {(
          [
            ['home', 'Homepage sections'],
            ['pages', 'Pages and policies'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-3 text-sm font-semibold ${tab === key ? 'border-ink text-ink' : 'border-transparent text-muted'}`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'home' ? <Sections /> : <Pages translation={translation} />}
    </div>
  );
}

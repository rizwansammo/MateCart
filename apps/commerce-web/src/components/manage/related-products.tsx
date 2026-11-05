'use client';

/**
 * Related products shown on a product page (D-110): an automatic list from shared categories,
 * collections, brand and tags, with merchant pins and hides. It works without any AI provider.
 */
import { useCallback, useState } from 'react';
import {
  managePage,
  managedProductSummary,
  productRecommendations,
  type ProductRecommendations,
  type RecommendationEntry,
} from '@matecart/shared-contracts';
import { Alert, Button, errorMessage } from '@matecart/shared-ui';
import { Card, api, query, useLoad } from './kit';

const REASON: Record<string, string> = {
  same_primary_category: 'same main category',
  same_category: 'same category',
  same_collection: 'same collection',
  same_brand: 'same brand',
  shared_tags: 'shared tags',
};

export function RelatedProducts({ productId }: { productId: string }) {
  const url = `/api/manage/catalog/products/${productId}/recommendations/`;
  const load = useCallback(async () => productRecommendations(await api.get<unknown>(url)), [url]);
  const { data, setData, error, loading } = useLoad(load);
  const [message, setMessage] = useState('');
  const [search, setSearch] = useState('');
  const [found, setFound] = useState<{ id: string; name: string }[]>([]);

  async function save(next: { pinned: string[]; hidden: string[] }) {
    setMessage('');
    try {
      setData(productRecommendations(await api.put<unknown>(url, next)));
    } catch (e) {
      setMessage(errorMessage(e));
    }
  }
  const ids = (rows: RecommendationEntry[]) => rows.map((row) => row.id);
  const current = (value: ProductRecommendations) => ({
    pinned: ids(value.pinned),
    hidden: ids(value.hidden),
  });

  return (
    <Card title="Related products">
      {loading && !data && <p className="text-sm text-muted">Loading…</p>}
      {(error || message) && <Alert>{error || message}</Alert>}
      {data && (
        <div className="grid gap-4 text-sm">
          <p className="text-xs leading-5 text-muted">
            Shown on the product page. Only published products that can be bought appear.
          </p>
          <div>
            <p className="mb-1 font-semibold">Shown now</p>
            {data.storefront.length ? (
              <ol className="grid gap-1">
                {data.storefront.map((row) => (
                  <li key={row.id}>{row.name}</li>
                ))}
              </ol>
            ) : (
              <p className="text-muted">Nothing related can be shown yet.</p>
            )}
          </div>
          {data.pinned.length > 0 && (
            <EntryList
              title="Always shown first"
              rows={data.pinned}
              action="Unpin"
              onAction={(row) => {
                const next = current(data);
                void save({ ...next, pinned: next.pinned.filter((id) => id !== row.id) });
              }}
            />
          )}
          <EntryList
            title="Suggested automatically"
            rows={data.automatic}
            describe={(row) => row.reasons.map((r) => REASON[r] ?? r).join(', ')}
            action="Hide"
            onAction={(row) => {
              const next = current(data);
              void save({ ...next, hidden: [...next.hidden, row.id] });
            }}
          />
          {data.hidden.length > 0 && (
            <EntryList
              title="Hidden"
              rows={data.hidden}
              action="Show again"
              onAction={(row) => {
                const next = current(data);
                void save({ ...next, hidden: next.hidden.filter((id) => id !== row.id) });
              }}
            />
          )}
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void api
                .get<unknown>(
                  `/api/manage/catalog/products/${query({ q: search.trim(), page_size: 6 })}`,
                )
                .then((value) =>
                  setFound(
                    managePage(managedProductSummary)(value)
                      .results.filter((row) => row.id !== productId)
                      .map((row) => ({ id: row.id, name: row.name })),
                  ),
                )
                .catch((err: unknown) => setMessage(errorMessage(err)));
            }}
          >
            <label className="text-[13px] font-semibold" htmlFor={`pin-${productId}`}>
              Pin a product
            </label>
            <div className="flex gap-2">
              <input
                id={`pin-${productId}`}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-11 min-w-0 flex-1 rounded-base border border-line-strong bg-surface px-3"
              />
              <Button variant="secondary" type="submit">
                Find
              </Button>
            </div>
            {found.map((row) => (
              <button
                key={row.id}
                type="button"
                className="rounded-base border border-line px-3 py-1.5 text-left hover:border-ink"
                onClick={() => {
                  const next = current(data);
                  setFound([]);
                  void save({
                    pinned: [...next.pinned.filter((id) => id !== row.id), row.id],
                    hidden: next.hidden.filter((id) => id !== row.id),
                  });
                }}
              >
                Pin {row.name}
              </button>
            ))}
          </form>
        </div>
      )}
    </Card>
  );
}

function EntryList({
  title,
  rows,
  action,
  onAction,
  describe,
}: {
  title: string;
  rows: RecommendationEntry[];
  action: string;
  onAction: (row: RecommendationEntry) => void;
  describe?: (row: RecommendationEntry) => string;
}) {
  return (
    <div>
      <p className="mb-1 font-semibold">{title}</p>
      {rows.length === 0 ? (
        <p className="text-muted">None.</p>
      ) : (
        <ul className="grid gap-1">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0">
                {row.name}
                {describe && <span className="block text-xs text-muted">{describe(row)}</span>}
              </span>
              <button
                type="button"
                className="shrink-0 text-xs font-semibold underline"
                onClick={() => onAction(row)}
              >
                {action}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

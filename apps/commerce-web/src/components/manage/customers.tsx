'use client';

import { useCallback, useState } from 'react';
import { customerDetail, customerSummary, managePage } from '@matecart/shared-contracts';
import { Button, TextField } from '@matecart/shared-ui';
import {
  Badge,
  CASE_KIND,
  CASE_STATUS,
  Card,
  Cell,
  Detail,
  Empty,
  Feedback,
  Loader,
  ORDER_STATUS,
  PAYMENT_STATUS,
  PageHeader,
  Pagination,
  StatusBadge,
  Table,
  api,
  date,
  dateTime,
  label,
  money,
  query,
  useLoad,
} from './kit';

const PAGE_SIZE = 25;

export function CustomerList() {
  const [text, setText] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      managePage(customerSummary)(
        await api.get<unknown>(
          `/api/manage/customers/${query({ q: search, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [search, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Sell"
        title="Customers"
        intro="People with a customer account in your store. Guest and manual orders keep their contact details on the order only and are never merged into accounts."
      />
      <Feedback error={error} />
      <Card flush>
        <form
          className="flex flex-wrap items-end gap-3 border-b border-line p-4"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setSearch(text.trim());
          }}
        >
          <div className="min-w-0 flex-1">
            <TextField
              label="Search customers"
              placeholder="Name, email or phone"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        {loading && !data ? (
          <Loader>Loading customers…</Loader>
        ) : !data?.results.length ? (
          <Empty title={search ? 'No customer matches' : 'No customer accounts yet'}>
            {search
              ? 'Try a different name, email or phone.'
              : 'Customers appear here when they create an account in your store.'}
          </Empty>
        ) : (
          <>
            <Table head={['Customer', 'Contact', 'Orders', 'Last order', 'Joined', 'Status']}>
              {data.results.map((row) => (
                <tr key={row.id} className="hover:bg-canvas">
                  <Cell>
                    <a
                      className="font-semibold text-brand-deep"
                      href={`/manage/customers/${row.id}`}
                    >
                      {row.full_name || 'Unnamed customer'}
                    </a>
                  </Cell>
                  <Cell>
                    {row.email && <p>{row.email}</p>}
                    {row.phone && <p className="text-xs text-muted">{row.phone}</p>}
                  </Cell>
                  <Cell className="tabular-nums">{row.order_count}</Cell>
                  <Cell className="text-xs">{date(row.last_order_at)}</Cell>
                  <Cell className="text-xs">{date(row.created_at)}</Cell>
                  <Cell>
                    {row.is_active ? (
                      <Badge tone="success">Active</Badge>
                    ) : (
                      <Badge tone="danger">Disabled</Badge>
                    )}
                  </Cell>
                </tr>
              ))}
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}

export function CustomerProfile({ id }: { id: string }) {
  const load = useCallback(
    async () => customerDetail(await api.get<unknown>(`/api/manage/customers/${id}/`)),
    [id],
  );
  const { data, error, loading } = useLoad(load);
  if (loading && !data) return <Loader>Loading customer…</Loader>;
  if (!data) return <Feedback error={error || 'This customer could not be loaded.'} />;
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Customers"
        title={data.full_name || 'Unnamed customer'}
        intro={`Customer since ${date(data.created_at)}`}
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Card title="Contact">
          <dl className="grid gap-3">
            <Detail label="Email">
              {data.email ?? '—'}{' '}
              {data.email &&
                (data.email_verified ? (
                  <Badge tone="success">Verified</Badge>
                ) : (
                  <Badge>Not verified</Badge>
                ))}
            </Detail>
            <Detail label="Phone">
              {data.phone ?? '—'}{' '}
              {data.phone &&
                (data.phone_verified ? (
                  <Badge tone="success">Verified</Badge>
                ) : (
                  <Badge>Not verified</Badge>
                ))}
            </Detail>
            <Detail label="Account">{data.is_active ? 'Active' : 'Disabled'}</Detail>
            <Detail label="Orders">{data.order_count}</Detail>
          </dl>
          <p className="mt-4 text-xs leading-5 text-muted">
            Sign-in, passwords and security settings belong to the customer and are not shown to
            staff.
          </p>
        </Card>
        <div className="grid content-start gap-6">
          <Card title="Orders" flush>
            {!data.orders.length ? (
              <Empty title="No orders yet" />
            ) : (
              <Table head={['Order', 'Placed', 'Total', 'Payment', 'Status']}>
                {data.orders.map((o) => (
                  <tr key={o.id}>
                    <Cell>
                      <a className="font-semibold text-brand-deep" href={`/manage/orders/${o.id}`}>
                        {o.number}
                      </a>
                    </Cell>
                    <Cell className="text-xs">{dateTime(o.placed_at)}</Cell>
                    <Cell className="tabular-nums">{money(o.grand_total, o.currency)}</Cell>
                    <Cell>
                      <StatusBadge value={o.payment_status} map={PAYMENT_STATUS} />
                    </Cell>
                    <Cell>
                      <StatusBadge value={o.status} map={ORDER_STATUS} />
                    </Cell>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
          <Card title="Requests">
            {!data.cases.length ? (
              <p className="text-sm text-muted">
                No returns, exchanges, warranty or support requests.
              </p>
            ) : (
              <ul className="grid gap-2 text-sm">
                {data.cases.map((c) => (
                  <li key={c.id}>
                    <a
                      className="font-semibold text-brand-deep"
                      href={`/manage/aftersales?case=${c.id}`}
                    >
                      {label(CASE_KIND, c.kind)}: {c.subject}
                    </a>
                    <span className="block text-xs text-muted">
                      {label(CASE_STATUS, c.status)} · {date(c.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

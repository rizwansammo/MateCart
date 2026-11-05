'use client';

import { useCallback, useState } from 'react';
import type { StaffSession } from '@matecart/shared-contracts';
import { inboxAnalytics } from '@matecart/shared-contracts';
import { Card, Empty, Feedback, Loader, PageHeader, Stat, api, money, useLoad } from './kit';

const CHANNEL: Record<string, string> = {
  website: 'Website chat',
  messenger: 'Messenger',
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
};

function duration(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/** Operational inbox figures from stored conversations (D-098). Not a financial report. */
export function InboxAnalyticsView({ session }: { session: StaffSession }) {
  const [days, setDays] = useState(30);
  const load = useCallback(
    async () => inboxAnalytics(await api.get<unknown>(`/api/manage/inbox/analytics/?days=${days}`)),
    [days],
  );
  const { data, error, loading } = useLoad(load);
  const canValue = session.permissions.includes('reports.view');
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Inbox"
        title="Conversation insights"
        intro="Counts come from stored conversations in your store's time zone. Response time is measured from the customer's first message to the first reply written by your team."
        actions={
          <>
            <a
              href="/manage/inbox"
              className="self-center text-sm font-semibold text-brand-deep hover:underline"
            >
              Back to inbox
            </a>
            <select
              aria-label="Period"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="h-10 rounded-base border border-line-strong bg-surface px-3 text-sm"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </>
        }
      />
      <Feedback error={error} />
      {loading && !data ? (
        <Loader />
      ) : data ? (
        <>
          <p className="text-xs text-muted">
            {data.start} to {data.end} · {data.timezone}
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="New conversations"
              value={data.new_conversations}
              hint={`${data.unique_participants} people`}
            />
            <Stat
              label="Messages"
              value={data.messages_received}
              hint={`${data.messages_sent} replies sent by your team`}
            />
            <Stat
              label="Median first response"
              value={duration(data.median_first_response_seconds)}
              hint={`Average ${duration(data.average_first_response_seconds)}`}
            />
            <Stat
              label="Resolved"
              value={data.resolved_conversations}
              hint={`Average time ${duration(data.average_resolution_seconds)}`}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Open now" value={data.open_now} href="/manage/inbox" />
            <Stat label="Pending now" value={data.pending_now} />
            <Stat
              label="Unassigned now"
              value={data.unassigned_now}
              tone={data.unassigned_now ? 'danger' : undefined}
            />
            <Stat label="Unread by you" value={data.unread_now} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Conversations by channel">
              {data.by_channel.length ? (
                <ul className="grid gap-2 text-sm">
                  {data.by_channel.map((row) => (
                    <li key={row.channel} className="flex justify-between">
                      <span>{CHANNEL[row.channel] ?? row.channel}</span>
                      <span className="font-semibold tabular-nums">{row.conversations}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty title="No conversations in this period" />
              )}
            </Card>
            <Card title="Orders linked to conversations">
              <p className="text-2xl font-bold tabular-nums">{data.orders_linked}</p>
              {canValue && data.linked_order_value !== null && (
                <p className="mt-1 text-sm text-muted">
                  Paid value:{' '}
                  {data.linked_order_currency
                    ? money(data.linked_order_value, data.linked_order_currency)
                    : data.linked_order_value}
                </p>
              )}
              <p className="mt-3 text-xs leading-5 text-muted">
                A linked order shows the conversation and the order belong together. It does not
                prove the conversation caused the sale. Revenue figures live in Reports.
              </p>
            </Card>
          </div>
          <Card title="Team activity" flush>
            {data.agents.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted uppercase">
                      <th className="px-5 py-2.5 font-semibold">Team member</th>
                      <th className="px-5 py-2.5 font-semibold">Assigned, active</th>
                      <th className="px-5 py-2.5 font-semibold">Resolved</th>
                      <th className="px-5 py-2.5 font-semibold">Replies</th>
                      <th className="px-5 py-2.5 font-semibold">Median first response</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.agents.map((agent) => (
                      <tr key={agent.id} className="border-b border-line last:border-0">
                        <td className="px-5 py-2.5">{agent.name}</td>
                        <td className="px-5 py-2.5 tabular-nums">{agent.assigned_open}</td>
                        <td className="px-5 py-2.5 tabular-nums">{agent.resolved}</td>
                        <td className="px-5 py-2.5 tabular-nums">{agent.replies}</td>
                        <td className="px-5 py-2.5">
                          {duration(agent.median_first_response_seconds)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="No team members can answer messages yet" />
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}

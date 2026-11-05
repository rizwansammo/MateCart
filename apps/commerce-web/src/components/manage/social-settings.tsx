'use client';

import { useCallback, useEffect, useState } from 'react';
import type {
  ChannelCard,
  LiveChatSettings,
  SocialSettings as Settings,
} from '@matecart/shared-contracts';
import {
  accountSelection,
  authorizeUrl,
  channelCard,
  socialSettings,
} from '@matecart/shared-contracts';
import { Alert, ApiError, Button, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import { CHANNEL_LABEL } from './inbox';
import {
  Badge,
  Card,
  Feedback,
  Loader,
  PageHeader,
  ReauthDialog,
  SelectField,
  api,
  dateTime,
} from './kit';

const DAYS: [string, string][] = [
  ['sat', 'Saturday'],
  ['sun', 'Sunday'],
  ['mon', 'Monday'],
  ['tue', 'Tuesday'],
  ['wed', 'Wednesday'],
  ['thu', 'Thursday'],
  ['fri', 'Friday'],
];
const STATE: Record<
  string,
  { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }
> = {
  not_configured: { label: 'Not connected', tone: 'neutral' },
  configuration_required: { label: 'Configuration required', tone: 'warning' },
  pending: { label: 'Authorization pending', tone: 'info' },
  connected: { label: 'Connected', tone: 'success' },
  permission_missing: { label: 'Permission missing', tone: 'danger' },
  revoked: { label: 'Revoked', tone: 'danger' },
  suspended: { label: 'Suspended by Meta', tone: 'danger' },
  error: { label: 'Failed', tone: 'danger' },
  disconnected: { label: 'Disconnected', tone: 'neutral' },
};
const RESULTS: Record<string, string> = {
  invalid_state: 'That authorization could not be matched to your session. Start connecting again.',
  cancelled: 'The authorization was cancelled in Meta.',
  permission_missing:
    'Meta did not grant every messaging permission. Start again and allow all of them.',
  failed: 'Meta did not complete the authorization. Try again in a moment.',
  no_accounts: 'Meta did not list any account you can manage for this channel.',
};

type Pending = { title: string; description: string; run: () => Promise<void> };

/** Website chat, Messenger, WhatsApp and Instagram for this store (Phase 14). */
export function SocialSettings() {
  const [data, setData] = useState<Settings | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [selection, setSelection] = useState<{
    token: string;
    channel: string;
    accounts: { id: string; name: string; identifier: string }[];
  } | null>(null);

  const load = useCallback(async () => {
    try {
      setData(socialSettings(await api.get<unknown>('/api/manage/communications/settings/')));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  // Returning from Meta: either a result message or a list of accounts to choose from.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get('result');
    const token = params.get('select');
    if (result)
      void Promise.resolve().then(() => setError(RESULTS[result] ?? RESULTS.failed ?? ''));
    if (token) {
      void api
        .get<unknown>(
          `/api/manage/communications/connections/selection/?selection=${encodeURIComponent(token)}`,
        )
        .then((value) => {
          const found = accountSelection(value);
          setSelection({ token, ...found });
        })
        .catch((e: unknown) => setError(errorMessage(e)));
    }
    if (result || token) window.history.replaceState(null, '', '/manage/settings/social');
  }, []);

  /** Runs a sensitive change, asking for the password first when the server requires it. */
  async function sensitive(title: string, description: string, run: () => Promise<void>) {
    setError('');
    try {
      await run();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'reauthentication_required') {
        setPending({ title, description, run });
      } else {
        setError(errorMessage(e));
      }
    }
  }

  async function plain(run: () => Promise<void>, done: string) {
    setError('');
    setNotice('');
    try {
      await run();
      setNotice(done);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!data) return error ? <Alert>{error}</Alert> : <Loader />;
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Settings"
        title="Live chat and social channels"
        intro="Answer website chat, Facebook Messenger, WhatsApp and Instagram messages from one inbox. Every channel uses its official, authorised connection; MateCart never logs in with personal passwords."
        actions={
          <a href="/manage/inbox" className="text-sm font-semibold text-brand-deep hover:underline">
            Open inbox
          </a>
        }
      />
      <Feedback error={error} notice={notice} />
      <LiveChatCard data={data} onSaved={load} onMessage={setNotice} onError={setError} />
      <div className="grid gap-4 lg:grid-cols-3">
        {data.connections.map((card) => (
          <ChannelPanel
            key={card.channel}
            card={card}
            data={data}
            onStart={() =>
              sensitive(
                `Connect ${CHANNEL_LABEL[card.channel]}`,
                'Connecting a business account changes who can message your customers. Confirm your password.',
                async () => {
                  const url = authorizeUrl(
                    await api.post<unknown>('/api/manage/communications/connections/start/', {
                      channel: card.channel,
                    }),
                  );
                  window.location.assign(url);
                },
              )
            }
            onSimulate={() =>
              plain(async () => {
                await api.post('/api/manage/communications/simulator/', { channel: card.channel });
              }, 'Development simulator connected. Nothing reaches Meta.')
            }
            onVerify={() =>
              plain(async () => {
                channelCard(
                  await api.post<unknown>(
                    `/api/manage/communications/connections/${card.id}/verify/`,
                  ),
                );
              }, 'Connection checked with Meta.')
            }
            onToggle={() =>
              plain(
                async () => {
                  await api.post(`/api/manage/communications/connections/${card.id}/enabled/`, {
                    enabled: !card.is_enabled,
                  });
                },
                card.is_enabled
                  ? 'Channel switched off. No messages will be sent.'
                  : 'Channel switched on.',
              )
            }
            onSync={() =>
              plain(async () => {
                await api.post(`/api/manage/communications/connections/${card.id}/templates/sync/`);
              }, 'Templates updated from Meta.')
            }
            onDisconnect={() =>
              sensitive(
                `Disconnect ${CHANNEL_LABEL[card.channel]}`,
                'Sending stops immediately and the stored access token is erased. Conversation history stays. Confirm your password.',
                async () => {
                  await api.post(`/api/manage/communications/connections/${card.id}/disconnect/`);
                  setNotice('Disconnected. The access token was erased.');
                  await load();
                },
              )
            }
          />
        ))}
      </div>
      <Card title="Requirements set up by NetaMate">
        <ul className="grid gap-2 text-sm leading-6 text-muted">
          <li>
            Webhook address for the store&apos;s Meta app:{' '}
            <code className="text-ink">{data.webhook_url}</code>
          </li>
          <li>
            Messenger and Instagram replies are possible for 24 hours after the customer&apos;s last
            message
            {data.human_agent_approved
              ? ', and up to 7 days as a human agent reply (approved for this app).'
              : '. Longer human agent replies need Meta to approve the app first.'}
          </li>
          <li>
            WhatsApp free text replies are possible inside the 24-hour service window. After that,
            only Meta-approved utility templates, for customers whose agreement you recorded.
          </li>
          <li>
            {data.product_links_available
              ? 'Product links in messages use your store’s public address.'
              : 'Product links are sent only once the store has its public https address.'}
          </li>
        </ul>
      </Card>
      {selection && (
        <Dialog
          title={`Choose the ${CHANNEL_LABEL[selection.channel as keyof typeof CHANNEL_LABEL] ?? ''} account`}
          onClose={() => setSelection(null)}
        >
          <div className="grid gap-3">
            <p className="text-sm text-muted">These are the accounts Meta says you manage.</p>
            <ul className="grid gap-2">
              {selection.accounts.map((account) => (
                <li
                  key={account.id}
                  className="flex items-center justify-between gap-3 rounded-base border border-line p-3 text-sm"
                >
                  <span>
                    <span className="block font-semibold">{account.name || account.id}</span>
                    <span className="text-xs text-muted">{account.identifier || account.id}</span>
                  </span>
                  <Button
                    type="button"
                    onClick={() =>
                      void sensitive(
                        'Connect this account',
                        'Confirm your password to connect this account.',
                        async () => {
                          await api.post('/api/manage/communications/connections/select/', {
                            selection: selection.token,
                            account: account.id,
                          });
                          setSelection(null);
                          setNotice('Connected. New messages will appear in the inbox.');
                          await load();
                        },
                      )
                    }
                  >
                    Connect
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        </Dialog>
      )}
      {pending && (
        <ReauthDialog
          title={pending.title}
          description={pending.description}
          onClose={() => setPending(null)}
          onConfirm={async () => {
            const run = pending.run;
            setPending(null);
            await sensitive(pending.title, pending.description, run);
          }}
        />
      )}
    </div>
  );
}

function ChannelPanel({
  card,
  data,
  onStart,
  onSimulate,
  onVerify,
  onToggle,
  onSync,
  onDisconnect,
}: {
  card: ChannelCard;
  data: Settings;
  onStart: () => void;
  onSimulate: () => void;
  onVerify: () => void;
  onToggle: () => void;
  onSync: () => void;
  onDisconnect: () => void;
}) {
  const state = STATE[card.state] ?? { label: card.state, tone: 'neutral' as const };
  const live = card.id !== null && card.state !== 'disconnected';
  return (
    <Card
      title={CHANNEL_LABEL[card.channel]}
      actions={
        <span className="flex gap-1.5">
          {card.is_simulated && <Badge tone="warning">Simulator</Badge>}
          <Badge tone={state.tone}>{state.label}</Badge>
        </span>
      }
    >
      <div className="grid gap-3 text-sm">
        {live ? (
          <dl className="grid gap-1.5">
            <div>
              <dt className="text-xs text-muted">Account</dt>
              <dd className="font-semibold">
                {card.display_name || card.account_id}
                {card.display_identifier ? ` · ${card.display_identifier}` : ''}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Account id</dt>
              <dd className="font-mono text-xs">{card.account_id}</dd>
            </div>
            {card.last_verified_at && (
              <div>
                <dt className="text-xs text-muted">Last checked</dt>
                <dd>{dateTime(card.last_verified_at)}</dd>
              </div>
            )}
            {card.last_error && (
              <div>
                <dt className="text-xs text-muted">Last error</dt>
                <dd className="text-danger-fg">{card.last_error}</dd>
              </div>
            )}
            <div>
              <dt className="text-xs text-muted">Sending</dt>
              <dd>{card.is_enabled ? 'On' : 'Off (incoming messages are still recorded)'}</dd>
            </div>
            {card.channel === 'whatsapp' && (
              <div>
                <dt className="text-xs text-muted">Synchronised templates</dt>
                <dd>{card.templates}</dd>
              </div>
            )}
          </dl>
        ) : (
          <ul className="grid list-disc gap-1 pl-4 text-xs leading-5 text-muted">
            {card.requirements.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        {card.is_simulated && (
          <p className="text-xs leading-5 text-warning-fg">
            Development simulator. Messages stay on this computer; use it only for local testing.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {!live && (
            <Button
              type="button"
              disabled={card.state === 'configuration_required'}
              onClick={onStart}
            >
              Connect
            </Button>
          )}
          {!live && data.simulator_available && (
            <Button type="button" variant="secondary" onClick={onSimulate}>
              Use simulator
            </Button>
          )}
          {live &&
            ['revoked', 'permission_missing', 'error'].includes(card.state) &&
            !card.is_simulated && (
              <Button type="button" onClick={onStart}>
                Reconnect
              </Button>
            )}
          {live && (
            <>
              <Button type="button" variant="secondary" onClick={onVerify}>
                Check connection
              </Button>
              <Button type="button" variant="secondary" onClick={onToggle}>
                {card.is_enabled ? 'Switch off' : 'Switch on'}
              </Button>
              {card.channel === 'whatsapp' && (
                <Button type="button" variant="secondary" onClick={onSync}>
                  Sync templates
                </Button>
              )}
              <Button type="button" variant="danger" onClick={onDisconnect}>
                Disconnect
              </Button>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

function LiveChatCard({
  data,
  onSaved,
  onMessage,
  onError,
}: {
  data: Settings;
  onSaved: () => Promise<void>;
  onMessage: (text: string) => void;
  onError: (text: string) => void;
}) {
  const [form, setForm] = useState<LiveChatSettings>(data.live_chat);
  const [busy, setBusy] = useState(false);
  const set = (name: keyof LiveChatSettings) => (e: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: e.target.value }));
  const always = Object.keys(form.hours).length === 0;

  async function toggle() {
    setBusy(true);
    try {
      await api.put('/api/manage/store/modules/live_chat/', { enabled: !data.live_chat_enabled });
      onMessage(
        data.live_chat_enabled
          ? 'Live chat switched off. The chat button is removed.'
          : 'Live chat switched on.',
      );
      await onSaved();
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    try {
      await api.put('/api/manage/communications/live-chat/', form);
      onMessage('Live chat settings saved.');
      await onSaved();
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function setRange(day: string, index: 0 | 1, value: string) {
    setForm((current) => {
      const range = current.hours[day]?.[0] ?? ['09:00', '18:00'];
      const next: [string, string] = index === 0 ? [value, range[1]] : [range[0], value];
      return { ...current, hours: { ...current.hours, [day]: [next] } };
    });
  }
  function setDay(day: string, open: boolean) {
    setForm((current) => {
      const hours = { ...current.hours };
      if (open) hours[day] = [['09:00', '18:00']];
      else delete hours[day];
      return { ...current, hours };
    });
  }

  return (
    <Card
      title="Website live chat"
      actions={
        <span className="flex items-center gap-2">
          <Badge tone={data.live_chat_enabled ? 'success' : 'neutral'}>
            {data.live_chat_enabled ? 'On' : 'Off'}
          </Badge>
          <Button
            type="button"
            variant="secondary"
            busy={busy}
            onClick={() => void toggle()}
            className="min-h-9"
          >
            {data.live_chat_enabled ? 'Switch off' : 'Switch on'}
          </Button>
        </span>
      }
    >
      <form
        className="grid gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <p className="text-sm leading-6 text-muted">
          MateCart&apos;s own chat, shown on every store page except checkout. Visitors appear
          online to your team only while someone has the inbox open
          {data.live_chat_enabled
            ? data.agents_online
              ? ' (someone has it open now)'
              : ' (nobody has it open right now)'
            : ''}
          .
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          <TextArea
            label="Welcome message (English)"
            maxLength={300}
            value={form.welcome_en}
            onChange={set('welcome_en')}
          />
          <TextArea
            label="Welcome message (Bangla)"
            maxLength={300}
            value={form.welcome_bn}
            onChange={set('welcome_bn')}
          />
          <TextArea
            label="Away message (English)"
            maxLength={300}
            value={form.away_en}
            onChange={set('away_en')}
          />
          <TextArea
            label="Away message (Bangla)"
            maxLength={300}
            value={form.away_bn}
            onChange={set('away_bn')}
          />
          <TextField
            label="Button label (English)"
            maxLength={30}
            value={form.button_label_en}
            onChange={set('button_label_en')}
          />
          <TextField
            label="Button label (Bangla)"
            maxLength={30}
            value={form.button_label_bn}
            onChange={set('button_label_bn')}
          />
          <SelectField
            label="Chat languages"
            value={form.languages}
            onChange={set('languages')}
            options={[
              { value: 'en,bn', label: 'English and Bangla' },
              { value: 'en', label: 'English' },
              { value: 'bn', label: 'Bangla' },
            ]}
          />
          <SelectField
            label="Button position"
            value={form.position}
            onChange={set('position')}
            options={[
              { value: 'right', label: 'Bottom right' },
              { value: 'left', label: 'Bottom left' },
            ]}
          />
        </div>
        <fieldset className="grid gap-3">
          <legend className="text-[13px] font-semibold">Opening hours ({data.timezone})</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={always}
              onChange={(e) =>
                setForm((current) => ({
                  ...current,
                  hours: e.target.checked ? {} : { sat: [['09:00', '18:00']] },
                }))
              }
            />
            No set hours (online whenever someone has the inbox open)
          </label>
          {!always && (
            <div className="grid gap-2">
              {DAYS.map(([day, name]) => {
                const range = form.hours[day]?.[0];
                return (
                  <div key={day} className="flex flex-wrap items-center gap-3 text-sm">
                    <label className="flex w-32 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={Boolean(range)}
                        onChange={(e) => setDay(day, e.target.checked)}
                      />
                      {name}
                    </label>
                    {range ? (
                      <>
                        <input
                          type="time"
                          aria-label={`${name} opens`}
                          value={range[0]}
                          onChange={(e) => setRange(day, 0, e.target.value)}
                          className="h-9 rounded-base border border-line-strong px-2"
                        />
                        <span className="text-muted">to</span>
                        <input
                          type="time"
                          aria-label={`${name} closes`}
                          value={range[1]}
                          onChange={(e) => setRange(day, 1, e.target.value)}
                          className="h-9 rounded-base border border-line-strong px-2"
                        />
                      </>
                    ) : (
                      <span className="text-muted">Closed</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </fieldset>
        <div>
          <Button type="submit" busy={busy}>
            Save live chat settings
          </Button>
        </div>
      </form>
    </Card>
  );
}

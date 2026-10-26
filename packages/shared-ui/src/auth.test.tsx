import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, errorMessage } from './api';
import { CopyButton, Dialog, OTPInput, PasswordField, ResendCode } from './auth-controls';
import { ActivationForm, EmployeeSignIn, PasswordRecovery } from './auth-pages';
import { MfaChallengeForm, RecoveryCodesPanel, TotpSetupWizard } from './mfa-ui';
import type { ApiClient } from './api';

function client(): ApiClient {
  return {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    upload: vi.fn(),
  };
}
afterEach(() => vi.useRealTimers());

describe('accessible sign-in controls', () => {
  it('supports pasting and sanitizing a six-digit code', async () => {
    function Test() {
      const [code, setCode] = useState('');
      return <OTPInput value={code} onChange={setCode} />;
    }
    render(<Test />);
    const input = screen.getByLabelText('Six-digit code');
    fireEvent.paste(input, { clipboardData: { getData: () => '12 34-56' } });
    expect(input).toHaveValue('123456');
    expect(input).toHaveAttribute('autocomplete', 'one-time-code');
    fireEvent.change(input, { target: { value: 'a12b' } });
    expect(input).toHaveValue('12');
  });
  it('hides passwords by default and reveals only on request', async () => {
    render(<PasswordField label="Password" />);
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    await userEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
  });
  it('copies only after a deliberate action and confirms the result', async () => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    render(<CopyButton text="test-secret">Copy key</CopyButton>);
    expect(clipboard).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Copy key' }));
    expect(clipboard).toHaveBeenCalledWith('test-secret');
    expect(screen.getByRole('status')).toHaveTextContent('Copied');
  });
  it('enforces the resend cooldown again after a successful resend', async () => {
    vi.useFakeTimers();
    const resend = vi.fn().mockResolvedValue(undefined);
    render(<ResendCode seconds={2} onResend={resend} />);
    expect(screen.getByRole('button')).toBeDisabled();
    await act(async () => {
      vi.advanceTimersByTime(2100);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Resend code' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(resend).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button')).toBeDisabled();
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole('button')).toBeDisabled();
  });
  it('uses a labelled modal and handles the escape cancellation', () => {
    const close = vi.fn();
    vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function (
      this: HTMLDialogElement,
    ) {
      this.open = true;
    });
    vi.spyOn(HTMLDialogElement.prototype, 'close').mockImplementation(function (
      this: HTMLDialogElement,
    ) {
      this.open = false;
    });
    render(
      <Dialog title="Confirm change" onClose={close}>
        Your confirmation
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Confirm change' });
    fireEvent(dialog, new Event('cancel', { bubbles: true, cancelable: true }));
    expect(close).toHaveBeenCalled();
  });
});

describe('authenticator and recovery setup', () => {
  const setup = {
    qr_svg: 'data:image/svg+xml;base64,PHN2Zy8+',
    secret: 'ABCD EFGH',
    uri: 'otpauth://totp/test',
  };
  it('shows QR by default, hides the key, verifies before enabling, and requires saved-code confirmation', async () => {
    const user = userEvent.setup();
    const begin = vi.fn().mockResolvedValue(setup);
    const confirm = vi.fn().mockResolvedValue(['ABCDE-FG234']);
    const done = vi.fn();
    render(<TotpSetupWizard begin={begin} confirm={confirm} onDone={done} />);
    expect(begin).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /I have an app/ }));
    expect(
      await screen.findByAltText('Scan this QR code with your authenticator app'),
    ).toBeVisible();
    expect(screen.queryByText('ABCD EFGH')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: "Can't scan? View secret key" }));
    expect(screen.getByText('ABCD EFGH')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'I have scanned the code' }));
    expect(screen.queryByText('ABCD EFGH')).not.toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('Six-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Verify and turn on' }));
    expect(confirm).toHaveBeenCalledWith('123456');
    expect(await screen.findByText('ABCDE-FG234')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Finish setup' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Finish setup' }));
    expect(done).toHaveBeenCalledTimes(1);
  });
  it('keeps the verification step open on an incorrect code', async () => {
    const done = vi.fn();
    const user = userEvent.setup();
    render(
      <TotpSetupWizard
        begin={async () => setup}
        confirm={async () => {
          throw new ApiError(400, 'invalid_code', 'Please use the newest code.');
        }}
        onDone={done}
      />,
    );
    await user.click(screen.getByRole('button', { name: /I have an app/ }));
    await user.click(await screen.findByRole('button', { name: 'I have scanned the code' }));
    await user.type(screen.getByLabelText('Six-digit code'), '000000');
    await user.click(screen.getByRole('button', { name: 'Verify and turn on' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Please use the newest code.');
    expect(done).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Six-digit code')).toHaveValue('');
  });
  it('offers download and copy controls without persisting recovery codes', () => {
    render(<RecoveryCodesPanel codes={['ABCDE-FG234']} onDone={() => undefined} />);
    expect(screen.getByRole('button', { name: 'Copy all' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Download codes' })).toBeEnabled();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe('real form behaviours', () => {
  it('inspects an invitation with a POST body so its token never becomes an API URL', async () => {
    history.replaceState(null, '', '/manage/invitation?token=test-invitation-token');
    const api = client();
    vi.mocked(api.post).mockResolvedValue({
      email: 'member@example.test',
      role_label: 'Manager',
      role_description: 'Manage the store',
      store_name: 'Review store',
    });
    const page = render(<ActivationForm api={api} realm="staff" />);
    expect(await screen.findByLabelText('Your full name')).toBeVisible();
    expect(api.post).toHaveBeenCalledWith('/api/manage/invitation/inspect/', {
      token: 'test-invitation-token',
    });
    expect(api.get).not.toHaveBeenCalled();
    page.unmount();
    history.replaceState(null, '', '/');
  });
  it('displays safe sign-in errors without showing an authenticated shell', async () => {
    const api = client();
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(401, 'invalid_credentials', 'The email or password is not right.'),
    );
    render(<EmployeeSignIn api={api} realm="staff" />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email address'), 'owner@example.com');
    await user.type(screen.getByLabelText('Password'), 'incorrect');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The email or password is not right.',
    );
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(screen.queryByText('Team & access')).not.toBeInTheDocument();
  });
  it('uses the backend pending session when sending and verifying an email factor', async () => {
    const api = client();
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        challenge_id: 'test-id',
        destination: 'o••@example.com',
        resend_in: 60,
        expires_in: 600,
      })
      .mockResolvedValueOnce({ status: 'signed_in' });
    const done = vi.fn();
    render(<MfaChallengeForm api={api} base="/api/manage/auth" onSuccess={done} />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Verification method'), 'email');
    await user.click(screen.getByRole('button', { name: 'Email me a code' }));
    expect(await screen.findByText(/o••@example.com/)).toBeVisible();
    await user.type(screen.getByLabelText('Six-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Verify and sign in' }));
    expect(api.post).toHaveBeenLastCalledWith('/api/manage/auth/mfa/verify/', {
      method: 'email',
      code: '123456',
      challenge_id: 'test-id',
    });
    expect(done).toHaveBeenCalled();
  });
  it('shows the same password recovery confirmation for an unknown account', async () => {
    const api = client();
    vi.mocked(api.post).mockResolvedValue(null);
    render(<PasswordRecovery api={api} base="/api/account" loginPath="/account/sign-in" />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email address'), 'unknown@example.com');
    await user.click(screen.getByRole('button', { name: 'Send reset link' }));
    expect(await screen.findByText('Check your inbox')).toBeVisible();
    expect(screen.getByText(/If this email has an account/)).toBeVisible();
  });
  it('handles an incomplete invitation without presenting account activation', async () => {
    const api = client();
    render(<ActivationForm api={api} realm="staff" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('incomplete');
    expect(api.get).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Activate account' })).not.toBeInTheDocument();
  });
});

describe('same-origin CSRF client', () => {
  it('bootstraps CSRF, forwards it, and keeps cookies first-party', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ csrf_token: 'csrf-test' }))
      .mockResolvedValueOnce(Response.json({ ok: true }));
    const api = createApiClient({ csrfCookie: 'test_csrf', fetchImpl: fetcher });
    await api.post('/api/account/login/', { email: 'test@example.com' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]?.[1]).toMatchObject({
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': 'csrf-test' },
      cache: 'no-store',
    });
  });
  it('never displays HTML error output', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('<html>internal trace</html>', { status: 500 }));
    const api = createApiClient({ csrfCookie: 'test_csrf', fetchImpl: fetcher });
    await expect(api.get('/api/account/me/')).rejects.toThrow('Something went wrong on our side.');
    expect(errorMessage(new Error('secret'))).not.toContain('secret');
  });
  it('retries a rejected CSRF token once, then fails instead of looping', async () => {
    document.cookie = 'test_csrf=expired';
    const reject = () =>
      Response.json(
        { error: { code: 'csrf_failed', message: 'Refresh and try again.' } },
        { status: 403 },
      );
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(reject())
      .mockResolvedValueOnce(Response.json({ csrf_token: 'new' }))
      .mockResolvedValueOnce(reject());
    const api = createApiClient({ csrfCookie: 'test_csrf', fetchImpl: fetcher });
    await expect(api.post('/api/account/login/')).rejects.toThrow('Refresh and try again.');
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

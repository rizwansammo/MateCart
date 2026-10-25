// Deliberate local verification: creates labelled review accounts in matecart-dev.
// Credentials, email bodies, OTPs and recovery codes stay in process memory.
import assert from 'node:assert/strict';
import { randomBytes, createHmac } from 'node:crypto';
import { execFileSync } from 'node:child_process';

if (!process.argv.includes('--local')) throw new Error('Pass --local for the MateCart dev stack.');
const WEB = 'http://127.0.0.1:3400';
const CONSOLE = 'http://127.0.0.1:3410';
const MAIL = 'http://127.0.0.1:18025';
const id = Date.now().toString(36);
const password = `Review-${randomBytes(24).toString('base64url')}!9`;
let passed = 0;
function pass(label) {
  passed++;
  console.log(`PASS ${label}`);
}
class Browser {
  constructor(origin) {
    this.origin = origin;
    this.cookies = new Map();
    this.csrf = '';
  }
  async request(path, body, expected = 200, csrf = true) {
    const method = body === undefined ? 'GET' : 'POST';
    const cookie = [...this.cookies.values()]
      .filter((c) => path.startsWith(c.path))
      .map((c) => `${c.name}=${c.value}`)
      .join('; ');
    const headers = { Origin: this.origin, Cookie: cookie, 'Content-Type': 'application/json' };
    if (method === 'POST' && csrf) headers['X-CSRFToken'] = this.csrf;
    const res = await fetch(this.origin + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'manual',
    });
    for (const raw of res.headers.getSetCookie()) {
      const parts = raw.split(';').map((p) => p.trim());
      const [name, ...value] = parts[0].split('=');
      const entry = {
        name,
        value: value.join('='),
        path: parts.find((p) => p.startsWith('Path='))?.slice(5) ?? '/',
        raw,
      };
      if (parts.includes('Max-Age=0')) this.cookies.delete(name);
      else this.cookies.set(name, entry);
    }
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    assert.equal(
      res.status,
      expected,
      `${path}: expected ${expected}, received ${res.status} (${data?.error?.code ?? 'no error code'})`,
    );
    return data;
  }
  async ready() {
    this.csrf = (await this.request('/api/csrf/')).csrf_token;
    return this;
  }
}
async function email(address) {
  for (let i = 0; i < 8; i++) {
    const results = await (
      await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent('to:' + address)}`)
    ).json();
    if (results.messages?.length) {
      const message = await (
        await fetch(`${MAIL}/api/v1/message/${results.messages[0].ID}`)
      ).json();
      return message.Text;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Local testing mailbox did not receive a message');
}
async function code(address) {
  const match = /\b(\d{6})\b/.exec(await email(address));
  assert.ok(match, 'Expected an emailed verification code');
  return match[1];
}
async function token(address) {
  const match = /[?&]token=([^\s<]+)/.exec(await email(address));
  assert.ok(match, 'Expected an emailed action link');
  return decodeURIComponent(match[1]);
}
function command(service, ...args) {
  // Capture output: provisioning links/passwords must never enter the transcript.
  return execFileSync(
    'docker',
    ['compose', 'exec', '-T', service, 'python', 'manage.py', ...args],
    { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] },
  );
}
function totp(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of secret.replaceAll(' ', ''))
    bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac('sha1', Buffer.from(bytes)).update(counter).digest();
  const offset = hash[19] & 15;
  return ((hash.readUInt32BE(offset) & 0x7fffffff) % 1000000).toString().padStart(6, '0');
}
async function run() {
  const customer = await new Browser(WEB).ready();
  const address = `review-customer-${id}@example.test`;
  const methods = await customer.request('/api/account/methods/');
  assert.equal(methods.phone, false);
  assert.equal(methods.google, false);
  pass('Unconfigured live SMS and Google are disabled');
  await customer.request('/api/account/login/', { email: address, password }, 403, false);
  pass('Same-origin POST still requires CSRF');
  const registration = await customer.request(
    '/api/account/register/',
    { full_name: 'Local Review Customer', email: address, password },
    202,
  );
  const profile = await customer.request(
    '/api/account/register/verify/',
    { challenge_id: registration.challenge_id, code: await code(address) },
    201,
  );
  assert.equal(profile.email_verified, true);
  const cookie = customer.cookies.get('mc_customer');
  assert.ok(cookie.raw.includes('HttpOnly'));
  assert.equal(cookie.path, '/');
  pass('Customer registration, Mailpit verification and HttpOnly session through Next.js');
  await customer.request('/api/manage/auth/session/', undefined, 401);
  pass('Customer session cannot authorize the merchant realm');
  await customer.request('/api/account/logout/', {}, 204);
  await customer.request('/api/account/me/', undefined, 401);
  await customer.request('/api/account/login/', { email: address, password });
  await customer.request('/api/account/password/forgot/', { email: address }, 202);
  const replacement = `Reset-${randomBytes(24).toString('base64url')}!8`;
  await customer.request(
    '/api/account/password/reset/',
    { token: await token(address), password: replacement },
    204,
  );
  await customer.request('/api/account/me/', undefined, 401);
  await customer.request('/api/account/login/', { email: address, password: replacement });
  pass('Customer logout, password recovery and invalidation of earlier sessions');

  const ownerEmail = 'review-owner@example.test';
  const ownerState = command(
    'commerce-api',
    'shell',
    '-c',
    "from commerce.tenancy.context import tenant_context; from commerce.tenancy.resolution import configured_tenant_id; from commerce.staff.models import StaffMembership; from commerce.staff.permissions import StaffRole; c=tenant_context(configured_tenant_id()); c.__enter__(); owner=StaffMembership.objects.filter(role=StaffRole.OWNER).first(); print('REVIEW_OWNER' if owner and owner.user.email == 'review-owner@example.test' else 'OTHER_OWNER' if owner else 'NO_OWNER')",
  );
  if (!ownerState.includes('OTHER_OWNER')) {
    const owner = await new Browser(WEB).ready();
    if (ownerState.includes('NO_OWNER')) {
      command('commerce-api', 'invite_owner', '--email', ownerEmail);
      const invitation = await token(ownerEmail);
      await owner.request('/api/manage/invitation/inspect/', { token: invitation });
      await owner.request(
        '/api/manage/invitation/accept/',
        { token: invitation, full_name: 'Local Review Owner', password },
        201,
      );
    } else {
      await owner.request('/api/manage/auth/password/forgot/', { email: ownerEmail }, 202);
      await owner.request(
        '/api/manage/auth/password/reset/',
        { token: await token(ownerEmail), password },
        204,
      );
      await owner.request('/api/manage/auth/login/', { email: ownerEmail, password });
    }
    const session = await owner.request('/api/manage/auth/session/');
    assert.equal(session.role, 'owner');
    assert.equal(owner.cookies.get('mc_staff').path, '/api/manage');
    await owner.request('/api/account/me/', undefined, 401);
    pass('Provisioned owner activation and separate staff cookie');
    const memberEmail = `review-staff-${id}@example.test`;
    await owner.request(
      '/api/manage/team/invitations/',
      { email: memberEmail, role: 'content_editor' },
      201,
    );
    const staff = await new Browser(WEB).ready();
    const invitation = await token(memberEmail);
    await staff.request('/api/manage/invitation/inspect/', { token: invitation });
    await staff.request(
      '/api/manage/invitation/accept/',
      { token: invitation, full_name: 'Local Review Editor', password },
      201,
    );
    await staff.request('/api/manage/team/', undefined, 403);
    await staff.request(
      '/api/manage/invitation/accept/',
      { token: invitation, full_name: 'Local Review Editor', password },
      400,
    );
    pass('Staff invitation activation, single use and owner-only team permission');
    const setup = await owner.request('/api/manage/account/mfa/totp/setup/', {});
    assert.ok(setup.qr_svg.startsWith('data:image/svg+xml;base64,'));
    const oldCookie = owner.cookies.get('mc_staff').value;
    const enrolled = await owner.request('/api/manage/account/mfa/totp/confirm/', {
      code: totp(setup.secret),
    });
    assert.equal(enrolled.recovery_codes.length, 10);
    assert.notEqual(owner.cookies.get('mc_staff').value, oldCookie);
    pass('Merchant QR enrollment, TOTP confirmation, recovery codes and token rotation');
    // Restore optional MFA for the review account so the owner can use email recovery locally.
    await owner.request('/api/manage/account/mfa/totp/disable/', {});
    await owner.request('/api/manage/auth/logout/', {}, 204);
  } else console.log('SKIP live owner actions: an existing non-review owner is preserved');

  const adminEmail = `review-admin-${id}@example.test`;
  command(
    'control-api',
    'create_admin',
    '--email',
    adminEmail,
    '--name',
    'Local Review Administrator',
  );
  const admin = await new Browser(CONSOLE).ready();
  const activation = await token(adminEmail);
  await admin.request('/api/console/auth/activate/inspect/', { token: activation });
  const activated = await admin.request('/api/console/auth/activate/', {
    token: activation,
    password,
  });
  assert.equal(activated.status, 'enrollment_required');
  await admin.request('/api/console/auth/session/', undefined, 401);
  pass('Console activation grants restricted enrollment only');
  const challenge = await admin.request('/api/console/enroll/email/start/', {}, 202);
  const enrollment = await admin.request('/api/console/enroll/email/confirm/', {
    challenge_id: challenge.challenge_id,
    code: await code(adminEmail),
  });
  assert.equal(enrollment.recovery_codes.length, 10);
  await admin.request('/api/console/auth/session/');
  await admin.request('/api/console/account/mfa/email/disable/', {}, 409);
  pass('Mandatory console MFA email enrollment and last-method protection');
  await admin.request('/api/console/auth/logout/', {}, 204);
  const pending = await admin.request('/api/console/auth/login/', { email: adminEmail, password });
  assert.equal(pending.status, 'mfa_required');
  await admin.request('/api/console/auth/session/', undefined, 401);
  const emailed = await admin.request('/api/console/auth/mfa/email/', {}, 202);
  await admin.request('/api/console/auth/mfa/verify/', {
    method: 'email',
    challenge_id: emailed.challenge_id,
    code: await code(adminEmail),
  });
  await admin.request('/api/console/auth/session/');
  await admin.request('/api/console/auth/logout/', {}, 204);
  pass('Console password-only access denial, email second step and logout');
  console.log(
    `${passed} live authentication checks passed. Review records remain labelled in the local databases.`,
  );
}
run().catch((error) => {
  // Never dump response bodies, command stderr or local credentials on failure.
  console.error(
    error instanceof assert.AssertionError && error.message.startsWith('/api/')
      ? error.message
      : 'Local auth smoke check failed; inspect the last labelled step and service health.',
  );
  process.exitCode = 1;
});

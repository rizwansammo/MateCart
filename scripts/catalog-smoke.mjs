// Deliberate local verification of the product and inventory engine (Phase 04) through the
// Next.js proxy. Creates labelled review catalogue data ("Review ... <run id>") in matecart-dev.
// Signs in a labelled local review Content editor (catalog.manage without catalog.costs), whose
// random password goes to the local container on stdin and is never printed or stored.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';

if (!process.argv.includes('--local')) throw new Error('Pass --local for the MateCart dev stack.');
const WEB = 'http://127.0.0.1:3400';
const EDITOR = 'review-catalog-editor@example.test';
const id = Date.now().toString(36);
// Creates or re-keys the review editor inside the configured local store only.
const PROVISION_EDITOR = `
import sys
from commerce.identity.models import AccountType, User
from commerce.staff.models import StaffMembership
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.resolution import configured_tenant_id
password = sys.stdin.read()
with tenant_context(configured_tenant_id()):
    user = User.objects.filter(account_type=AccountType.STAFF, email="${EDITOR}").first()
    if user is None:
        user = User.objects.create_user(
            account_type=AccountType.STAFF, email="${EDITOR}", full_name="Local Review Catalogue Editor"
        )
    user.set_password(password)
    user.save(update_fields=["password"])
    StaffMembership.objects.update_or_create(
        user=user, defaults={"role": StaffRole.CONTENT_EDITOR, "status": "active"}
    )
`;
// A valid 1x1 PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);
let passed = 0;
function pass(label) {
  passed++;
  console.log(`PASS ${label}`);
}

class Browser {
  constructor() {
    this.cookies = new Map();
    this.csrf = '';
  }
  cookieHeader(path) {
    return [...this.cookies.values()]
      .filter((c) => path.startsWith(c.path))
      .map((c) => `${c.name}=${c.value}`)
      .join('; ');
  }
  remember(res) {
    for (const raw of res.headers.getSetCookie()) {
      const parts = raw.split(';').map((p) => p.trim());
      const [name, ...value] = parts[0].split('=');
      const path = parts.find((p) => p.startsWith('Path='))?.slice(5) ?? '/';
      if (parts.includes('Max-Age=0')) this.cookies.delete(name);
      else this.cookies.set(name, { name, value: value.join('='), path });
    }
  }
  async send(method, path, body, expected, { form = false } = {}) {
    const headers = { Origin: WEB, Cookie: this.cookieHeader(path) };
    if (method !== 'GET') headers['X-CSRFToken'] = this.csrf;
    if (body !== undefined && !form) headers['Content-Type'] = 'application/json';
    const res = await fetch(WEB + path, {
      method,
      headers,
      body: body === undefined ? undefined : form ? body : JSON.stringify(body),
      redirect: 'manual',
    });
    this.remember(res);
    const type = res.headers.get('content-type') ?? '';
    const data =
      res.status === 204 ? null : type.includes('json') ? await res.json().catch(() => null) : res;
    assert.equal(
      res.status,
      expected,
      `${method} ${path}: expected ${expected}, received ${res.status} (${data?.error?.code ?? 'no error code'})`,
    );
    return data;
  }
  get(path, expected = 200) {
    return this.send('GET', path, undefined, expected);
  }
  post(path, body, expected = 200) {
    return this.send('POST', path, body, expected);
  }
  put(path, body, expected = 200) {
    return this.send('PUT', path, body, expected);
  }
  async ready() {
    this.csrf = (await this.get('/api/csrf/')).csrf_token;
    return this;
  }
}

async function run() {
  const visitor = await new Browser().ready();
  await visitor.get('/api/manage/catalog/products/', 401);
  await visitor.post('/api/manage/inventory/receipts/', {}, 401);
  pass('Merchant catalogue and inventory APIs refuse anonymous visitors');

  const password = `Review-${randomBytes(24).toString('base64url')}!7`;
  // Output is captured, never printed: it could contain local details.
  execFileSync(
    'docker',
    [
      'compose',
      'exec',
      '-T',
      'commerce-api',
      'python',
      'manage.py',
      'shell',
      '-c',
      PROVISION_EDITOR,
    ],
    { input: password, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] },
  );
  const owner = await new Browser().ready();
  const signedIn = await owner.post('/api/manage/auth/login/', { email: EDITOR, password });
  assert.equal(signedIn.status, 'signed_in');
  await owner.ready();
  const session = await owner.get('/api/manage/auth/session/');
  assert.equal(session.role, 'content_editor');
  assert.ok(session.permissions.includes('catalog.manage'));
  assert.ok(!session.permissions.includes('catalog.costs'));
  await owner.get('/api/manage/team/', 403);
  pass('Review content editor signed in: catalog.manage without costs or team access');

  const size = await owner.post(
    '/api/manage/catalog/attributes/',
    { code: `review-size-${id}`, name: 'Review size', value_type: 'option', scope: 'variant' },
    201,
  );
  for (const value of ['S', 'M']) {
    await owner.post(`/api/manage/catalog/attributes/${size.id}/options/`, { value }, 201);
  }
  const form = new FormData();
  form.append('file', new Blob([PNG], { type: 'text/html' }), '../evil.php');
  form.append('alt_text', 'Review image');
  const image = await owner.send('POST', '/api/manage/media/assets/upload/', form, 201, {
    form: true,
  });
  assert.equal(image.content_type, 'image/png');
  const svg = new FormData();
  svg.append('file', new Blob(['<svg onload="alert(1)"/>'], { type: 'image/png' }), 'x.png');
  const refused = await owner.send('POST', '/api/manage/media/assets/upload/', svg, 400, {
    form: true,
  });
  assert.equal(refused.error.code, 'unsupported_media');
  pass('Typed attribute options and server-validated image upload (SVG refused)');

  const product = await owner.post(
    '/api/manage/catalog/products/',
    {
      name: `Review Smoke Shirt ${id}`,
      product_type: 'apparel',
      options: [size.code],
      variants: [
        {
          sku: `rev-${id}-s`,
          options: { [size.code]: 's' },
          price: { amount: '1200' },
        },
        {
          sku: `rev-${id}-m`,
          options: { [size.code]: 'm' },
          price: { amount: '1250', compare_at_amount: '1400' },
        },
      ],
      apparel: { material: 'Cotton', fit: 'Regular' },
    },
    201,
  );
  assert.ok(!('cost_amount' in product.variants[0].prices[0]));
  const costDenied = await owner.put(
    `/api/manage/pricing/variants/${product.variants[0].id}/prices/`,
    { prices: [{ amount: '1200', cost_amount: '713.37' }] },
    403,
  );
  assert.equal(costDenied.error.code, 'permission_denied');
  await owner.put(`/api/manage/catalog/products/${product.id}/media/`, {
    media: [{ asset: image.id }],
  });
  const active = await owner.post(`/api/manage/catalog/products/${product.id}/status/`, {
    status: 'active',
  });
  assert.equal(active.status, 'active');
  pass('Variant product with prices, gallery and publication; cost prices refused');

  const medium = product.variants[1];
  const receipt = {
    variant: medium.id,
    quantity: '5',
    reason: 'Review smoke receipt',
    idempotency_key: `review-receipt-${id}`,
  };
  const first = await owner.post('/api/manage/inventory/receipts/', receipt, 201);
  const replay = await owner.post('/api/manage/inventory/receipts/', receipt, 200);
  assert.equal(replay.replayed, true);
  assert.equal(replay.operation_id, first.operation_id);
  const oversell = await owner.post(
    '/api/manage/inventory/adjustments/',
    { variant: medium.id, quantity: '9', movement_type: 'lost', reason: 'Review count' },
    409,
  );
  assert.equal(oversell.error.code, 'insufficient_stock');
  await owner.post(
    '/api/manage/inventory/adjustments/',
    { variant: medium.id, quantity: '1', movement_type: 'damaged', reason: 'Review damage' },
    201,
  );
  const levels = await owner.get(`/api/manage/inventory/levels/?variant=${medium.id}`);
  assert.equal(levels.results[0].available, '4.000');
  const reconciliation = await owner.get('/api/manage/inventory/reconciliation/');
  assert.equal(reconciliation.consistent, true);
  pass('Idempotent receipt, oversell refusal, damage adjustment and ledger reconciliation');

  const shopper = await new Browser().ready();
  const detail = await shopper.get(`/api/catalog/products/${active.slug}/`);
  assert.deepEqual(
    detail.variants.map((v) => [v.sku, v.in_stock]),
    [
      [`REV-${id.toUpperCase()}-S`, false],
      [`REV-${id.toUpperCase()}-M`, true],
    ],
  );
  const text = JSON.stringify(detail);
  for (const hidden of ['cost_amount', '713.37', 'on_hand', 'reserved', 'publication_problems']) {
    assert.ok(!text.includes(hidden), `public detail exposes ${hidden}`);
  }
  const file = await fetch(WEB + detail.images[0].url);
  assert.equal(file.status, 200);
  assert.equal(file.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(file.headers.get('content-type'), 'image/png');
  pass('Public catalogue shows the product without costs or stock quantities; image served safely');

  await owner.post(`/api/manage/catalog/products/${product.id}/status/`, { status: 'archived' });
  await shopper.get(`/api/catalog/products/${active.slug}/`, 404);
  assert.equal((await fetch(WEB + detail.images[0].url)).status, 404);
  pass('Archived product and its image leave the public catalogue');

  await owner.post('/api/manage/auth/logout/', {}, 204);
  console.log(
    `${passed} live catalogue checks passed. Review records remain labelled in the local database.`,
  );
}

run().catch((error) => {
  // Never dump response bodies or local credentials on failure.
  console.error(
    error instanceof assert.AssertionError && /^(GET|POST|PUT) \//.test(error.message)
      ? error.message
      : `Local catalogue smoke check failed: ${error.message?.split('\n')[0] ?? 'unknown error'}`,
  );
  process.exitCode = 1;
});

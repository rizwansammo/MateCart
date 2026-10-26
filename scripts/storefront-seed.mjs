// Local review data for the customer storefront (Phase 06), created through the merchant API of
// the matecart-dev store only: categories, a collection, about a dozen products of every product
// type with generated images, stock, homepage sections, two pages and Bangla translations.
// Additive and idempotent: it stops when its categories already exist and never deletes anything.
// Signs in the labelled local review Content editor (as catalog-smoke.mjs does); the random
// password goes to the local container on stdin and is never printed or stored.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { deflateSync } from 'node:zlib';

if (!process.argv.includes('--local')) throw new Error('Pass --local for the MateCart dev stack.');
const WEB = 'http://127.0.0.1:3400';
const EDITOR = 'review-catalog-editor@example.test';
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

// --- A tiny PNG encoder for generated review images (gradients; no external files) ------------

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}
/** A diagonal gradient from `from` to `to` with a soft light disc, as a PNG. */
function png(width, height, from, to) {
  const rows = Buffer.alloc((width * 3 + 1) * height);
  const [cx, cy, radius] = [width * 0.5, height * 0.45, Math.min(width, height) * 0.32];
  for (let y = 0; y < height; y++) {
    const row = y * (width * 3 + 1);
    rows[row] = 0;
    for (let x = 0; x < width; x++) {
      const t = (x / width + y / height) / 2;
      const glow = Math.max(0, 1 - Math.hypot(x - cx, y - cy) / radius) * 0.35;
      for (let i = 0; i < 3; i++) {
        const base = from[i] + (to[i] - from[i]) * t;
        rows[row + 1 + x * 3 + i] = Math.round(base + (255 - base) * glow);
      }
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// --- HTTP session through the Next.js proxy (cookies + CSRF) --------------------------------

class Browser {
  constructor() {
    this.cookies = new Map();
    this.csrf = '';
  }
  remember(res) {
    for (const raw of res.headers.getSetCookie()) {
      const parts = raw.split(';').map((p) => p.trim());
      const [name, ...value] = parts[0].split('=');
      if (parts.includes('Max-Age=0')) this.cookies.delete(name);
      else this.cookies.set(name, `${name}=${value.join('=')}`);
    }
  }
  async send(method, path, body, expected, form = false) {
    const headers = { Origin: WEB, Cookie: [...this.cookies.values()].join('; ') };
    if (method !== 'GET') headers['X-CSRFToken'] = this.csrf;
    if (body !== undefined && !form) headers['Content-Type'] = 'application/json';
    const res = await fetch(WEB + path, {
      method,
      headers,
      body: body === undefined ? undefined : form ? body : JSON.stringify(body),
      redirect: 'manual',
    });
    this.remember(res);
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    assert.equal(
      res.status,
      expected,
      `${method} ${path}: expected ${expected}, received ${res.status} (${data?.error?.code ?? 'no error code'})`,
    );
    return data;
  }
  get(path) {
    return this.send('GET', path, undefined, 200);
  }
  post(path, body, expected = 201) {
    return this.send('POST', path, body, expected);
  }
  patch(path, body) {
    return this.send('PATCH', path, body, 200);
  }
  put(path, body) {
    return this.send('PUT', path, body, 200);
  }
  async ready() {
    this.csrf = (await this.get('/api/csrf/')).csrf_token;
    return this;
  }
}

// --- Review catalogue --------------------------------------------------------------------------

const CATEGORIES = [
  ['Home & Kitchen', 'home-kitchen', 'ঘর ও রান্নাঘর', 'Stoneware, cookware and lighting', null],
  ['Cookware', 'cookware', 'রান্নার সরঞ্জাম', '', 'home-kitchen'],
  ['Lighting', 'lighting', 'আলো', '', 'home-kitchen'],
  ['Fashion', 'fashion', 'ফ্যাশন', 'Handloom and everyday wear', null],
  ['Groceries', 'groceries', 'মুদি', 'Rice, eggs and daily essentials', null],
  ['Electronics', 'electronics', 'ইলেকট্রনিক্স', 'Phones and accessories', null],
  ['Books', 'books', 'বই', 'Poetry, fiction and new releases', null],
];

async function run() {
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
  const api = await new Browser().ready();
  const signedIn = await api.post('/api/manage/auth/login/', { email: EDITOR, password }, 200);
  assert.equal(signedIn.status, 'signed_in');
  await api.ready();

  const existing = await api.get('/api/manage/catalog/categories/?page_size=100');
  if (existing.results.some((c) => c.slug === 'home-kitchen')) {
    console.log('Review storefront data is already present; nothing was changed.');
    await api.post('/api/manage/auth/logout/', {}, 204);
    return;
  }

  async function image(name, w, h, from, to) {
    const form = new FormData();
    form.append('file', new Blob([png(w, h, from, to)], { type: 'image/png' }), `${name}.png`);
    form.append('alt_text', name);
    return (await api.send('POST', '/api/manage/media/assets/upload/', form, 201, true)).id;
  }

  const category = {};
  for (const [name, slug, bn, description, parent] of CATEGORIES) {
    const created = await api.post('/api/manage/catalog/categories/', {
      name,
      slug,
      description,
      parent: parent ? category[parent] : null,
      image: parent ? null : await image(name, 640, 640, [214, 205, 190], [150, 132, 110]),
      translations: { bn: { name: bn } },
    });
    category[slug] = created.id;
  }
  const brand = await api.post('/api/manage/catalog/brands/', { name: 'Nova', slug: 'nova' });
  const shapla = await api.post('/api/manage/catalog/brands/', {
    name: 'Shapla Home',
    slug: 'shapla-home',
  });
  const warranty = await api.post('/api/manage/catalog/warranties/', {
    name: 'Official warranty',
    duration_months: 12,
    provider: 'manufacturer',
  });
  async function optionAttribute(code, name, values) {
    const attribute = await api.post('/api/manage/catalog/attributes/', {
      code,
      name,
      value_type: 'option',
      scope: 'variant',
    });
    for (const [position, [value, option, swatch]] of values.entries()) {
      await api.post(`/api/manage/catalog/attributes/${attribute.id}/options/`, {
        value,
        code: option,
        position,
        ...(swatch ? { swatch } : {}),
      });
    }
    return code;
  }
  const size = await optionAttribute('sf-size', 'Size', [
    ['S', 's'],
    ['M', 'm'],
    ['L', 'l'],
  ]);
  const colour = await optionAttribute('sf-colour', 'Colour', [
    ['Sand', 'sand', '#D8CBB6'],
    ['Navy', 'navy', '#1F3B4D'],
  ]);
  const storage = await optionAttribute('sf-storage', 'Storage', [
    ['128 GB', '128-gb'],
    ['256 GB', '256-gb'],
  ]);
  const pack = await optionAttribute('sf-pack', 'Pack size', [
    ['1 kg', '1-kg'],
    ['5 kg', '5-kg'],
  ]);
  const format = await optionAttribute('sf-format', 'Format', [
    ['Paperback', 'paperback'],
    ['eBook', 'ebook'],
  ]);
  const display = await api.post('/api/manage/catalog/attributes/', {
    code: 'sf-display',
    name: 'Display',
    value_type: 'text',
    scope: 'specification',
  });
  const battery = await api.post('/api/manage/catalog/attributes/', {
    code: 'sf-battery',
    name: 'Battery',
    value_type: 'number',
    unit: 'mAh',
    scope: 'specification',
  });

  const products = [
    {
      colors: [
        [226, 214, 198],
        [168, 140, 112],
      ],
      stock: { 'SF-MUG-SET': 20 },
      body: {
        name: 'Stoneware Mug Set',
        brand: shapla.id,
        summary: 'Four glazed stoneware mugs, 350 ml each.',
        description: 'Thrown and glazed by hand. Dishwasher safe.\n\nEach mug holds 350 ml.',
        categories: [category.cookware],
        variants: [{ sku: 'SF-MUG-SET', price: { amount: '850', compare_at_amount: '990' } }],
        translations: { bn: { name: 'স্টোনওয়্যার মগ সেট', summary: 'চারটি হাতে তৈরি মগ।' } },
      },
    },
    {
      colors: [
        [238, 222, 184],
        [176, 132, 64],
      ],
      stock: { 'SF-LAMP': 4 },
      body: {
        name: 'Brass Table Lamp',
        brand: shapla.id,
        summary: 'A warm brass lamp with a linen shade.',
        description: 'Solid brass base with a natural linen shade. E27 bulb holder.',
        categories: [category.lighting],
        variants: [{ sku: 'SF-LAMP', price: { amount: '3200' } }],
        translations: { bn: { name: 'পিতলের টেবিল ল্যাম্প' } },
      },
    },
    {
      colors: [
        [232, 228, 222],
        [120, 116, 108],
      ],
      stock: {},
      noImage: true,
      body: {
        name: 'Bamboo Cutting Board',
        summary: 'Out of stock in this review store.',
        categories: [category.cookware],
        variants: [{ sku: 'SF-BOARD', price: { amount: '650' } }],
      },
    },
    {
      colors: [
        [214, 196, 214],
        [120, 70, 96],
      ],
      stock: { 'SF-KURTI-S': 3, 'SF-KURTI-M': 6 },
      body: {
        name: 'Handloom Cotton Kurti',
        product_type: 'apparel',
        summary: 'Soft handloom cotton with a relaxed fit.',
        description: 'Woven on a handloom. Soft on first wear and softer with every wash.',
        categories: [category.fashion],
        options: [size],
        variants: [
          { sku: 'SF-KURTI-S', options: { [size]: 's' }, price: { amount: '1850' } },
          { sku: 'SF-KURTI-M', options: { [size]: 'm' }, price: { amount: '1850' } },
          { sku: 'SF-KURTI-L', options: { [size]: 'l' }, price: { amount: '1950' } },
        ],
        apparel: {
          audience: 'women',
          material: '100% cotton',
          fit: 'Relaxed',
          care_instructions: 'Gentle hand wash, dry in shade.',
        },
        translations: { bn: { name: 'হ্যান্ডলুম কটন কুর্তি' } },
      },
    },
    {
      colors: [
        [206, 214, 222],
        [70, 86, 110],
      ],
      stock: { 'SF-SHIRT-SAND': 5, 'SF-SHIRT-NAVY': 5 },
      body: {
        name: 'Linen Shirt',
        product_type: 'apparel',
        summary: 'Breathable linen for warm days.',
        categories: [category.fashion],
        options: [colour],
        variants: [
          { sku: 'SF-SHIRT-SAND', options: { [colour]: 'sand' }, price: { amount: '2200' } },
          { sku: 'SF-SHIRT-NAVY', options: { [colour]: 'navy' }, price: { amount: '2200' } },
        ],
        apparel: { audience: 'men', material: 'Linen', fit: 'Regular' },
      },
    },
    {
      colors: [
        [246, 240, 220],
        [196, 176, 120],
      ],
      stock: { 'SF-RICE-1': 40, 'SF-RICE-5': 12 },
      body: {
        name: 'Miniket Rice',
        product_type: 'grocery',
        summary: 'Polished miniket rice.',
        categories: [category.groceries],
        options: [pack],
        variants: [
          {
            sku: 'SF-RICE-1',
            options: { [pack]: '1-kg' },
            net_quantity: '1',
            net_unit: 'kg',
            price: { amount: '92' },
          },
          {
            sku: 'SF-RICE-5',
            options: { [pack]: '5-kg' },
            net_quantity: '5',
            net_unit: 'kg',
            price: { amount: '440' },
          },
        ],
        grocery: {
          storage_instructions: 'Keep in a cool, dry place.',
          shelf_life_days: 365,
          country_of_origin: 'BD',
        },
        translations: { bn: { name: 'মিনিকেট চাল' } },
      },
    },
    {
      colors: [
        [250, 236, 214],
        [214, 168, 112],
      ],
      stock: { 'SF-EGGS-12': 30 },
      body: {
        name: 'Farm Eggs',
        product_type: 'grocery',
        summary: 'Brown eggs, a dozen.',
        categories: [category.groceries],
        variants: [
          { sku: 'SF-EGGS-12', net_quantity: '12', net_unit: 'pcs', price: { amount: '165' } },
        ],
        grocery: { is_perishable: true, storage_instructions: 'Refrigerate.', shelf_life_days: 14 },
        translations: { bn: { name: 'খামারের ডিম' } },
      },
    },
    {
      colors: [
        [52, 64, 92],
        [12, 16, 24],
      ],
      stock: { 'SF-NOVA-128': 8, 'SF-NOVA-256': 2 },
      body: {
        name: 'Nova X12 Pro',
        product_type: 'electronics',
        brand: brand.id,
        summary: '6.7-inch 120 Hz display, 5,000 mAh battery.',
        description: 'Brand-new official unit with local warranty.',
        categories: [category.electronics],
        options: [storage],
        variants: [
          { sku: 'SF-NOVA-128', options: { [storage]: '128-gb' }, price: { amount: '49999' } },
          {
            sku: 'SF-NOVA-256',
            options: { [storage]: '256-gb' },
            price: { amount: '54999', compare_at_amount: '59999' },
          },
        ],
        electronics: { model_number: 'NX-12P', warranty: warranty.id },
        specifications: [
          { attribute: display.code, value: '6.7" AMOLED, 120 Hz' },
          { attribute: battery.code, value: 5000 },
        ],
      },
    },
    {
      colors: [
        [110, 31, 42],
        [40, 12, 18],
      ],
      stock: { 'SF-GITANJALI-PB': 15 },
      cover: true,
      body: {
        name: 'Gitanjali',
        product_type: 'book',
        summary: 'Song offerings.',
        description: 'A collection of poems by Rabindranath Tagore.',
        categories: [category.books],
        options: [format],
        variants: [
          {
            sku: 'SF-GITANJALI-PB',
            options: { [format]: 'paperback' },
            isbn: '978-0-306-40615-7',
            price: { amount: '450' },
          },
          {
            sku: 'SF-GITANJALI-EB',
            options: { [format]: 'ebook' },
            kind: 'digital',
            digital_format: 'epub',
            price: { amount: '250' },
          },
        ],
        book: {
          authors: ['Rabindranath Tagore'],
          publisher: 'Review Classics',
          edition: 'Revised',
          language: 'bn',
          genre: 'Poetry',
          page_count: 160,
        },
        translations: { bn: { name: 'গীতাঞ্জলি' } },
      },
    },
    {
      colors: [
        [31, 59, 77],
        [10, 20, 28],
      ],
      stock: {},
      cover: true,
      body: {
        name: 'The River Notebook',
        product_type: 'book',
        summary: 'A new novel, available to pre-order.',
        categories: [category.books],
        variants: [{ sku: 'SF-RIVER', release_date: '2027-03-15', price: { amount: '520' } }],
        book: {
          authors: ['Review Author'],
          publisher: 'Review Press',
          language: 'en',
          genre: 'Fiction',
        },
      },
    },
  ];

  const ids = [];
  for (const { body, stock, colors, noImage, cover } of products) {
    const product = await api.post('/api/manage/catalog/products/', body);
    if (!noImage) {
      const [w, h] = cover ? [600, 900] : [800, 800];
      const asset = await image(body.name, w, h, colors[0], colors[1]);
      await api.put(`/api/manage/catalog/products/${product.id}/media/`, { media: [{ asset }] });
    }
    await api.post(`/api/manage/catalog/products/${product.id}/status/`, { status: 'active' }, 200);
    for (const variant of product.variants) {
      const quantity = stock[variant.sku];
      if (!quantity) continue;
      await api.post('/api/manage/inventory/receipts/', {
        variant: variant.id,
        quantity: String(quantity),
        reason: 'Review storefront stock',
        idempotency_key: `sf-seed-${variant.sku}`,
      });
    }
    ids.push(product.id);
  }
  const collection = await api.post('/api/manage/catalog/collections/', {
    name: 'New this week',
    slug: 'new-this-week',
    description: 'Fresh arrivals across the store.',
    translations: { bn: { name: 'এই সপ্তাহে নতুন' } },
  });
  await api.put(`/api/manage/catalog/collections/${collection.id}/products/`, {
    products: [ids[3], ids[7], ids[0], ids[5], ids[8], ids[1], ids[4], ids[6], ids[9], ids[2]],
  });

  // Homepage: the preset's default sections, completed with real content.
  const current = await api.get('/api/manage/content/sections/');
  const sections = (current.results ?? current).length
    ? current
    : await api.post('/api/manage/content/sections/defaults/', {});
  const hero = await image('Storefront hero', 1600, 900, [222, 232, 224], [96, 128, 104]);
  for (const section of sections.results ?? sections) {
    const update = { is_enabled: true };
    if (section.section_type === 'hero') {
      Object.assign(update, {
        title: section.title || 'New this week',
        subtitle: 'Review store: products, prices and stock are sample data.',
        image: hero,
        cta_label: 'Shop new arrivals',
        cta_url: '/collections/new-this-week',
        translations: {
          bn: {
            title: 'এই সপ্তাহে নতুন',
            subtitle: 'রিভিউ স্টোর: পণ্য, দাম ও স্টক নমুনা তথ্য।',
            cta_label: 'নতুন পণ্য দেখুন',
          },
        },
      });
    }
    if (section.section_type === 'collection_highlight') {
      Object.assign(update, { collection: collection.id, title: section.title || 'New this week' });
    }
    if (!section.title && !update.title) update.title = 'Featured';
    await api.patch(`/api/manage/content/sections/${section.id}/`, update);
  }

  const pages = [
    {
      title: 'Shipping information',
      page_type: 'policy',
      body: 'This is a review store. Shipping options are configured in a later release.',
      translations: { bn: { title: 'শিপিং তথ্য', body: 'এটি একটি রিভিউ স্টোর।' } },
    },
    {
      title: 'About us',
      body: 'MateCart review store, used to check the customer storefront locally.',
      translations: { bn: { title: 'আমাদের কথা' } },
    },
  ];
  for (const page of pages) {
    const created = await api.post('/api/manage/content/pages/', { ...page, show_in_footer: true });
    await api.post(`/api/manage/content/pages/${created.id}/status/`, { status: 'published' }, 200);
  }
  await api.patch('/api/manage/store/appearance/', {
    tagline: 'Everyday goods, chosen with care',
    announcement: 'Review store: sample products and prices for local testing.',
    translations: {
      bn: {
        tagline: 'প্রতিদিনের পণ্য, যত্ন করে বাছাই করা',
        announcement: 'রিভিউ স্টোর: স্থানীয় পরীক্ষার জন্য নমুনা পণ্য ও দাম।',
      },
    },
  });

  await api.post('/api/manage/auth/logout/', {}, 204);
  console.log(
    `Review storefront data created: ${CATEGORIES.length} categories, ${products.length} products, ` +
      `1 collection, ${pages.length} pages and the homepage sections.`,
  );
}

run().catch((error) => {
  // Never dump response bodies or local credentials on failure.
  console.error(
    error instanceof assert.AssertionError && /^(GET|POST|PUT|PATCH) \//.test(error.message)
      ? error.message
      : `Storefront seed failed: ${error.message?.split('\n')[0] ?? 'unknown error'}`,
  );
  process.exitCode = 1;
});

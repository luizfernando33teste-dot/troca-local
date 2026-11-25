import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.js';

class MemoryBucket {
  objects = new Map();

  async put(key, value, options = {}) {
    const bytes = value instanceof Uint8Array
      ? value
      : new Uint8Array(await new Response(value).arrayBuffer());
    const object = {
      key,
      bytes,
      size: bytes.byteLength,
      uploaded: new Date(),
      customMetadata: options.customMetadata ?? {},
      httpMetadata: options.httpMetadata ?? {},
    };
    this.objects.set(key, object);
    return object;
  }

  async get(key) {
    const object = this.objects.get(key);
    if (!object) return null;
    return {
      ...object,
      body: new Blob([object.bytes]).stream(),
      text: async () => new TextDecoder().decode(object.bytes),
      arrayBuffer: async () => object.bytes.buffer.slice(
        object.bytes.byteOffset,
        object.bytes.byteOffset + object.bytes.byteLength,
      ),
    };
  }

  async head(key) {
    return this.objects.get(key) ?? null;
  }

  async list({ prefix = '', include = [] } = {}) {
    const objects = [...this.objects.values()].filter(object => object.key.startsWith(prefix));
    return {
      objects: objects.map(object => include.includes('customMetadata') ? object : { ...object, customMetadata: undefined }),
      truncated: false,
    };
  }

  async delete(keys) {
    for (const key of Array.isArray(keys) ? keys : [keys]) this.objects.delete(key);
  }
}

function environment() {
  return { FILES_BUCKET: new MemoryBucket() };
}

async function newSession(env) {
  const response = await worker.fetch(new Request('https://example.test/api/sessions', { method: 'POST' }), env);
  assert.equal(response.status, 201);
  return (await response.json()).token;
}

test('creates a temporary session and stores its expiry metadata', async () => {
  const env = environment();
  const token = await newSession(env);
  assert.match(token, /^[A-Za-z0-9_-]{32,}$/);
  const record = await env.FILES_BUCKET.get(`sessions/${token}.json`);
  assert.ok(record);
  assert.equal(JSON.parse(new TextDecoder().decode(await record.arrayBuffer())).createdAt > 0, true);
});

test('uploads, lists, and downloads a file using the session link', async () => {
  const env = environment();
  const token = await newSession(env);
  const upload = await worker.fetch(new Request(
    `https://example.test/api/upload/${token}?name=${encodeURIComponent('../foto teste.txt')}`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: 'conteudo do arquivo' },
  ), env);
  assert.equal(upload.status, 201);
  const uploaded = await upload.json();
  assert.equal(uploaded.name, 'foto teste.txt');

  const listing = await worker.fetch(new Request(`https://example.test/api/files/${token}`), env);
  assert.equal(listing.status, 200);
  const files = await listing.json();
  assert.equal(files.files.length, 1);
  assert.equal(files.files[0].name, 'foto teste.txt');

  const download = await worker.fetch(new Request(`https://example.test/api/download/${token}/${uploaded.id}`), env);
  assert.equal(download.status, 200);
  assert.equal(await download.text(), 'conteudo do arquivo');
  assert.match(download.headers.get('content-disposition'), /foto%20teste\.txt/);
});

test('rejects access with an unknown session token', async () => {
  const env = environment();
  const response = await worker.fetch(new Request('https://example.test/api/files/not-a-real-token'), env);
  assert.equal(response.status, 404);
});

test('rejects uploads larger than the configured 50 MB limit', async () => {
  const env = environment();
  const token = await newSession(env);
  const response = await worker.fetch(new Request(`https://example.test/api/upload/${token}`, {
    method: 'POST',
    headers: { 'Content-Length': String(50 * 1024 * 1024 + 1), 'Content-Type': 'application/octet-stream' },
  }), env);
  assert.equal(response.status, 413);
});

test('rejects sessions after 24 hours', async () => {
  const env = environment();
  const token = await newSession(env);
  await env.FILES_BUCKET.put(`sessions/${token}.json`, JSON.stringify({ createdAt: Date.now() - 25 * 60 * 60 * 1000 }));
  const response = await worker.fetch(new Request(`https://example.test/api/files/${token}`), env);
  assert.equal(response.status, 404);
});

test('does not expose or download files after 24 hours', async () => {
  const env = environment();
  const token = await newSession(env);
  const uploaded = await worker.fetch(new Request(`https://example.test/api/upload/${token}?name=velho.txt`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain' },
    body: 'conteudo',
  }), env);
  const file = await uploaded.json();
  const stored = env.FILES_BUCKET.objects.get(`files/${token}/${file.id}`);
  stored.customMetadata.uploadedAt = String(Date.now() - 25 * 60 * 60 * 1000);
  stored.uploaded = new Date(Number(stored.customMetadata.uploadedAt));

  const listing = await worker.fetch(new Request(`https://example.test/api/files/${token}`), env);
  assert.deepEqual((await listing.json()).files, []);
  const download = await worker.fetch(new Request(`https://example.test/api/download/${token}/${file.id}`), env);
  assert.equal(download.status, 404);
});

test('scheduled cleanup removes expired sessions and their files', async () => {
  const env = environment();
  const token = await newSession(env);
  await env.FILES_BUCKET.put(`files/${token}/old-id`, 'old', {
    customMetadata: { uploadedAt: String(Date.now() - 25 * 60 * 60 * 1000) },
  });
  await env.FILES_BUCKET.put(`sessions/${token}.json`, JSON.stringify({ createdAt: Date.now() - 25 * 60 * 60 * 1000 }));
  await worker.scheduled({}, env);
  assert.equal(await env.FILES_BUCKET.head(`sessions/${token}.json`), null);
  assert.equal(await env.FILES_BUCKET.head(`files/${token}/old-id`), null);
});

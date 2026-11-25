const MAX_FILE_BYTES = 50 * 1024 * 1024;
const MAX_STORAGE_BYTES = 1024 * 1024 * 1024;
const MAX_FILES_PER_SESSION = 1_000;
const SESSION_TTL_MS = 24 * 60 * 60 * 1_000;
const SESSION_PREFIX = 'sessions/';
const FILE_PREFIX = 'files/';
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{40,48}$/;
const ID_PATTERN = /^[a-f0-9-]{36}$/i;
const CONTROL_CHARS = /[\x00-\x1f\x7f]/g;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    },
  });
}

function safeFilename(value) {
  const name = String(value || 'arquivo').replaceAll('\\', '/').split('/').pop();
  return (name.replace(CONTROL_CHARS, '_').trim().replace(/^\.+|\.+$/g, '').slice(0, 160)) || 'arquivo';
}

function tokenFromPath(pathname, route) {
  const match = pathname.match(route);
  if (!match || !TOKEN_PATTERN.test(match[1])) return null;
  return match[1];
}

async function listAll(bucket, prefix) {
  const objects = [];
  let cursor;
  do {
    const page = await bucket.list({ prefix, limit: 1_000, include: ['customMetadata'], ...(cursor ? { cursor } : {}) });
    objects.push(...page.objects);
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return objects;
}

async function getSession(bucket, token) {
  if (!TOKEN_PATTERN.test(token)) return null;
  const object = await bucket.get(`${SESSION_PREFIX}${token}.json`);
  if (!object) return null;
  let session;
  try {
    session = JSON.parse(await object.text());
  } catch {
    return null;
  }
  if (!Number.isFinite(session.createdAt) || Date.now() - session.createdAt >= SESSION_TTL_MS) return null;
  return session;
}

async function createSession(bucket, origin) {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const token = btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
  const createdAt = Date.now();
  await bucket.put(`${SESSION_PREFIX}${token}.json`, JSON.stringify({ createdAt }), {
    httpMetadata: { contentType: 'application/json' },
  });
  return json({ token, url: `${origin}/?session=${encodeURIComponent(token)}` }, 201);
}

async function listFiles(bucket, token) {
  const prefix = `${FILE_PREFIX}${token}/`;
  const now = Date.now();
  const objects = (await listAll(bucket, prefix)).filter(object => {
    const uploadedAt = Number(object.customMetadata?.uploadedAt || object.uploaded?.getTime() || 0);
    return uploadedAt > 0 && now - uploadedAt < SESSION_TTL_MS;
  });
  const files = objects.map(object => ({
    id: object.key.slice(prefix.length),
    name: object.customMetadata?.name || 'arquivo',
    size: object.size,
    uploaded: Number(object.customMetadata?.uploadedAt || object.uploaded?.getTime() || Date.now()),
  })).sort((left, right) => right.uploaded - left.uploaded);
  return json({ files });
}

async function uploadFile(request, bucket, token) {
  const contentLength = request.headers.get('Content-Length');
  let body = request.body;
  let size = Number(contentLength);
  if (contentLength === null) {
    body = await request.arrayBuffer();
    size = body.byteLength;
  }
  if (!Number.isFinite(size) || size <= 0) return json({ error: 'O arquivo está vazio.' }, 400);
  if (size > MAX_FILE_BYTES) return json({ error: 'O arquivo excede o limite de 50 MB.' }, 413);

  const prefix = `${FILE_PREFIX}${token}/`;
  const files = await listAll(bucket, prefix);
  if (files.length >= MAX_FILES_PER_SESSION) return json({ error: 'Esta sessão atingiu o limite de arquivos.' }, 413);
  const usedBytes = files.reduce((sum, file) => sum + file.size, 0);
  if (usedBytes + size > MAX_STORAGE_BYTES) return json({ error: 'O espaço de 1 GB desta sessão está cheio.' }, 413);

  const id = crypto.randomUUID();
  const name = safeFilename(new URL(request.url).searchParams.get('name'));
  const uploadedAt = Date.now();
  await bucket.put(`${prefix}${id}`, body, {
    httpMetadata: { contentType: request.headers.get('Content-Type') || 'application/octet-stream' },
    customMetadata: { name, uploadedAt: String(uploadedAt) },
  });
  return json({ id, name, size, uploaded: uploadedAt }, 201);
}

async function downloadFile(bucket, token, id) {
  if (!ID_PATTERN.test(id)) return new Response('Não encontrado.', { status: 404 });
  const object = await bucket.get(`${FILE_PREFIX}${token}/${id}`);
  if (!object) return new Response('Não encontrado.', { status: 404 });
  const uploadedAt = Number(object.customMetadata?.uploadedAt || object.uploaded?.getTime() || 0);
  if (!uploadedAt || Date.now() - uploadedAt >= SESSION_TTL_MS) return new Response('Não encontrado.', { status: 404 });
  const name = safeFilename(object.customMetadata?.name);
  const headers = new Headers({
    'Content-Type': 'application/octet-stream',
    'Content-Length': String(object.size),
    'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
  });
  return new Response(object.body, { headers });
}

async function cleanup(bucket, now = Date.now()) {
  const sessions = await listAll(bucket, SESSION_PREFIX);
  for (const sessionObject of sessions) {
    let data;
    try {
      const item = await bucket.get(sessionObject.key);
      data = JSON.parse(await item.text());
    } catch {
      data = null;
    }
    const token = sessionObject.key.slice(SESSION_PREFIX.length).replace(/\.json$/, '');
    if (!TOKEN_PATTERN.test(token)) {
      await bucket.delete(sessionObject.key);
      continue;
    }
    const expiredSession = !Number.isFinite(data?.createdAt) || now - data.createdAt >= SESSION_TTL_MS;
    const files = await listAll(bucket, `${FILE_PREFIX}${token}/`);
    const expiredFiles = files.filter(file => {
      const uploadedAt = Number(file.customMetadata?.uploadedAt || file.uploaded?.getTime() || 0);
      return expiredSession || now - uploadedAt >= SESSION_TTL_MS;
    });
    if (expiredFiles.length) await bucket.delete(expiredFiles.map(file => file.key));
    if (expiredSession) await bucket.delete(sessionObject.key);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    const bucket = env.FILES_BUCKET;

    if (pathname === '/api/sessions' && request.method === 'POST') {
      return createSession(bucket, url.origin);
    }

    const filesToken = tokenFromPath(pathname, /^\/api\/files\/([^/]+)$/);
    if (filesToken && request.method === 'GET') {
      if (!(await getSession(bucket, filesToken))) return json({ error: 'Sessão inválida ou expirada.' }, 404);
      return listFiles(bucket, filesToken);
    }

    const uploadToken = tokenFromPath(pathname, /^\/api\/upload\/([^/]+)$/);
    if (uploadToken && request.method === 'POST') {
      if (!(await getSession(bucket, uploadToken))) return json({ error: 'Sessão inválida ou expirada.' }, 404);
      return uploadFile(request, bucket, uploadToken);
    }

    const downloadMatch = pathname.match(/^\/api\/download\/([^/]+)\/([^/]+)$/);
    if (downloadMatch && request.method === 'GET') {
      const [, token, id] = downloadMatch;
      if (!(await getSession(bucket, token))) return json({ error: 'Sessão inválida ou expirada.' }, 404);
      return downloadFile(bucket, token, id);
    }

    if (pathname.startsWith('/api/')) return json({ error: 'Endpoint não encontrado.' }, 404);
    if (!env.ASSETS) return new Response('Arquivos do site indisponíveis.', { status: 503 });
    const response = await env.ASSETS.fetch(request);
    const headers = new Headers(response.headers);
    headers.set('X-Content-Type-Options', 'nosniff');
    headers.set('Referrer-Policy', 'no-referrer');
    headers.set('X-Frame-Options', 'DENY');
    headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },

  async scheduled(_event, env) {
    await cleanup(env.FILES_BUCKET);
  },
};

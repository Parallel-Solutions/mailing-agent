/**
 * Thin API client for setup / email tests (real backend, no mocks).
 */

const API_URL = (process.env.E2E_API_URL || process.env.E2E_BASE_URL || 'http://web:9806').replace(
  /\/$/,
  '',
);
const USERNAME = process.env.E2E_USERNAME || 'admin';
const PASSWORD = process.env.E2E_PASSWORD || 'change-me';
const SESSION_COOKIE = 'mailing_agent_session';

export type ApiSession = {
  cookie: string;
  username: string;
};

async function parseJson(response: Response): Promise<any> {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Non-JSON ${response.status}: ${text.slice(0, 400)}`);
  }
}

export async function apiLogin(
  username = USERNAME,
  password = PASSWORD,
): Promise<ApiSession> {
  const response = await fetch(`${API_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await parseJson(response);
  if (!response.ok) {
    throw new Error(`Login failed ${response.status}: ${JSON.stringify(data)}`);
  }
  const setCookie = response.headers.getSetCookie?.() || [];
  let cookie = '';
  for (const line of setCookie) {
    if (line.startsWith(`${SESSION_COOKIE}=`)) {
      cookie = line.split(';')[0];
      break;
    }
  }
  // Node fetch may expose set-cookie differently; fallback via raw header
  if (!cookie) {
    const raw = response.headers.get('set-cookie') || '';
    const match = raw.match(new RegExp(`${SESSION_COOKIE}=([^;]+)`));
    if (match) cookie = `${SESSION_COOKIE}=${match[1]}`;
  }
  if (!cookie) {
    throw new Error('Login succeeded but session cookie was not returned.');
  }
  return { cookie, username };
}

export async function apiLogout(session: ApiSession): Promise<void> {
  await fetch(`${API_URL}/api/auth/logout`, {
    method: 'POST',
    headers: { Cookie: session.cookie },
  });
}

export async function apiGet(session: ApiSession, path: string): Promise<any> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { Cookie: session.cookie },
  });
  const data = await parseJson(response);
  if (!response.ok) {
    throw new Error(`GET ${path} → ${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

export async function apiPost(session: ApiSession, path: string, body?: unknown): Promise<any> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: {
      Cookie: session.cookie,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await parseJson(response);
  if (!response.ok) {
    throw new Error(`POST ${path} → ${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

export async function apiPostMultipart(
  session: ApiSession,
  path: string,
  form: FormData,
): Promise<any> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { Cookie: session.cookie },
    body: form,
  });
  const data = await parseJson(response);
  if (!response.ok) {
    throw new Error(`POST multipart ${path} → ${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

export async function ensureMailpitMailbox(session: ApiSession): Promise<{ id: string; email: string }> {
  const email = process.env.E2E_SMTP_SENDER_EMAIL || 'e2e-sender@example.test';
  const password = process.env.E2E_SMTP_SENDER_PASSWORD || 'mailpit-e2e';
  const host = process.env.E2E_SMTP_HOST || 'mailpit';
  const port = Number(process.env.E2E_SMTP_PORT || 1025);

  const listed = await apiGet(session, '/api/smtp/mailboxes');
  const mailboxes = listed?.result?.mailboxes || listed?.mailboxes || [];
  const existing = (mailboxes as Array<any>).find(
    (m) => String(m.email || '').toLowerCase() === email.toLowerCase(),
  );
  if (existing?.id) {
    await apiPost(session, `/api/smtp/mailboxes/${existing.id}/default`);
    return { id: String(existing.id), email };
  }

  const created = await apiPost(session, '/api/smtp/mailboxes', {
    provider: 'custom',
    email,
    password,
    sender_name: 'E2E Mailpit',
    host,
    port,
    use_ssl: false,
    use_starttls: false,
    make_default: true,
    send_test: false,
  });
  const mailbox = created?.result?.mailbox || created?.mailbox;
  if (!mailbox?.id) {
    throw new Error(`Failed to create mailbox: ${JSON.stringify(created)}`);
  }
  return { id: String(mailbox.id), email };
}

export { API_URL, USERNAME, PASSWORD, SESSION_COOKIE };

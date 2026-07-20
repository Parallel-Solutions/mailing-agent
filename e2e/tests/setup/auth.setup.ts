import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { apiLogin, API_URL, USERNAME, PASSWORD, SESSION_COOKIE } from '../fixtures/appApi';
import { mailpitDeleteAll, MAILPIT_URL } from '../fixtures/mailpit';

const authFile = '/artifacts/auth/user.json';

setup('prepare e2e auth and clean mailpit @setup', async ({ request }) => {
  // Services already waited by entrypoint; double-check critical endpoints.
  const health = await request.get(`${API_URL}/health`);
  expect(health.ok(), `health not ok: ${health.status()}`).toBeTruthy();
  const healthBody = await health.json();
  expect(healthBody.status).toBe('ok');
  expect(healthBody.database).toBe('up');

  const ready = await request.get(`${API_URL}/ready`);
  expect(ready.ok(), `ready not ok: ${ready.status()}`).toBeTruthy();
  const readyBody = await ready.json();
  expect(readyBody.status).toBe('ok');

  const mailpit = await request.get(`${MAILPIT_URL}/api/v1/info`);
  expect(mailpit.ok(), `mailpit not ok: ${mailpit.status()}`).toBeTruthy();

  await mailpitDeleteAll();

  const session = await apiLogin(USERNAME, PASSWORD);
  const me = await request.get(`${API_URL}/api/auth/me`, {
    headers: { Cookie: session.cookie },
  });
  expect(me.ok()).toBeTruthy();
  const meBody = await me.json();
  const user = meBody?.result?.user || meBody?.user;
  expect(user?.username || USERNAME).toBeTruthy();

  const token = session.cookie.split('=')[1];
  const base = new URL(API_URL);
  const storage = {
    cookies: [
      {
        name: SESSION_COOKIE,
        value: token,
        domain: base.hostname,
        path: '/',
        expires: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 7,
        httpOnly: true,
        secure: false,
        sameSite: 'Lax' as const,
      },
    ],
    origins: [],
  };

  fs.mkdirSync(path.dirname(authFile), { recursive: true });
  fs.writeFileSync(authFile, JSON.stringify(storage, null, 2));
});

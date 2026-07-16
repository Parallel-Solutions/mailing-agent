#!/usr/bin/env node
/**
 * Wait for app, backend health, and Mailpit before handing off to Playwright.
 * Exit non-zero on timeout. Usage: node wait-for-services.mjs [--] <cmd...>
 */
import { spawn } from 'node:child_process';

const APP_URL = (process.env.E2E_BASE_URL || 'http://web:9806').replace(/\/$/, '');
const API_URL = (process.env.E2E_API_URL || APP_URL).replace(/\/$/, '');
const MAILPIT_URL = (process.env.MAILPIT_API_URL || 'http://mailpit:8025').replace(/\/$/, '');
const TIMEOUT_MS = Number(process.env.E2E_WAIT_TIMEOUT_MS || 180_000);
const INTERVAL_MS = 2_000;

async function check(name, url, validate) {
  const response = await fetch(url, { redirect: 'manual' });
  if (!response.ok && response.status !== 302 && response.status !== 301) {
    throw new Error(`${name}: HTTP ${response.status} for ${url}`);
  }
  if (validate) {
    await validate(response);
  }
  return true;
}

async function checkHealth() {
  const response = await fetch(`${API_URL}/health`);
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.status !== 'ok') {
    throw new Error(`backend health: HTTP ${response.status} body=${JSON.stringify(body)}`);
  }
  if (body.database !== 'up') {
    throw new Error(`backend health: database not up (${JSON.stringify(body)})`);
  }
}

async function checkMailpit() {
  const response = await fetch(`${MAILPIT_URL}/api/v1/info`);
  if (!response.ok) {
    throw new Error(`mailpit: HTTP ${response.status}`);
  }
  await response.json();
}

async function waitAll() {
  const started = Date.now();
  let lastError = 'not started';
  while (Date.now() - started < TIMEOUT_MS) {
    try {
      await check('frontend', `${APP_URL}/login`);
      await checkHealth();
      await checkMailpit();
      const elapsed = ((Date.now() - started) / 1000).toFixed(1);
      console.log(`[wait-for-services] ready in ${elapsed}s`);
      console.log(`  frontend: ${APP_URL}`);
      console.log(`  health:   ${API_URL}/health`);
      console.log(`  mailpit:  ${MAILPIT_URL}`);
      return;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
      const left = Math.max(0, TIMEOUT_MS - (Date.now() - started));
      console.log(`[wait-for-services] waiting... (${lastError}) [${Math.ceil(left / 1000)}s left]`);
      await new Promise((r) => setTimeout(r, INTERVAL_MS));
    }
  }
  console.error(`[wait-for-services] TIMEOUT after ${TIMEOUT_MS}ms: ${lastError}`);
  process.exit(1);
}

function runCommand(argv) {
  if (!argv.length) {
    process.exit(0);
  }
  const child = spawn(argv[0], argv.slice(1), { stdio: 'inherit', shell: false });
  child.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });
}

const args = process.argv.slice(2);
const dash = args.indexOf('--');
const cmd = dash >= 0 ? args.slice(dash + 1) : args;

await waitAll();
runCommand(cmd);

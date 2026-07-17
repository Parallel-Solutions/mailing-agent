import { describe, expect, it } from 'vitest';
import {
  authKindFromSetupAction,
  isOAuthKindAvailable,
  resolveOAuthProvider,
} from './connectionAuthKind';

describe('authKindFromSetupAction', () => {
  it('maps show_oauth to oauth', () => {
    expect(authKindFromSetupAction('show_oauth')).toBe('oauth');
  });

  it('maps show_app_password to app_password', () => {
    expect(authKindFromSetupAction('show_app_password')).toBe('app_password');
  });

  it('maps password and manual actions to password', () => {
    expect(authKindFromSetupAction('show_password')).toBe('password');
    expect(authKindFromSetupAction('show_manual')).toBe('password');
    expect(authKindFromSetupAction('apply_settings')).toBe('password');
    expect(authKindFromSetupAction('retry_probe')).toBe('password');
    expect(authKindFromSetupAction('contact_admin')).toBe('password');
  });

  it('defaults unknown actions to password', () => {
    expect(authKindFromSetupAction(undefined)).toBe('password');
    expect(authKindFromSetupAction('')).toBe('password');
    expect(authKindFromSetupAction('something_else')).toBe('password');
  });
});

describe('resolveOAuthProvider', () => {
  it('prefers explicit oauth_provider', () => {
    expect(resolveOAuthProvider({ oauthProvider: 'microsoft', email: 'a@gmail.com' })).toBe(
      'microsoft',
    );
  });

  it('resolves from smtp provider and email domain', () => {
    expect(resolveOAuthProvider({ smtpProvider: 'gmail' })).toBe('google');
    expect(resolveOAuthProvider({ smtpProvider: 'outlook' })).toBe('microsoft');
    expect(resolveOAuthProvider({ email: 'user@gmail.com' })).toBe('google');
    expect(resolveOAuthProvider({ email: 'user@outlook.com' })).toBe('microsoft');
    expect(resolveOAuthProvider({ email: 'user@yandex.ru' })).toBeNull();
  });
});

describe('isOAuthKindAvailable', () => {
  it('requires a resolvable provider that is configured on the server', () => {
    expect(
      isOAuthKindAvailable({
        email: 'user@gmail.com',
        oauthAvailable: { google: true, microsoft: false },
      }),
    ).toBe(true);
    expect(
      isOAuthKindAvailable({
        email: 'user@gmail.com',
        oauthAvailable: { google: false, microsoft: false },
      }),
    ).toBe(false);
    expect(
      isOAuthKindAvailable({
        email: 'user@yandex.ru',
        oauthAvailable: { google: true, microsoft: true },
      }),
    ).toBe(false);
  });
});

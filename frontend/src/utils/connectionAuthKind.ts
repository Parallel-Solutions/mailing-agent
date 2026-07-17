/** Auth kinds for the mailbox (SMTP) branch only. API key is a separate top-level method. */
export type AuthKind = 'password' | 'app_password' | 'oauth';

export type AuthKindOption = {
  value: AuthKind;
  label: string;
  description: string;
};

export const MAILBOX_AUTH_KIND_OPTIONS: AuthKindOption[] = [
  {
    value: 'password',
    label: 'Логин + обычный пароль',
    description: 'Простые IMAP/SMTP-серверы',
  },
  {
    value: 'app_password',
    label: 'Логин + пароль приложения',
    description: 'Gmail, Яндекс, Mail.ru и другие сервисы при включённой 2FA',
  },
  {
    value: 'oauth',
    label: 'OAuth 2.0',
    description: 'Gmail, Microsoft 365, Outlook и современные корпоративные сервисы',
  },
];

/** Map backend setup action to the most likely mailbox auth UI kind. */
export function authKindFromSetupAction(action: string | undefined | null): AuthKind {
  switch (String(action || '').trim()) {
    case 'show_oauth':
      return 'oauth';
    case 'show_app_password':
      return 'app_password';
    case 'show_password':
    case 'show_manual':
    case 'apply_settings':
    case 'retry_probe':
    case 'contact_admin':
    default:
      return 'password';
  }
}

export function resolveOAuthProvider(input: {
  oauthProvider?: string | null;
  email?: string;
  smtpProvider?: string | null;
}): 'google' | 'microsoft' | null {
  const explicit = String(input.oauthProvider || '').trim().toLowerCase();
  if (explicit === 'google' || explicit === 'microsoft') return explicit;

  const smtpProvider = String(input.smtpProvider || '').trim().toLowerCase();
  if (smtpProvider === 'gmail') return 'google';
  if (smtpProvider === 'outlook') return 'microsoft';

  const domain = String(input.email || '').trim().toLowerCase().split('@')[1] || '';
  if (domain === 'gmail.com' || domain === 'googlemail.com') return 'google';
  if (
    domain === 'outlook.com'
    || domain === 'hotmail.com'
    || domain === 'live.com'
    || domain === 'msn.com'
  ) {
    return 'microsoft';
  }
  return null;
}

export function isOAuthKindAvailable(input: {
  oauthAvailable?: Record<string, boolean> | null;
  oauthProvider?: string | null;
  email?: string;
  smtpProvider?: string | null;
}): boolean {
  const provider = resolveOAuthProvider(input);
  if (!provider) return false;
  return Boolean(input.oauthAvailable?.[provider]);
}

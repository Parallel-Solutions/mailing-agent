import { describe, expect, it } from 'vitest';
import type { SmtpSetupAnalysis, SmtpSetupSettings } from '@/api/connections';
import { selectSmtpSetupSettings, smtpSetupSecurity } from './smtpSetup';

const settings: SmtpSetupSettings = {
  provider: 'yandex',
  host: 'smtp.yandex.ru',
  port: 465,
  use_ssl: true,
  use_starttls: false,
};

function analysis(
  overrides: Partial<SmtpSetupAnalysis> = {},
): SmtpSetupAnalysis {
  return {
    setup_session_id: 'session-1',
    email: 'user@example.ru',
    domain: 'example.ru',
    probe: null,
    discoveries: [],
    action: {
      action: 'show_password',
      message_ru: '',
      instructions: [],
      recommended_settings: null,
    },
    probe_status: 'skipped',
    discovery_applied: false,
    ...overrides,
  };
}

describe('selectSmtpSetupSettings', () => {
  it('prefers backend recommended settings', () => {
    const result = selectSmtpSetupSettings(analysis({
      action: {
        action: 'show_password',
        message_ru: '',
        instructions: [],
        recommended_settings: settings,
      },
      probe: {
        ...settings,
        host: 'probe.example.ru',
        reachable: true,
      },
    }));

    expect(result).toEqual(settings);
  });

  it('falls back to a reachable or discovered SMTP configuration', () => {
    const probe = {
      ...settings,
      host: 'smtp.corporate.ru',
      reachable: true,
    };
    expect(selectSmtpSetupSettings(analysis({ probe }))).toEqual(probe);

    const discovered = { ...settings, host: 'smtp.discovered.ru' };
    expect(selectSmtpSetupSettings(analysis({ discoveries: [discovered] }))).toEqual(discovered);
  });

  it('returns null when manual configuration is required', () => {
    expect(selectSmtpSetupSettings(analysis())).toBeNull();
  });
});

describe('smtpSetupSecurity', () => {
  it('maps transport flags to the form value', () => {
    expect(smtpSetupSecurity(settings)).toBe('tls');
    expect(smtpSetupSecurity({ ...settings, use_ssl: false, use_starttls: true })).toBe('starttls');
    expect(smtpSetupSecurity({ ...settings, use_ssl: false, use_starttls: false })).toBe('none');
  });
});

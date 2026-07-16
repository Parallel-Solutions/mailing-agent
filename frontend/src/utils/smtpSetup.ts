import type { SmtpSetupAnalysis, SmtpSetupSettings } from '@/api/connections';

export function selectSmtpSetupSettings(
  analysis: SmtpSetupAnalysis,
): SmtpSetupSettings | null {
  const recommended = analysis.action.recommended_settings;
  if (recommended?.host) return recommended;
  if (analysis.probe?.host) return analysis.probe;
  return analysis.discoveries.find((item) => Boolean(item.host)) || null;
}

export function smtpSetupSecurity(settings: SmtpSetupSettings): 'none' | 'tls' | 'starttls' {
  if (settings.use_ssl) return 'tls';
  return settings.use_starttls ? 'starttls' : 'none';
}

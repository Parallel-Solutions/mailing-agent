const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test((value || '').trim());
}

export function validateCampaignBasics(input: {
  name?: string;
  mail_subject?: string;
  send_scenario?: string;
}): string[] {
  const errors: string[] = [];
  if (!(input.name || '').trim()) errors.push('Укажите название рассылки');
  if (!(input.mail_subject || '').trim()) errors.push('Укажите тему письма');
  if (
    input.send_scenario &&
    !['consent_then_materials', 'materials_now', 'email_chain'].includes(input.send_scenario)
  ) {
    errors.push('Некорректный сценарий отправки');
  }
  return errors;
}

export function findDuplicateEmails(emails: string[]): string[] {
  const seen = new Set<string>();
  const dups = new Set<string>();
  for (const raw of emails) {
    const email = raw.trim().toLowerCase();
    if (!email) continue;
    if (seen.has(email)) dups.add(email);
    seen.add(email);
  }
  return [...dups];
}

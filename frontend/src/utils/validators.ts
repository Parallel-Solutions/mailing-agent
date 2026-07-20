const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test((value || '').trim());
}

export function validateCampaignBasics(input: {
  name?: string;
  email_chain_id?: string | null;
}): string[] {
  const errors: string[] = [];
  if (!(input.name || '').trim()) errors.push('Укажите название рассылки');
  if (!input.email_chain_id) {
    errors.push('Выберите цепочку писем');
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

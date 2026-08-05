const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test((value || '').trim());
}

export function validateCampaignBasics(input: {
  name?: string;
  send_scenario?: string | null;
  email_chain_id?: string | null;
  company_id?: string | null;
  company_work_type_id?: string | null;
  draft_payload?: {
    company_id?: string;
    company_work_type_id?: string;
    email_chain?: {
      nodes?: unknown[];
    };
  };
}): string[] {
  const errors: string[] = [];
  const scenario = (input.send_scenario || 'consent_then_materials').trim();
  const hasEmbeddedEmailChain = Boolean(input.draft_payload?.email_chain?.nodes?.length);
  if (!(input.name || '').trim()) errors.push('Укажите название рассылки');
  if (
    scenario === 'email_chain'
    && !(input.email_chain_id || '').trim()
    && !hasEmbeddedEmailChain
  ) {
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

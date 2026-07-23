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
  };
}): string[] {
  const errors: string[] = [];
  const companyId = (input.company_id || input.draft_payload?.company_id || '').trim();
  const workTypeId = (input.company_work_type_id || input.draft_payload?.company_work_type_id || '').trim();
  const scenario = (input.send_scenario || 'email_chain').trim();
  if (!(input.name || '').trim()) errors.push('Укажите название рассылки');
  if (scenario === 'email_chain' && !(input.email_chain_id || '').trim()) {
    errors.push('Выберите цепочку писем');
  }
  if (!companyId) errors.push('Выберите компанию');
  if (!workTypeId) errors.push('Выберите вид работ');
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

type ValidationRecipient = {
  validation_status?: string;
  extra?: Record<string, unknown>;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function emailValidationReason(recipient: ValidationRecipient): string {
  const validation = asRecord(recipient.extra?.email_validation);
  const candidates = Array.isArray(validation.candidates) ? validation.candidates : [];
  const status = String(recipient.validation_status || '').trim().toLowerCase();
  const records = candidates.map(asRecord);
  const matching = records.find((item) => String(item.status || '').toLowerCase() === status);
  const candidate = matching || records.find((item) => String(item.reason || '').trim());
  return String(candidate?.reason || '').trim();
}

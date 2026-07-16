/**
 * Mailpit HTTP API helpers with polling (no fixed sleep).
 * API docs: https://mailpit.axllent.org/docs/api-v1/
 */

export type MailpitAddress = { Address?: string; Name?: string };

export type MailpitMessageSummary = {
  ID: string;
  MessageID?: string;
  From?: MailpitAddress;
  To?: MailpitAddress[];
  Subject?: string;
  Created?: string;
  Attachments?: number;
  Size?: number;
};

export type MailpitAttachment = {
  PartID?: string;
  FileName?: string;
  ContentType?: string;
  Size?: number;
};

export type MailpitMessage = MailpitMessageSummary & {
  Text?: string;
  HTML?: string;
  Attachments?: MailpitAttachment[] | number;
};

const MAILPIT_URL = (process.env.MAILPIT_API_URL || 'http://mailpit:8025').replace(/\/$/, '');

async function mailpitFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${MAILPIT_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`Mailpit ${init?.method || 'GET'} ${path} → HTTP ${response.status}: ${body.slice(0, 300)}`);
  }
  return response;
}

export async function mailpitDeleteAll(): Promise<void> {
  await mailpitFetch('/api/v1/messages', { method: 'DELETE' });
}

export async function mailpitListMessages(): Promise<MailpitMessageSummary[]> {
  const response = await mailpitFetch('/api/v1/messages');
  const data = (await response.json()) as { messages?: MailpitMessageSummary[] };
  return data.messages || [];
}

export async function mailpitGetMessage(id: string): Promise<MailpitMessage> {
  const response = await mailpitFetch(`/api/v1/message/${id}`);
  return (await response.json()) as MailpitMessage;
}

export async function mailpitGetAttachment(
  messageId: string,
  partId: string,
): Promise<{ bytes: ArrayBuffer; contentType: string }> {
  const response = await mailpitFetch(`/api/v1/message/${messageId}/part/${partId}`);
  return {
    bytes: await response.arrayBuffer(),
    contentType: response.headers.get('content-type') || 'application/octet-stream',
  };
}

export type WaitForMessageOptions = {
  timeoutMs?: number;
  intervalMs?: number;
  subjectIncludes?: string;
  toIncludes?: string;
  fromIncludes?: string;
};

export async function mailpitWaitForMessage(
  predicate?: (msg: MailpitMessageSummary) => boolean,
  options: WaitForMessageOptions = {},
): Promise<MailpitMessage> {
  const timeoutMs = options.timeoutMs ?? 45_000;
  const intervalMs = options.intervalMs ?? 1_000;
  const started = Date.now();
  let lastCount = 0;

  while (Date.now() - started < timeoutMs) {
    const messages = await mailpitListMessages();
    lastCount = messages.length;
    const match = messages.find((msg) => {
      if (options.subjectIncludes && !(msg.Subject || '').includes(options.subjectIncludes)) {
        return false;
      }
      if (options.toIncludes) {
        const to = (msg.To || []).map((t) => t.Address || '').join(',');
        if (!to.toLowerCase().includes(options.toIncludes.toLowerCase())) {
          return false;
        }
      }
      if (options.fromIncludes) {
        const from = msg.From?.Address || '';
        if (!from.toLowerCase().includes(options.fromIncludes.toLowerCase())) {
          return false;
        }
      }
      return predicate ? predicate(msg) : true;
    });
    if (match) {
      return mailpitGetMessage(match.ID);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }

  throw new Error(
    `Mailpit: no matching message within ${timeoutMs}ms ` +
      `(messages=${lastCount}, subjectIncludes=${options.subjectIncludes || '-'}, ` +
      `toIncludes=${options.toIncludes || '-'})`,
  );
}

export function extractFirstHttpUrl(text: string): string | null {
  const match = text.match(/https?:\/\/[^\s"'<>]+/i);
  return match ? match[0].replace(/[),.;]+$/, '') : null;
}

export { MAILPIT_URL };

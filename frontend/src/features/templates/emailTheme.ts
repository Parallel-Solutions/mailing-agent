export const EMAIL_THEME = {
  primary: '#236348',
  primaryDark: '#174d38',
  accent: '#2d8a5e',
  text: '#303633',
  textSecondary: '#495057',
  textMuted: '#6c757d',
  textLight: '#868e96',
  bg: '#f4f6f5',
  bgCard: '#ffffff',
  bgAccent: '#eef4f1',
  border: '#dee2e6',
  fontStack: "'Segoe UI', Arial, sans-serif",
  maxWidth: '600px',
} as const;

export function simpleEmailWrapper(content: string): string {
  const t = EMAIL_THEME;
  return (
    `<div style="font-family:${t.fontStack};max-width:${t.maxWidth};line-height:1.6;color:${t.text}">`
    + content
    + '</div>'
  );
}

export function simpleGreeting(text: string): string {
  return `<p style="margin:0 0 16px;font-size:16px;font-weight:600;color:${EMAIL_THEME.text}">${text}</p>`;
}

export function simpleParagraph(text: string): string {
  return `<p style="margin:0 0 16px;font-size:15px;color:${EMAIL_THEME.text}">${text}</p>`;
}

export function simpleMuted(text: string): string {
  return `<p style="margin:0;font-size:13px;color:${EMAIL_THEME.textMuted}">${text}</p>`;
}

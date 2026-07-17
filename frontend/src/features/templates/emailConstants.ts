import { EMAIL_THEME } from './emailTheme';

export type EditorVariable = { name: string; label: string; source: string };

export const EMAIL_VARIABLES: EditorVariable[] = [
  { name: 'company', label: 'Компания', source: 'Получатель' },
  { name: 'contact_name', label: 'Контактное лицо', source: 'Получатель' },
  { name: 'email', label: 'Email', source: 'Получатель' },
  { name: 'region', label: 'Регион', source: 'Получатель' },
  { name: 'campaign_name', label: 'Название рассылки', source: 'Рассылка' },
];

export const SAMPLE_EMAIL_VALUES: Record<string, string> = {
  company: 'ООО «Вектор»',
  contact_name: 'Анна Сергеевна',
  email: 'anna@vector.ru',
  region: 'Московская область',
  campaign_name: 'КП — июль 2026',
};

export const DEFAULT_VISUAL_EMAIL_HTML = (
  `<table width="600" style="width:100%;max-width:${EMAIL_THEME.maxWidth};margin:0 auto;font-family:${EMAIL_THEME.fontStack};background:${EMAIL_THEME.bgCard}">`
  + `<tr><td style="background:${EMAIL_THEME.primary};color:#fff;padding:24px 32px;font-size:20px;font-weight:700">`
  + 'Ваше письмо'
  + '</td></tr>'
  + `<tr><td style="padding:32px;color:${EMAIL_THEME.text};line-height:1.6;font-size:15px">`
  + '<p style="margin:0 0 16px">Здравствуйте, {{contact_name}}!</p>'
  + '<p style="margin:0">Начните верстать письмо — перетащите блоки слева.</p>'
  + '</td></tr>'
  + `<tr><td style="background:${EMAIL_THEME.bg};padding:20px 32px;color:${EMAIL_THEME.textMuted};font-size:12px;text-align:center">`
  + '© {{campaign_name}}'
  + '</td></tr>'
  + '</table>'
);

import { EMAIL_THEME } from './emailTheme';

export type EditorVariable = { name: string; label: string; source: string };

export const EMAIL_VARIABLES: EditorVariable[] = [
  { name: 'company', label: 'Компания', source: 'Получатель' },
  { name: 'contact_name', label: 'Контактное лицо', source: 'Получатель' },
  { name: 'Имя', label: 'Имя (из ФИО)', source: 'Получатель' },
  { name: 'Отчество', label: 'Отчество (из ФИО)', source: 'Получатель' },
  { name: 'email', label: 'Email', source: 'Получатель' },
  { name: 'region', label: 'Регион', source: 'Получатель' },
  { name: 'campaign_name', label: 'Название рассылки', source: 'Рассылка' },
  { name: 'DATE', label: 'Дата', source: 'Система' },
  { name: 'current_date', label: 'Текущая дата', source: 'Система' },
  { name: 'VALID_UNTIL', label: 'Срок действия', source: 'Система' },
  { name: 'OUTGOING_NUMBER', label: 'Исходящий номер', source: 'Система' },
  { name: 'DOCUMENT_ID', label: 'Номер документа', source: 'Система' },
];

export const SAMPLE_EMAIL_VALUES: Record<string, string> = {
  company: 'ООО «Вектор»',
  contact_name: 'Анна Сергеевна',
  Имя: 'Анна',
  Отчество: 'Сергеевна',
  email: 'anna@vector.ru',
  region: 'Московская область',
  campaign_name: 'КП — июль 2026',
  DATE: '21.07.2026',
  current_date: '21.07.2026',
  VALID_UNTIL: '20.08.2026',
  OUTGOING_NUMBER: '101',
  DOCUMENT_ID: '000',
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
  + '</table>'
);

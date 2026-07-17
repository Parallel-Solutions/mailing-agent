export type User = {
  username: string;
  role?: string;
  tenant_id?: string;
};

export type Campaign = {
  id: string;
  name: string;
  status: string;
  work_type?: string;
  document_mode?: string;
  mail_subject?: string;
  description?: string;
  send_scenario?: string;
  tags?: string[];
  internal_comment?: string;
  smtp_mailbox_id?: string | null;
  transport?: string;
  email_template_id?: string | null;
  kp_template_id?: string | null;
  contract_template_id?: string | null;
  audience_id?: string | null;
  job_id?: string | null;
  sent_count?: number;
  total_count?: number;
  error_count?: number;
  progress?: number;
  draft_payload?: Record<string, unknown>;
  launched_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type CampaignList = { items: Campaign[]; total: number };

export type ActiveSending = {
  campaign_id: string;
  name: string;
  status: string;
  sent_count: number;
  total_count: number;
  remaining: number;
  queued_batches: number;
  sending_now: number;
  next_batch_size: number;
  next_batch_at?: string | null;
  batch_size: number;
  interval_seconds: number;
  max_per_hour: number;
  max_per_day: number;
  progress: number;
} | null;

export type Recipient = {
  id: number;
  company: string;
  contact_name: string;
  email: string;
  email_fallback?: string;
  region?: string;
  source?: string;
  validation_status?: string;
  excluded?: boolean;
  send_status?: string;
  last_error?: string | null;
};

export type Schedule = {
  send_immediately: boolean;
  start_at?: string | null;
  timezone: string;
  weekdays: number[];
  time_windows: { start: string; end: string }[];
  batch_size: number;
  interval_seconds: number;
  pause_between_messages_ms: number;
  max_per_hour: number;
  max_per_day: number;
  on_error: string;
  max_retries: number;
  preview?: SchedulePreview;
};

export type SchedulePreview = {
  batch_count: number;
  total_recipients: number;
  first_batch_at?: string | null;
  estimated_completion_at?: string | null;
  batches: { batch_index: number; scheduled_at: string; size: number }[];
  per_day: Record<string, number>;
  next_send_at?: string | null;
};

export type CampaignGeneration = {
  campaign_id?: string;
  job_id?: string;
  document_mode?: string;
  work_type?: string;
  prepared: boolean;
  stale: boolean;
  ready: boolean;
  status: string;
  manifest?: {
    recipient_count?: number;
    prepared_at?: string;
    templates?: { kind: string; filename: string; stored_as: string }[];
  };
  documents?: {
    status?: string;
    progress_percent?: number;
    stage_text?: string;
    summary_text?: string;
    output_ready?: boolean;
    output_file_count?: number;
    error?: string;
  };
};

export type DocumentTemplatePreview = {
  status: string;
  pdf_url?: string;
  docx_url?: string;
  row_label?: string;
  failed_message?: string;
};

export type Batch = {
  id: string;
  batch_index: number;
  scheduled_at?: string | null;
  size: number;
  sent_count: number;
  error_count: number;
  remaining: number;
  status: string;
  error?: string | null;
};

export type PdfEditorField = {
  id: string;
  page: number;
  variable: string;
  label: string;
  source_text: string;
  value: string;
  x: number;
  y: number;
  width: number;
  height: number;
  text_x: number;
  baseline: number;
  font_size: number;
  bold: boolean;
  text_color: string;
  background: string;
};

export type PdfEditorState = {
  page_count: number;
  pages: { index: number; width: number; height: number }[];
  fields: PdfEditorField[];
};
export type Template = {
  id: string;
  name: string;
  template_type: string;
  status: string;
  tags?: string[];
  version?: {
    id: string;
    subject: string;
    body_html: string;
    body_text: string;
    variables: { name: string; source: string; label: string }[];
    version_number?: number;
    storage_key?: string | null;
    filename?: string | null;
    rendered_pdf_storage_key?: string | null;
    rendered_pdf_filename?: string | null;
    editor_state?: PdfEditorState | null;
    artifacts?: {
      source?: { filename?: string | null; storage_key?: string | null } | null;
      delivery_pdf?: { filename?: string | null; storage_key?: string | null } | null;
    };
    created_at?: string | null;
  };
};

export type Audience = {
  id: string;
  name: string;
  source: string;
  member_count: number;
  quality_score: number;
  updated_at?: string | null;
};

export type Profile = {
  username: string;
  display_name: string;
  email: string;
  company: string;
  job_title: string;
  signature: string;
  timezone: string;
  mailing_defaults: Record<string, unknown>;
  notifications: Record<string, unknown>;
};

export type DeliveryConnection = {
  id: string;
  transport: 'smtp' | 'rusender' | 'mailopost';
  email: string;
  sender_name?: string;
  provider?: string;
  host?: string;
  port?: number | null;
  api_base_url?: string;
  status: string;
  is_default?: boolean;
  last_error?: string | null;
  use_ssl?: boolean | null;
  use_starttls?: boolean | null;
  has_secret?: boolean;
};

export type SmtpMailbox = DeliveryConnection;

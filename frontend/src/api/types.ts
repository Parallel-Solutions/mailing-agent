export type OnboardingStatus = 'active' | 'paused' | 'dismissed' | 'completed';

export type OnboardingState = {
  version: number;
  status: OnboardingStatus;
  current_step: number;
  completed_steps: string[];
  step_count: number;
  available: boolean;
  paused_at: string | null;
  dismissed_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
};

export type OnboardingUpdate = {
  status: OnboardingStatus;
  current_step?: number;
  completed_steps?: string[];
};

export type CompanyAccess = {
  company_id: string;
  company_name: string;
  access_level: 'view' | 'manage';
};

export type User = {
  username: string;
  role?: string;
  tenant_id?: string;
  company_id?: string | null;
  company_role?: 'company_admin' | 'member' | null;
  company?: { id: string; name: string; logo_url?: string | null };
  company_accesses?: CompanyAccess[];
};

export type Company = {
  id: string;
  name: string;
  phone?: string;
  contact_person_name?: string;
  logo_url?: string | null;
  member_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
};

export type CampaignDraftPayload = {
  company_id?: string;
  company_work_type_id?: string;
  work_type_name?: string;
  mapping_confirmed?: boolean;
  mapping_confirmed_at?: string | null;
  variable_mapping?: Record<string, string>;
  email_body?: string;
  price_total?: string | number;
  valid_until_days?: number;
  [key: string]: unknown;
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
  connection_ids?: string[];
  transport?: string;
  email_template_id?: string | null;
  kp_template_id?: string | null;
  contract_template_id?: string | null;
  audience_id?: string | null;
  email_chain_id?: string | null;
  job_id?: string | null;
  company_id?: string;
  company_work_type_id?: string;
  work_type_name?: string;
  sent_count?: number;
  total_count?: number;
  error_count?: number;
  success_count?: number;
  skipped_count?: number;
  failed_recipient_count?: number;
  processed_count?: number;
  pending_count?: number;
  attempt_count?: number;
  attempt_error_count?: number;
  success_rate?: number;
  allowed_actions?: string[];
  layout_error_count?: number;
  progress?: number;
  draft_payload?: CampaignDraftPayload;
  launched_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ChainNodeKind = 'email' | 'link';
export type ChainLinkKind = 'custom' | 'unsubscribe' | 'subscribe';

export type EmailChainNode = {
  id: string;
  name: string;
  kind?: ChainNodeKind;
  email_template_id?: string | null;
  document_template_ids?: string[];
  consent_on_click?: boolean;
  link_kind?: ChainLinkKind;
  link_url?: string | null;
};

export type EmailChainEdge = {
  id: string;
  source_id: string;
  target_id: string;
  button_label: string;
};

export type EmailChain = {
  version: number;
  root_node_id: string;
  nodes: EmailChainNode[];
  edges: EmailChainEdge[];
};

export type EmailChainState = {
  chain: EmailChain;
  validation: { ok: boolean; errors: string[]; warnings: string[] };
  published: boolean;
};

export type EmailChainStats = {
  edges: { edge_id: string; tokens: number; clicks: number }[];
  consents?: {
    subscribe: { count: number };
    unsubscribe: { count: number };
    materials_request?: { count: number };
  };
};

export type EmailChainPreviewAttachment = {
  template_id: string;
  filename: string;
  has_content: boolean;
  error?: string;
  content_type?: string;
  text_preview?: string;
  issues?: TemplatePlaceholderIssue[];
};

export type TemplateReviewKind =
  | 'artifact'
  | 'malformed'
  | 'unresolved'
  | 'punctuation'
  | 'grammar'
  | 'case';

export type TemplateReviewSeverity = 'error' | 'warning' | 'info';

export type TemplatePlaceholderIssue = {
  token: string;
  kind: TemplateReviewKind;
  field: string;
  severity?: TemplateReviewSeverity;
  message?: string;
  fragment?: string;
  template_id?: string | null;
  suggestion?: string;
  blocking?: boolean;
};

export type TemplateValidationIssue = {
  template_id?: string | null;
  template_name?: string;
  token: string;
  kind: TemplateReviewKind;
  field?: string;
  severity?: TemplateReviewSeverity;
  message?: string;
  fragment?: string;
  suggestion?: string;
  blocking?: boolean;
};

export type CampaignValidateResponse = {
  ok: boolean;
  errors: string[];
  warnings: string[];
  template_issues?: TemplateValidationIssue[];
  active_recipients: number;
  excluded_recipients: number;
  mapping_confirmed?: boolean;
  email_validation?: Record<string, number>;
};

export type EmailValidationRun = {
  id: string;
  scope_type: 'campaign' | 'audience';
  scope_id: string;
  revision: string;
  provider: string;
  status: 'not_started' | 'queued' | 'running' | 'completed' | 'failed' | 'stale';
  total_count: number;
  processed_count: number;
  valid_count: number;
  invalid_count: number;
  unknown_count: number;
  cached_count: number;
  progress_percent: number;
  task_id: string;
  error: string;
  started_at: string;
  completed_at: string;
  created_at: string;
  enabled: boolean;
  recipient_counts: Record<string, number>;
};

export type EmailChainPreviewItem = {
  node_id: string;
  node_name: string;
  subject: string;
  body_html: string;
  email_template_id?: string | null;
  attachments: EmailChainPreviewAttachment[];
  issues?: TemplatePlaceholderIssue[];
};
export type EmailChainPreviewResponse = {
  recipient: {
    id: number;
    company?: string;
    contact_name?: string;
    email?: string;
  };
  items: EmailChainPreviewItem[];
};

export type DocumentLayoutReviewItem = {
  template_id: string;
  active_version_id: string;
  template_name: string;
  filename: string;
  status:
    | 'candidate'
    | 'already_applied'
    | 'preview_only'
    | 'fallback'
    | 'skipped'
    | 'error';
  message: string;
  changes: string[];
  before_image?: string;
  after_image?: string;
  can_apply: boolean;
  layout_version?: string;
  fallback_used?: boolean;
  issues?: Array<{
    page: number;
    source_text: string;
    variables: string[];
    rendered_value: string;
    reason: string;
    message: string;
  }>;
};

export type CampaignDocumentLayoutReview = {
  campaign_id: string;
  recipient: {
    id: number;
    company?: string;
    contact_name?: string;
  };
  estimate_seconds: number;
  documents: DocumentLayoutReviewItem[];
};

export type CampaignDocumentLayoutApplyResult = {
  template_id: string;
  template_version_id: string;
  layout_version: string;
  changes: string[];
};

export type CampaignList = { items: Campaign[]; total: number };

export type ActiveSending = {
  campaign_id: string;
  name: string;
  status: string;
  sent_count: number;
  total_count: number;
  processed_count: number;
  skipped_count: number;
  failed_recipient_count: number;
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
  success_rate: number;
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
  extra?: Record<string, unknown>;
  send_status?: string;
  last_error?: string | null;
  layout_error_code?: string | null;
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
    restart_locked?: boolean;
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
  processed_count?: number;
  skipped_count?: number;
  failed_recipient_count?: number;
  remaining: number;
  status: string;
  task_status?: string | null;
  queue_position?: number | null;
  is_current?: boolean;
  available_at?: string | null;
  attempt?: number;
  max_attempts?: number;
  wait_reason?: string;
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

export type ImportRefinementState = {
  available?: boolean;
  selected_source?: string;
  stop_reason?: string;
  best_score?: number;
  rounds?: number;
  spent_usd?: number;
  source?: string;
  qa?: {
    winner?: string;
    winner_score?: number;
    candidate_scores?: Record<string, number>;
    regenerated?: boolean;
  };
};

export type EmailEditorState = {
  email_format?: 'simple' | 'visual';
  grapesjs_project?: Record<string, unknown>;
  imported_layout?: boolean;
  import_source?: string;
  import_as_draft?: boolean;
  import_refinement?: ImportRefinementState;
  brand?: { primaryColor?: string; logoUrl?: string };
  fields?: PdfEditorField[];
};

export type TemplateEditorState = PdfEditorState | EmailEditorState;
export type Template = {
  id: string;
  name: string;
  template_type: string;
  status: string;
  is_template?: boolean;
  attachment_output_format?: 'original' | 'pdf';
  enforce_one_page?: boolean;
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
    editor_state?: TemplateEditorState | null;

    artifacts?: {
      source?: { filename?: string | null; storage_key?: string | null } | null;
      delivery_pdf?: { filename?: string | null; storage_key?: string | null } | null;
    };
    created_at?: string | null;
  };
};

export type FontAsset = {
  id: string;
  family: string;
  family_normalized: string;
  subfamily: string;
  weight: number;
  italic: boolean;
  postscript_name?: string;
  source: 'upload' | 'google_fonts' | string;
  sha256: string;
  size_bytes: number;
  original_filename: string;
  license_type?: string;
  license_url?: string;
  embedding_permissions: 'installable' | 'editable' | 'preview_print' | 'restricted' | 'unknown' | string;
  glyph_coverage?: {
    glyph_count?: number;
    latin?: boolean;
    cyrillic?: boolean;
    digits?: boolean;
  };
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type TemplateFontRequirement = {
  family: string;
  family_normalized: string;
  weight: number;
  italic: boolean;
  source_parts?: string[];
  document_has_embedded_fonts?: boolean;
  status: 'resolved' | 'system' | 'missing';
  source: string;
  font_asset?: FontAsset | null;
};

export type TemplateFontsResult = {
  template_id: string;
  version_id: string;
  requirements: TemplateFontRequirement[];
  missing_count: number;
  ready: boolean;
  font_pack_hash: string;
  attempted_families?: string[];
  downloaded_fonts?: FontAsset[];
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
  sending_key_id?: number | null;
  auth_method?: string;
  oauth_provider?: string;
  status: string;
  is_default?: boolean;
  last_error?: string | null;
  use_ssl?: boolean | null;
  use_starttls?: boolean | null;
  save_sent_copy?: boolean;
  imap_host?: string;
  imap_port?: number | null;
  imap_use_ssl?: boolean | null;
  imap_use_starttls?: boolean | null;
  imap_username?: string;
  imap_sent_folder?: string;
  imap_password_configured?: boolean;
  has_secret?: boolean;
  max_per_hour?: number;
  max_per_day?: number;
  delivery_guard_enabled?: boolean;
  delivery_error_rate_threshold?: number;
  delivery_error_window_minutes?: number;
  delivery_error_min_samples?: number;
  delivery_error_critical_count?: number;
  delivery_error_action?: 'throttle' | 'disable' | 'warmup';
  delivery_throttled_max_per_hour?: number;
  warmup_recipients?: string[];
  warmup_percent_of_errors?: number;
  delivery_guard?: {
    enabled: boolean;
    scope: 'connection' | 'sending_key';
    scope_id: string;
    state: 'normal' | 'throttled' | 'disabled' | 'warmup';
    reason: string;
    error_rate_threshold: number;
    tracking_mode: 'since_reset';
    monitoring_started_at: string;
    min_samples: number;
    action: 'throttle' | 'disable' | 'warmup';
    throttled_max_per_hour: number;
    terminal_count: number;
    error_count: number;
    error_rate: number;
    effective_max_per_hour: number;
    triggered_at: string;
    last_error_at: string;
    warmup_recipients: string[];
    warmup_percent_of_errors: number;
    warmup_task_id: string;
    warmup_status: string;
    warmup_sent_count: number;
    warmup_error_count: number;
    warmup_started_at: string;
    warmup_completed_at: string;
  };
};

export type ConnectionWarmupRecipient = {
  id: string;
  email: string;
  provider: 'gmail' | 'yandex' | 'mailru' | 'outlook' | 'other';
  status: 'active' | 'disabled';
  messages_per_day: number;
  sent_count: number;
  error_count: number;
  last_sent_at: string;
  last_error: string;
  created_at: string;
};

export type ConnectionWarmupCheck = {
  key: string;
  status: 'pass' | 'warning' | 'fail';
  detail: string;
};

export type ConnectionWarmupProgram = {
  id: string;
  connection_id: string;
  smtp_connection_id: string;
  smtp_connection_email: string;
  smtp_connection_status: string;
  sending_transport: 'smtp' | 'rusender';
  status: 'draft' | 'running' | 'paused' | 'completed' | 'blocked' | 'cancelled';
  timezone: string;
  daily_start_time: string;
  daily_end_time: string;
  pause_campaigns_during_warmup: boolean;
  warmup_mode: 'growth' | 'fixed_daily';
  duration_days: number;
  max_growth_percent: number;
  current_day: number;
  run_number: number;
  daily_plan: number[];
  effective_daily_plan: number[];
  recipients_consent_confirmed: boolean;
  recipients_consent_confirmed_at: string;
  subject_templates: string[];
  body_templates: string[];
  pause_reason: string;
  suspended_by_campaign: boolean;
  suspended_at: string;
  active_campaigns: Array<{ id: string; name: string }>;
  diagnostics_status: 'not_checked' | 'ready' | 'warning' | 'blocked';
  diagnostics: {
    checked_at?: string;
    from_domain?: string;
    checks?: ConnectionWarmupCheck[];
    outbound_ips?: string[];
  };
  recipients: ConnectionWarmupRecipient[];
  recipient_count: number;
  active_recipient_count: number;
  delivery_counts: Record<string, number>;
  started_at: string;
  paused_at: string;
  completed_at: string;
};
export type SmtpMailbox = DeliveryConnection;

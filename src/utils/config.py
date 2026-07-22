from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class SecurityConfigurationError(RuntimeError):
    """Raised when the service would start with an unsafe security config."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    # СБИС
    sbis_login: str = ""
    sbis_password: str = ""

    app_username: str = "admin"
    app_password: str = ""
    app_users: str = ""
    app_admin_tenant_id: str = "admin"
    app_session_ttl_days: int = 7
    app_allow_registration: bool = False
    # JSON map: {"<mcp-token>": "<username>"} for Authorization: Bearer MCP auth.
    mailing_agent_mcp_tokens: str = ""
    frontend_dist_dir: str = ""
    seed_demo_data_on_startup: bool = False

    sender_domain_limits_json: str = (
        '{"gmail.com":60,"mail.ru":40,"yandex.ru":40,"outlook.com":40,"other":30}'
    )
    sender_domain_limit_window_seconds: int = 3600

    send_guard_window_seconds: int = 3600
    send_guard_complaint_rate_threshold: float = 0.001
    send_guard_api_error_rate_threshold: float = 0.05
    send_guard_min_samples: int = 20
    suppression_soft_bounce_ttl_days: int = 7

    # PostgreSQL
    database_url: str = "postgresql+psycopg://mailing:mailing@postgres:5432/mailing"

    # S3 / MinIO
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "mailing-agent"
    s3_region: str = "us-east-1"
    s3_use_path_style: bool = True

    # Redis
    redis_url: str = "redis://redis:6379"

    # Local workspace (tmp) for document generation
    workspace_dir: str = "/app/tmp"

    # Приложение
    app_host: str = "0.0.0.0"
    app_port: int = 9806
    public_base_url: str = "https://31-130-150-209.sslip.io"
    upload_data_max_bytes: int = 25 * 1024 * 1024
    upload_template_max_bytes: int = 10 * 1024 * 1024
    municipality_upload_auto_verify_max_bytes: int = 4 * 1024 * 1024

    openai_api_key: str = ""
    openai_base_url: str = "https://api.vsellm.ru/v1"
    template_ai_models: str = "gpt-4o-mini,gpt-4.1"
    template_import_max_rounds: int = 10
    template_import_max_cost_usd: float = 1.5
    template_import_target_similarity: float = 0.97
    case_agent_model: str = "gpt-4o"
    enable_case_agent: bool = True
    case_agent_mode: str = "auto_fix"
    orchestrator_mode: str = "agentic"

    smtp_sender_email: str = ""
    smtp_sender_password: str = ""
    smtp_credentials_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_use_starttls: bool = False
    smtp_allow_real_send: bool = False
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    microsoft_oauth_tenant: str = "common"
    smtp_oauth_redirect_base: str = "http://localhost:9806"
    imap_host: str = "imap.mail.ru"
    imap_port: int = 993
    imap_use_ssl: bool = True
    imap_sent_folder: str = "Отправленные"
    smtp_save_sent_copy: bool = True
    sender_default_batch_size: int = 25
    sender_max_batch_size: int = 100
    sender_delay_seconds: float = 60.0
    sender_delay_min_seconds: float = 0.0
    sender_delay_max_seconds: float = 0.0
    sender_transport: str = "smtp"
    sender_unisender_concurrency: int = 1
    email_validation_mode: str = "domain"
    email_validation_timeout_seconds: float = 3.0
    smtpbz_api_key: str = ""
    smtpbz_api_base_url: str = "https://api.smtp.bz/v1"
    documents_worker_max_processes: int = 1
    sender_worker_max_processes: int = 1
    user_worker_max_processes_per_task: int = 1
    user_inprocess_max_tasks: int = 1
    documents_worker_timeout_seconds: int = 21600
    sender_worker_timeout_seconds: int = 0
    background_queue_enabled: bool = True
    background_queue_poll_seconds: float = 1.0
    background_queue_lease_seconds: int = 7200
    background_queue_heartbeat_seconds: int = 30
    background_queue_retry_base_seconds: int = 30
    background_queue_max_attempts: int = 3
    background_queue_shutdown_grace_seconds: int = 20
    unisender_api_key: str = ""
    unisender_api_base_url: str = "https://goapi.unisender.ru/ru/transactional/api/v1"
    unisender_sender_name: str = "ООО «ПР»"
    unisender_sender_email: str = ""
    unisender_list_id: int = 1
    unisender_webhook_secret: str = ""
    unisender_webhook_token: str = ""
    rusender_api_key: str = ""
    rusender_api_base_url: str = "https://api.rusender.ru/api/v1"
    rusender_sender_name: str = "ООО «ПР»"
    rusender_sender_email: str = ""
    rusender_webhook_secret: str = ""
    rusender_webhook_token: str = ""
    mailopost_api_token: str = ""
    mailopost_api_base_url: str = "https://api.mailopost.ru/v1"
    mailopost_sender_name: str = "ООО «ПР»"
    mailopost_sender_email: str = ""
    mailopost_webhook_secret: str = ""
    mailopost_webhook_token: str = ""
    webhook_max_body_bytes: int = 256 * 1024
    mail_signature_image_url: str = ""
    consent_token_ttl_hours: int = 720
    consent_materials_recovery_enabled: bool = True
    consent_materials_recovery_poll_seconds: int = 60
    consent_materials_recovery_batch_size: int = 25
    consent_materials_recovery_max_attempts: int = 3
    stats_cache_warm_enabled: bool = True
    stats_cache_warm_interval_seconds: int = 1200

    municipality_oktmo_lookup_enabled: bool = True
    municipality_oktmo_csv_path: str = ""
    municipality_oktmo_verify_ssl: bool = False
    municipality_official_sites_enabled: bool = True
    municipality_official_sites_verify_ssl: bool = True
    municipality_official_sites_timeout_seconds: float = 15.0
    municipality_minjust_lookup_enabled: bool = False

    inter_agent_handoffs_enabled: bool = False
    autonomous_workers_enabled: bool = False
    philologist_auto_run_enabled: bool = True
    autonomous_workers_poll_seconds: int = 5
    autonomous_task_timeout_seconds: int = 120
    autonomous_task_max_retries: int = 3

    # Парсер
    checko_api_key: str = ""
    tavily_api_key: str = ""
    parser_openai_api_key: str = ""
    parser_openai_base_url: str = "https://api.vsellm.ru/v1"
    parser_model: str = "gpt-4o"


def require_configured_app_password(settings_obj: Any) -> None:
    password = str(getattr(settings_obj, "app_password", "") or "").strip()
    if not password:
        raise SecurityConfigurationError(
            "APP_PASSWORD must be set to a non-empty value before starting the service."
        )


settings = Settings()

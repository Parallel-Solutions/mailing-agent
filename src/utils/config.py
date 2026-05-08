from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # СБИС
    sbis_login: str = ""
    sbis_password: str = ""

    app_username: str = "admin"
    app_password: str = "nngG!8c%Rm2UY"

    # Приложение
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    openai_api_key: str = "sk-PK3lARaSCCuZ6-fKKecF0w"
    openai_base_url: str = "https://api.vsellm.ru/v1"
    case_agent_model: str = "gpt-4o"
    enable_case_agent: bool = True
    case_agent_mode: str = "auto_fix"
    orchestrator_mode: str = "agentic"

    smtp_sender_email: str = ""
    smtp_sender_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_use_starttls: bool = False
    smtp_allow_real_send: bool = False
    imap_host: str = "imap.mail.ru"
    imap_port: int = 993
    imap_use_ssl: bool = True
    imap_sent_folder: str = "Отправленные"
    smtp_save_sent_copy: bool = True
    sender_default_batch_size: int = 25
    sender_max_batch_size: int = 100
    sender_delay_seconds: float = 30.0
    sender_transport: str = "smtp"
    unisender_api_key: str = ""
    unisender_api_base_url: str = "https://goapi.unisender.ru/ru/transactional/api/v1"
    unisender_sender_name: str = "ООО «ПР»"
    unisender_sender_email: str = ""
    unisender_list_id: int = 1

    inter_agent_handoffs_enabled: bool = False
    autonomous_workers_enabled: bool = False
    philologist_auto_run_enabled: bool = False
    autonomous_workers_poll_seconds: int = 5
    autonomous_task_timeout_seconds: int = 120
    autonomous_task_max_retries: int = 3

    # Парсер
    checko_api_key: str = ""
    tavily_api_key: str = ""
    parser_openai_api_key: str = ""
    parser_openai_base_url: str = "https://api.vsellm.ru/v1"
    parser_model: str = "gpt-4o"


settings = Settings()

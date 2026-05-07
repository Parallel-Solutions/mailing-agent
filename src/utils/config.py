from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Checko API
    checko_api_key: str = ""

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
    sender_default_batch_size: int = 25
    sender_max_batch_size: int = 100

    autonomous_workers_enabled: bool = True
    philologist_auto_run_enabled: bool = False
    autonomous_workers_poll_seconds: int = 5
    autonomous_task_timeout_seconds: int = 120
    autonomous_task_max_retries: int = 3


settings = Settings()

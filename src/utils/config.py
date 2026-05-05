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


settings = Settings()
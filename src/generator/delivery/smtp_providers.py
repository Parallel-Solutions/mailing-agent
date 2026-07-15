from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SmtpProviderPreset:
    id: str
    title: str
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool


PROVIDER_PRESETS: dict[str, SmtpProviderPreset] = {
    "gmail": SmtpProviderPreset("gmail", "Gmail", "smtp.gmail.com", 465, True, False),
    "outlook": SmtpProviderPreset("outlook", "Outlook / Microsoft 365", "smtp.office365.com", 587, False, True),
    "yandex": SmtpProviderPreset("yandex", "Яндекс", "smtp.yandex.ru", 465, True, False),
    "mailru": SmtpProviderPreset("mailru", "Mail.ru", "smtp.mail.ru", 465, True, False),
    "custom": SmtpProviderPreset("custom", "Другой SMTP-сервер", "", 587, False, True),
}


def list_provider_presets() -> list[dict[str, str | int | bool]]:
    return [
        {
            "id": preset.id,
            "title": preset.title,
            "host": preset.host,
            "port": preset.port,
            "use_ssl": preset.use_ssl,
            "use_starttls": preset.use_starttls,
        }
        for preset in PROVIDER_PRESETS.values()
    ]


def resolve_provider_settings(
    provider: str,
    *,
    host: str = "",
    port: int | None = None,
    use_ssl: bool | None = None,
    use_starttls: bool | None = None,
) -> SmtpProviderPreset:
    normalized = str(provider or "custom").strip().lower() or "custom"
    preset = PROVIDER_PRESETS.get(normalized, PROVIDER_PRESETS["custom"])
    if normalized != "custom":
        return preset
    safe_host = str(host or "").strip()
    if not safe_host:
        raise ValueError("Укажите SMTP-сервер для пользовательского провайдера.")
    safe_port = int(port or preset.port or 587)
    return SmtpProviderPreset(
        id="custom",
        title=preset.title,
        host=safe_host,
        port=safe_port,
        use_ssl=bool(use_ssl) if use_ssl is not None else preset.use_ssl,
        use_starttls=bool(use_starttls) if use_starttls is not None else preset.use_starttls,
    )

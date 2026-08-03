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
    imap_host: str
    imap_port: int
    imap_use_ssl: bool
    imap_use_starttls: bool
    imap_sent_folder: str


PROVIDER_PRESETS: dict[str, SmtpProviderPreset] = {
    "gmail": SmtpProviderPreset(
        "gmail", "Gmail", "smtp.gmail.com", 587, False, True,
        "imap.gmail.com", 993, True, False, "[Gmail]/Sent Mail",
    ),
    "outlook": SmtpProviderPreset(
        "outlook", "Outlook / Microsoft 365", "smtp.office365.com", 587, False, True,
        "outlook.office365.com", 993, True, False, "Sent Items",
    ),
    "yandex": SmtpProviderPreset(
        "yandex", "Яндекс", "smtp.yandex.ru", 465, True, False,
        "imap.yandex.ru", 993, True, False, "Отправленные",
    ),
    "mailru": SmtpProviderPreset(
        "mailru", "Mail.ru", "smtp.mail.ru", 465, True, False,
        "imap.mail.ru", 993, True, False, "Отправленные",
    ),
    "custom": SmtpProviderPreset(
        "custom", "Другой SMTP-сервер", "", 587, False, True,
        "", 993, True, False, "",
    ),
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
            "imap_host": preset.imap_host,
            "imap_port": preset.imap_port,
            "imap_use_ssl": preset.imap_use_ssl,
            "imap_use_starttls": preset.imap_use_starttls,
            "imap_sent_folder": preset.imap_sent_folder,
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
    safe_host = str(host or "").strip()
    if normalized != "custom":
        if safe_host or port is not None or use_ssl is not None or use_starttls is not None:
            return SmtpProviderPreset(
                id=preset.id,
                title=preset.title,
                host=safe_host or preset.host,
                port=int(port if port is not None else preset.port),
                use_ssl=bool(use_ssl) if use_ssl is not None else preset.use_ssl,
                use_starttls=bool(use_starttls) if use_starttls is not None else preset.use_starttls,
                imap_host=preset.imap_host,
                imap_port=preset.imap_port,
                imap_use_ssl=preset.imap_use_ssl,
                imap_use_starttls=preset.imap_use_starttls,
                imap_sent_folder=preset.imap_sent_folder,
            )
        return preset
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
        imap_host=preset.imap_host,
        imap_port=preset.imap_port,
        imap_use_ssl=preset.imap_use_ssl,
        imap_use_starttls=preset.imap_use_starttls,
        imap_sent_folder=preset.imap_sent_folder,
    )

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.generator.generation.config_generator import _read_env_override
from src.generator.inflection.ai_case_agent import (
    OpenAI,
    _build_openai_http_client,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.utils.config import settings

SMTP_SETUP_AI_ENABLED = _read_env_override("SMTP_SETUP_AI_ENABLED", "1") == "1"

SETUP_ACTIONS = frozenset(
    {
        "apply_settings",
        "show_oauth",
        "show_app_password",
        "show_password",
        "show_manual",
        "retry_probe",
        "contact_admin",
    }
)

OAUTH_PROVIDERS = frozenset({"google", "microsoft"})

_KNOWN_PROVIDER_DOMAINS: dict[str, frozenset[str]] = {
    "gmail": frozenset({"gmail.com", "googlemail.com"}),
    "outlook": frozenset({"outlook.com", "hotmail.com", "live.com", "msn.com"}),
    "yandex": frozenset({"yandex.ru", "ya.ru", "yandex.com", "yandex.by", "yandex.kz"}),
    "mailru": frozenset({"mail.ru", "inbox.ru", "list.ru", "bk.ru"}),
}

_PROBE_UNREACHABLE_NOTE = (
    "Проверка портов из контейнера не удалась — это не мешает подключению."
)


@dataclass(frozen=True)
class SetupAction:
    action: str
    message_ru: str
    instructions: list[str]
    oauth_provider: str | None
    recommended_settings: dict[str, Any] | None
    ai_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "message_ru": self.message_ru,
            "instructions": list(self.instructions),
            "oauth_provider": self.oauth_provider,
            "recommended_settings": self.recommended_settings,
            "ai_used": self.ai_used,
        }


def advise_smtp_setup(context: dict[str, Any]) -> SetupAction:
    fallback = build_fallback_setup_action(context)
    if fallback.action in {"show_app_password", "show_oauth"}:
        # Provider already identified deterministically (gmail/outlook/yandex/mailru) —
        # we have a vetted, provider-specific flow, so the AI must not replace it with
        # a more generic (and potentially wrong, e.g. plain-password) suggestion.
        return fallback
    if not SMTP_SETUP_AI_ENABLED:
        return fallback
    client = _build_llm_client()
    if client is None:
        return fallback
    try:
        response = client.chat.completions.create(
            **_build_request_kwargs(context),
        )
        content = str(response.choices[0].message.content or "").strip()
        parsed = _parse_setup_action_json(content, context)
        if parsed is not None:
            return parsed
    except Exception:
        pass
    return fallback


def build_fallback_setup_action(context: dict[str, Any]) -> SetupAction:
    email = str(context.get("email") or "").strip().lower()
    domain = str(context.get("domain") or email.split("@")[-1]).strip().lower()
    provider_hint = str(context.get("provider_hint") or "").strip().lower()
    last_error = str(context.get("last_error") or "").strip().lower()
    probe = context.get("probe") or {}
    discoveries = context.get("discoveries") or []
    recommended = _resolve_recommended_settings(context)
    oauth_available_map = context.get("oauth_available") or {}
    oauth_available = bool(oauth_available_map)
    probe_reachable = bool(probe.get("reachable"))
    resolved_provider = _resolve_provider(provider_hint, domain, discoveries)
    probe_note = [] if probe_reachable else [_PROBE_UNREACHABLE_NOTE]

    if last_error and ("535" in last_error or "authentication" in last_error or "авториза" in last_error):
        if resolved_provider == "gmail":
            google_oauth = bool(oauth_available_map.get("google"))
            return SetupAction(
                action="show_oauth" if google_oauth else "show_app_password",
                message_ru="Не удалось войти. Для Gmail используйте пароль приложения или OAuth.",
                instructions=[
                    "Откройте страницу паролей приложений: https://myaccount.google.com/apppasswords",
                    "Войдите в аккаунт Google и подтвердите двухфакторную аутентификацию, если потребуется.",
                    "Создайте пароль приложения «Почта» и скопируйте 16-символьный код.",
                    "Вставьте пароль приложения в поле ниже без пробелов и нажмите «Проверить и подключить».",
                    *probe_note,
                ],
                oauth_provider="google" if google_oauth else None,
                recommended_settings=recommended,
            )
        if resolved_provider == "outlook":
            microsoft_oauth = bool(oauth_available_map.get("microsoft"))
            return SetupAction(
                action="show_oauth" if microsoft_oauth else "show_password",
                message_ru="Не удалось войти в Microsoft 365 / Outlook.",
                instructions=[
                    "Войдите через Microsoft или проверьте пароль.",
                    "Для корпоративных ящиков может потребоваться разрешение администратора.",
                    *probe_note,
                ],
                oauth_provider="microsoft" if microsoft_oauth else None,
                recommended_settings=recommended,
            )
        return SetupAction(
            action="show_app_password",
            message_ru="Не удалось войти. Проверьте пароль или используйте пароль приложения.",
            instructions=[
                "Убедитесь, что включён доступ по SMTP у провайдера.",
                "Если включена двухфакторная аутентификация — нужен пароль приложения.",
                *probe_note,
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    if resolved_provider == "gmail":
        google_oauth = bool(oauth_available_map.get("google"))
        return SetupAction(
            action="show_oauth" if google_oauth else "show_app_password",
            message_ru="Gmail готов к подключению. Рекомендуем OAuth или пароль приложения.",
            instructions=[
                "Нажмите «Войти через Google» или откройте https://myaccount.google.com/apppasswords",
                "Создайте пароль приложения «Почта» в настройках безопасности Google.",
                "Скопируйте 16-символьный пароль и вставьте его без пробелов в поле ниже.",
                "Обычный пароль от входа в Google не подходит для SMTP.",
                *probe_note,
            ],
            oauth_provider="google" if google_oauth else None,
            recommended_settings=recommended,
        )

    if resolved_provider == "outlook":
        microsoft_oauth = bool(oauth_available_map.get("microsoft"))
        return SetupAction(
            action="show_oauth" if microsoft_oauth else "show_password",
            message_ru="Outlook / Microsoft 365 готов к подключению.",
            instructions=[
                "Нажмите «Войти через Microsoft» или введите пароль почтового ящика.",
                *probe_note,
            ],
            oauth_provider="microsoft" if microsoft_oauth else None,
            recommended_settings=recommended,
        )

    if resolved_provider == "yandex":
        return SetupAction(
            action="show_app_password",
            message_ru="Яндекс готов к подключению. Нужен пароль приложения.",
            instructions=[
                "Откройте https://id.yandex.ru/security/app-passwords",
                "Войдите в аккаунт Яндекса и создайте пароль для приложения «Почта».",
                "Скопируйте сгенерированный пароль и вставьте его в поле ниже.",
                "Обычный пароль от входа в почту не подходит для SMTP.",
                *probe_note,
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    if resolved_provider == "mailru":
        return SetupAction(
            action="show_app_password",
            message_ru="Почта Mail готова к подключению. Нужен пароль для внешнего приложения.",
            instructions=[
                "Откройте инструкцию Mail: https://help.mail.ru/mail/login/mailer/",
                "Войдите в аккаунт и перейдите в «Безопасность → Пароли для внешних приложений».",
                "Создайте новый пароль для внешнего приложения и скопируйте его.",
                "Вставьте пароль в поле ниже — обычный пароль от входа в почту не подойдёт.",
                *probe_note,
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    if recommended and probe_reachable:
        return SetupAction(
            action="show_password",
            message_ru="SMTP-сервер найден. Введите пароль почтового ящика.",
            instructions=[
                "После ввода пароля нажмите «Проверить и сохранить».",
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    if recommended:
        return SetupAction(
            action="show_password",
            message_ru="SMTP-сервер определён. Введите пароль почтового ящика.",
            instructions=[
                "После ввода пароля нажмите «Проверить и сохранить».",
                *probe_note,
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    if not probe_reachable and ("network is unreachable" in last_error or "файрвол" in last_error):
        return SetupAction(
            action="contact_admin",
            message_ru="Сеть блокирует исходящие SMTP-порты из контейнера.",
            instructions=[
                "Попросите администратора открыть исходящие порты 587 и 465.",
                "Проверьте VPN и корпоративный файрвол.",
                "Можно указать сервер и порт вручную, если знаете рабочие значения.",
            ],
            oauth_provider=None,
            recommended_settings=recommended,
        )

    return SetupAction(
        action="show_manual",
        message_ru="Не удалось автоматически найти рабочий SMTP-сервер.",
        instructions=[
            "Укажите SMTP-сервер, порт и тип шифрования вручную.",
            "Обычно работает порт 587 с STARTTLS.",
        ],
        oauth_provider=None,
        recommended_settings=recommended,
    )


def _resolve_provider(
    provider_hint: str,
    domain: str,
    discoveries: list[dict[str, Any]],
) -> str:
    if provider_hint and provider_hint != "custom":
        return provider_hint
    for item in discoveries:
        provider = str(item.get("provider") or "").strip().lower()
        if provider and provider != "custom":
            return provider
    for provider_id, domains in _KNOWN_PROVIDER_DOMAINS.items():
        if domain in domains:
            return provider_id
    return "custom"


def _resolve_recommended_settings(context: dict[str, Any]) -> dict[str, Any] | None:
    probe = context.get("probe") or {}
    if probe.get("reachable"):
        settings = _recommended_settings_from_probe(probe)
        if settings:
            return settings
    discoveries = context.get("discoveries") or []
    discovery_settings = _recommended_settings_from_discovery(discoveries)
    if discovery_settings:
        return discovery_settings
    return _recommended_settings_from_probe(probe)


def _recommended_settings_from_discovery(discoveries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in discoveries:
        host = str(item.get("host") or "").strip()
        if not host:
            continue
        return {
            "provider": str(item.get("provider") or "custom"),
            "host": host,
            "port": int(item.get("port") or 587),
            "use_ssl": bool(item.get("use_ssl")),
            "use_starttls": bool(item.get("use_starttls")),
            "source": str(item.get("source") or ""),
            "confidence": str(item.get("confidence") or ""),
        }
    return None


def _recommended_settings_from_probe(probe: dict[str, Any]) -> dict[str, Any] | None:
    host = str(probe.get("host") or "").strip()
    if not host:
        return None
    return {
        "provider": str(probe.get("provider") or "custom"),
        "host": host,
        "port": int(probe.get("port") or 587),
        "use_ssl": bool(probe.get("use_ssl")),
        "use_starttls": bool(probe.get("use_starttls")),
        "source": str(probe.get("source") or ""),
        "confidence": str(probe.get("confidence") or ""),
    }


def _build_llm_client() -> OpenAI | None:
    if not OpenAI:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client:
        kwargs["http_client"] = http_client
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


def _build_request_kwargs(context: dict[str, Any]) -> dict[str, Any]:
    base_url = _resolve_openai_base_url()
    prompt = (
        "Ты помощник по настройке исходящей SMTP-почты в веб-приложении.\n"
        "Верни только JSON без markdown.\n"
        "Поля:\n"
        "- action: apply_settings|show_oauth|show_app_password|show_password|show_manual|retry_probe|contact_admin\n"
        "- message_ru: короткое сообщение пользователю\n"
        "- instructions: массив из 3–5 строк с пошаговыми инструкциями\n"
        "- oauth_provider: google|microsoft|null\n"
        "- recommended_settings: host/port/use_ssl/use_starttls/provider из probe или discoveries, не придумывай host\n"
        "Для action=show_app_password обязательно дай подробную инструкцию со ссылками:\n"
        "- Gmail: https://myaccount.google.com/apppasswords\n"
        "- Яндекс: https://id.yandex.ru/security/app-passwords\n"
        "- Mail.ru: https://help.mail.ru/mail/login/mailer/\n"
        "Каждый шаг — отдельная строка в instructions[]. Включай полные URL в текст шага.\n"
        "Не предлагай host/port, которых нет в probe или discoveries.\n"
        f"Контекст:\n{json.dumps(context, ensure_ascii=False)}"
    )
    request_kwargs: dict[str, Any] = {
        "model": settings.case_agent_model,
        "messages": [
            {"role": "system", "content": "Отвечай только JSON для SMTP setup wizard."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    if not base_url:
        request_kwargs["response_format"] = {"type": "json_object"}
    return request_kwargs


def _parse_setup_action_json(content: str, context: dict[str, Any]) -> SetupAction | None:
    payload = _extract_json_object(content)
    if not payload:
        return None
    action = str(payload.get("action") or "").strip()
    if action not in SETUP_ACTIONS:
        return None
    oauth_provider = payload.get("oauth_provider")
    if oauth_provider is not None:
        oauth_provider = str(oauth_provider).strip().lower() or None
        if oauth_provider not in OAUTH_PROVIDERS:
            oauth_provider = None
    recommended = payload.get("recommended_settings")
    if isinstance(recommended, dict):
        host = str(recommended.get("host") or "").strip()
        if not host:
            recommended = _resolve_recommended_settings(context)
    else:
        recommended = _resolve_recommended_settings(context)
    instructions = payload.get("instructions") or []
    if not isinstance(instructions, list):
        instructions = []
    instructions = [str(item).strip() for item in instructions if str(item).strip()]
    message_ru = str(payload.get("message_ru") or "").strip()
    if not message_ru:
        return None
    return SetupAction(
        action=action,
        message_ru=message_ru,
        instructions=instructions,
        oauth_provider=oauth_provider,
        recommended_settings=recommended,
        ai_used=True,
    )


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fence_match:
        text = fence_match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None

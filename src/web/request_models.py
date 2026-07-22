from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    value = value.strip()
    return value or None


def _clean_required_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value.strip()


def _empty_to_none(value: Any) -> Any:
    if value == "":
        return None
    return value


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobScopedRequest(ApiRequest):
    job_id: str | None = None

    @field_validator("job_id", mode="before")
    @classmethod
    def _normalize_job_id(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class ChatRequest(JobScopedRequest):
    message: str
    session_id: str | None = None

    @field_validator("message", mode="before")
    @classmethod
    def _normalize_message(cls, value: Any) -> str:
        return _clean_required_text(value)

    @field_validator("session_id", mode="before")
    @classmethod
    def _normalize_session_id(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class PromptRequest(JobScopedRequest):
    prompt: str

    @field_validator("prompt", mode="before")
    @classmethod
    def _normalize_prompt(cls, value: Any) -> str:
        return _clean_required_text(value)


class LimitRequest(JobScopedRequest):
    limit: int | None = Field(default=None, ge=1)

    @field_validator("limit", mode="before")
    @classmethod
    def _normalize_limit(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        if value == 0 or value == "0":
            return None
        return _empty_to_none(value)


class DocumentsStartRequest(JobScopedRequest):
    mode: str = "fast"
    document_mode: str | None = None
    work_type: str | None = None
    template_analysis_confirmed: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> str:
        cleaned = _clean_optional_text(value)
        return cleaned or "fast"

    @field_validator("document_mode", "work_type", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class TemplatePreviewRequest(JobScopedRequest):
    document_mode: str | None = None
    work_type: str | None = None
    row_id: str | None = None

    @field_validator("document_mode", "work_type", "row_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class SenderRunRequest(LimitRequest):
    dry_run: bool = True
    transport: str | None = None
    send_mode: str | None = None
    attachment_mode: str | None = None
    recipient_strategy: str | None = None
    mail_subject: str | None = None
    sender_email: str | None = None
    work_type: str | None = None
    campaign_name: str | None = None
    scheduled_start_at: datetime | None = None
    smtp_mailbox_id: str | None = None

    @field_validator("transport", "send_mode", "attachment_mode", "recipient_strategy", "mail_subject", "sender_email", "work_type", "campaign_name", "smtp_mailbox_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)

    @field_validator("scheduled_start_at", mode="before")
    @classmethod
    def _normalize_scheduled_start_at(cls, value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            normalized = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise ValueError("must be a valid ISO datetime") from exc
        else:
            raise ValueError("must be a datetime string")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


class WorkerStopRequest(ApiRequest):
    status_path: str = Field(min_length=1)
    pid: int | None = Field(default=None, ge=1)

    @field_validator("status_path", mode="before")
    @classmethod
    def _normalize_status_path(cls, value: Any) -> str:
        return _clean_required_text(value)

    @field_validator("pid", mode="before")
    @classmethod
    def _normalize_pid(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        return _empty_to_none(value)


class PhilologistRunRequest(JobScopedRequest):
    ai_enabled: bool = True
    mode: str | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class DocumentsLoadTestRequest(ApiRequest):
    row_count: int = Field(default=500, ge=1)
    source_job_id: str | None = None
    seed: int | None = None

    @field_validator("row_count", "seed", mode="before")
    @classmethod
    def _normalize_ints(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        return _empty_to_none(value)

    @field_validator("source_job_id", mode="before")
    @classmethod
    def _normalize_source_job_id(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class DataVerifyMunicipalityNamesRequest(JobScopedRequest):
    pass


class InflectionApprovalRequest(ApiRequest):
    entity_type: str
    source_value: str
    target_case: str
    result_value: str

    @field_validator("entity_type", "source_value", "target_case", "result_value", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        return _clean_required_text(value)


class AuthRegisterRequest(ApiRequest):
    username: str
    password: str
    password_confirm: str | None = None
    company_id: str | None = None
    company_role: str | None = None

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return _clean_required_text(value)

    @field_validator("password", "password_confirm", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value


class AuthLoginRequest(ApiRequest):
    username: str
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return _clean_required_text(value)

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.bitrix_board.config import BoardConfig, WebhookConfig

RETRYABLE_STATUS = {429, 502, 503, 504}
MAX_RETRIES = 2
RETRY_DELAYS_MS = (500, 1500)
HTTP_TIMEOUT_SECONDS = 30.0

LIST_TASK_SELECT = [
    "ID",
    "TITLE",
    "DESCRIPTION",
    "STATUS",
    "PRIORITY",
    "DEADLINE",
    "CREATED_DATE",
    "CHANGED_DATE",
    "RESPONSIBLE_ID",
    "RESPONSIBLE_NAME",
    "CREATED_BY",
    "CREATED_BY_NAME",
    "GROUP_ID",
    "PARENT_ID",
    "STAGE_ID",
    "UF_CRM_TASK",
    "ACCOMPLICES",
    "AUDITORS",
    "TAGS",
]

GET_TASK_SELECT = ["*", "UF_CRM_TASK", "UF_TASK_WEBDAV_FILES", "UF_MAIL_MESSAGE", "SE_PARAMETER"]


class BitrixClientError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ProjectInfo:
    id: int
    name: str
    matched_repo: str | None = None


@dataclass(frozen=True)
class ProjectStage:
    id: int
    title: str
    sort: int


@dataclass(frozen=True)
class TaskSummary:
    id: int
    title: str
    description: str | None
    status: int | None
    stage_id: int | None
    stage_name: str | None
    group_id: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class TaskMessage:
    id: int | str | None
    author_id: int | str | None
    author_name: str | None
    date: str | None
    text: str | None
    plain_text: str | None


@dataclass(frozen=True)
class ChecklistItem:
    id: int | str | None
    title: str | None
    is_complete: bool


def _normalize_stage_name(value: str) -> str:
    return value.strip().lower()


def _strip_html(value: str) -> str:
    text = re.sub(r"\[/?[a-z0-9*=]+(?:=[^\]]+)?\]", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _pick(task: dict[str, Any], upper: str, lower: str) -> Any:
    if task.get(upper) is not None:
        return task.get(upper)
    return task.get(lower)


class BitrixClient:
    def __init__(self, config: WebhookConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    @property
    def webhook_user_id(self) -> int:
        return self._config.user_id

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BitrixClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _build_v2_url(self, method: str) -> str:
        return f"{self._config.base_url}/{method}.json"

    def _build_v3_url(self, method: str) -> str:
        return (
            f"{self._config.origin}/rest/api/"
            f"{self._config.user_id}/{self._config.token}/{method}"
        )

    def call_v2(self, method: str, params: dict[str, Any] | None = None) -> Any:
        data = self.call_v2_raw(method, params)
        return data.get("result")

    def call_v2_raw(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._build_v2_url(method)
        return self._post_json(url, params or {}, api_version="v2")

    def call_v3(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = self._build_v3_url(method)
        data = self._post_json(url, params or {}, api_version="v3")
        return data.get("result")

    def _post_json(self, url: str, params: dict[str, Any], *, api_version: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._execute_post(url, params, api_version=api_version)
            except BitrixClientError as exc:
                last_error = exc
                if not exc.retryable or attempt >= MAX_RETRIES:
                    raise
                time.sleep(RETRY_DELAYS_MS[attempt] / 1000.0)
        if last_error:
            raise last_error
        raise BitrixClientError("Bitrix24 request failed")

    def _execute_post(self, url: str, params: dict[str, Any], *, api_version: str) -> dict[str, Any]:
        try:
            response = self._client.post(
                url,
                json=params,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise BitrixClientError("Bitrix24 request timed out after 30 seconds") from exc
        except httpx.HTTPError as exc:
            raise BitrixClientError(f"Network error calling Bitrix24: {exc}") from exc

        try:
            data = response.json() if response.text else {}
        except ValueError as exc:
            raise BitrixClientError(
                f"Bitrix24 returned non-JSON response (HTTP {response.status_code})",
                retryable=response.status_code in RETRYABLE_STATUS,
            ) from exc

        if not isinstance(data, dict):
            raise BitrixClientError("Bitrix24 returned unexpected payload")

        if not response.is_success:
            raise BitrixClientError(
                self._format_api_error(data) or f"Bitrix24 HTTP error {response.status_code}",
                retryable=response.status_code in RETRYABLE_STATUS,
            )

        if data.get("error") or data.get("error_description"):
            raise BitrixClientError(self._format_api_error(data))

        if api_version == "v3":
            error = data.get("error")
            if isinstance(error, dict):
                raise BitrixClientError(self._format_api_error(error))

        return data

    def _format_api_error(self, data: dict[str, Any]) -> str:
        parts: list[str] = []
        if data.get("error"):
            parts.append(str(data["error"]))
        if data.get("error_description"):
            parts.append(str(data["error_description"]))
        if data.get("message"):
            parts.append(str(data["message"]))
        return ": ".join(parts) if parts else "Bitrix24 API error"


def create_bitrix_client(config: BoardConfig) -> BitrixClient:
    return BitrixClient(config.webhook)


def _unwrap_task(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    task = result.get("task")
    if isinstance(task, dict):
        return task
    return result


def _normalize_list_task(task: dict[str, Any]) -> TaskSummary:
    stage_id_raw = _pick(task, "STAGE_ID", "stageId")
    stage_name = task.get("STAGE_NAME") or task.get("stageName")
    group_raw = _pick(task, "GROUP_ID", "groupId")
    description = _pick(task, "DESCRIPTION", "description")
    return TaskSummary(
        id=int(_pick(task, "ID", "id")),
        title=str(_pick(task, "TITLE", "title") or ""),
        description=str(description) if description is not None else None,
        status=int(status) if (status := _pick(task, "STATUS", "status")) is not None else None,
        stage_id=int(stage_id_raw) if stage_id_raw is not None else None,
        stage_name=str(stage_name) if stage_name else None,
        group_id=int(group_raw) if group_raw is not None else None,
        raw=task,
    )


def _enrich_task_with_stage(task: dict[str, Any], stages_by_id: dict[int, ProjectStage]) -> dict[str, Any]:
    stage_id_raw = _pick(task, "STAGE_ID", "stageId")
    stage_id = int(stage_id_raw) if stage_id_raw is not None else None
    stage = stages_by_id.get(stage_id) if stage_id is not None else None
    enriched = dict(task)
    enriched["STAGE_ID"] = stage_id
    enriched["STAGE_NAME"] = stage.title if stage else task.get("STAGE_NAME")
    return enriched


def get_project_stages(client: BitrixClient, group_id: int) -> list[ProjectStage]:
    result = client.call_v2("task.stages.get", {"entityId": group_id}) or {}
    stages: list[ProjectStage] = []
    for stage in (result.values() if isinstance(result, dict) else []):
        if not isinstance(stage, dict):
            continue
        stage_id = int(stage.get("ID", 0))
        title = str(stage.get("TITLE", ""))
        if stage_id and title:
            stages.append(ProjectStage(id=stage_id, title=title, sort=int(stage.get("SORT", 0))))
    stages.sort(key=lambda item: item.sort)
    return stages


def resolve_stage_ids(
    stages: list[ProjectStage],
    requested_stages: list[str] | None = None,
    *,
    include_done_stage: bool = False,
) -> set[int]:
    if requested_stages:
        wanted = {_normalize_stage_name(name) for name in requested_stages}
        return {stage.id for stage in stages if _normalize_stage_name(stage.title) in wanted}
    return {
        stage.id
        for stage in stages
        if include_done_stage or _normalize_stage_name(stage.title) != "готово"
    }


def list_projects(
    client: BitrixClient,
    config: BoardConfig,
    *,
    search: str | None = None,
) -> list[ProjectInfo]:
    projects: dict[int, ProjectInfo] = {}
    search_needle = search.strip().lower() if search else None

    try:
        result = client.call_v2(
            "sonet_group.get",
            {"filter": {"ACTIVE": "Y"}, "order": {"NAME": "ASC"}},
        )
        groups = result if isinstance(result, list) else list((result or {}).values())
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_id = int(group.get("ID") or group.get("id") or 0)
            name = str(group.get("NAME") or group.get("name") or "")
            if not group_id or not name:
                continue
            if search_needle and search_needle not in name.lower():
                continue
            matched = next((k for k, gid in config.repo_group_map.items() if gid == group_id), None)
            projects[group_id] = ProjectInfo(id=group_id, name=name, matched_repo=matched)
    except BitrixClientError:
        pass

    if not projects:
        seen: set[int] = set()
        start = 0
        while True:
            page = client.call_v2_raw(
                "tasks.task.list",
                {
                    "filter": {},
                    "select": ["ID", "GROUP_ID"],
                    "order": {"ID": "desc"},
                    "start": start,
                },
            )
            tasks = (page.get("result") or {}).get("tasks") or []
            for task in tasks:
                group = task.get("group")
                if not isinstance(group, dict):
                    continue
                group_id = int(group.get("id") or task.get("groupId") or task.get("GROUP_ID") or 0)
                name = str(group.get("name") or "")
                if not group_id or not name or group_id in seen:
                    if group_id:
                        seen.add(group_id)
                    continue
                seen.add(group_id)
                if search_needle and search_needle not in name.lower():
                    continue
                matched = next((k for k, gid in config.repo_group_map.items() if gid == group_id), None)
                projects[group_id] = ProjectInfo(id=group_id, name=name, matched_repo=matched)
            next_start = page.get("next")
            if next_start is None:
                break
            start = int(next_start)

    return sorted(projects.values(), key=lambda item: item.name)


def resolve_effective_group_id(
    client: BitrixClient,
    config: BoardConfig,
    *,
    project: str | None = None,
    auto_discover: bool = False,
) -> int | None:
    project_name = (project or "").strip()
    if project_name:
        repo_key = project_name.lower()
        if repo_key in config.repo_group_map:
            return config.repo_group_map[repo_key]
        needle = repo_key
        try:
            result = client.call_v2(
                "sonet_group.get",
                {"filter": {"%NAME": project_name}},
            )
            groups = result if isinstance(result, list) else list((result or {}).values())
            for group in groups:
                if not isinstance(group, dict):
                    continue
                name = str(group.get("NAME") or group.get("name") or "")
                group_id = int(group.get("ID") or group.get("id") or 0)
                if group_id and (name.lower() == needle or needle in name.lower()):
                    return group_id
        except BitrixClientError:
            pass

        seen: set[int] = set()
        start = 0
        while True:
            page = client.call_v2_raw(
                "tasks.task.list",
                {"filter": {}, "select": ["ID", "GROUP_ID"], "order": {"ID": "desc"}, "start": start},
            )
            tasks = (page.get("result") or {}).get("tasks") or []
            for task in tasks:
                group = task.get("group")
                if not isinstance(group, dict):
                    continue
                name = str(group.get("name") or "")
                group_id = int(group.get("id") or task.get("groupId") or task.get("GROUP_ID") or 0)
                if group_id and group_id not in seen and (
                    name.lower() == needle or needle in name.lower()
                ):
                    return group_id
                if group_id:
                    seen.add(group_id)
            next_start = page.get("next")
            if next_start is None:
                break
            start = int(next_start)
        raise BitrixClientError(f'Project "{project_name}" not found in Bitrix24')

    if config.default_group_id is not None:
        return config.default_group_id

    if auto_discover:
        projects = list_projects(client, config)
        if len(projects) == 1:
            return projects[0].id
    return None


def list_tasks_in_stages(
    client: BitrixClient,
    config: BoardConfig,
    *,
    project: str,
    stages: list[str],
    limit: int = 200,
) -> list[TaskSummary]:
    group_id = resolve_effective_group_id(client, config, project=project, auto_discover=True)
    if group_id is None:
        raise BitrixClientError(f"Could not resolve project: {project}")

    project_stages = get_project_stages(client, group_id)
    stages_by_id = {stage.id: stage for stage in project_stages}
    allowed_stage_ids = resolve_stage_ids(project_stages, stages)

    collected: list[dict[str, Any]] = []
    start = 0
    while True:
        page = client.call_v2_raw(
            "tasks.task.list",
            {
                "order": {"ID": "asc"},
                "filter": {"GROUP_ID": group_id},
                "select": LIST_TASK_SELECT,
                "start": start,
            },
        )
        tasks = (page.get("result") or {}).get("tasks") or []
        for task in tasks:
            enriched = _enrich_task_with_stage(task, stages_by_id)
            stage_id = enriched.get("STAGE_ID")
            if stage_id in allowed_stage_ids:
                status = enriched.get("STATUS") or enriched.get("status")
                if int(status or 0) == 5:
                    continue
                collected.append(enriched)
                if len(collected) >= limit:
                    break
        if len(collected) >= limit:
            break
        next_start = page.get("next")
        if next_start is None:
            break
        start = int(next_start)

    return [_normalize_list_task(task) for task in collected]


def get_task(client: BitrixClient, task_id: int) -> dict[str, Any]:
    result = client.call_v2("tasks.task.get", {"taskId": task_id, "select": GET_TASK_SELECT})
    task = _unwrap_task(result)
    if not task:
        raise BitrixClientError(f"Task {task_id} not found or access denied")
    return task


def get_task_checklist(client: BitrixClient, task_id: int) -> list[ChecklistItem]:
    try:
        result = client.call_v2(
            "task.checklistitem.getlist",
            {"TASKID": task_id, "ORDER": {"SORT_INDEX": "ASC"}},
        )
    except BitrixClientError:
        return []

    items: list[ChecklistItem] = []
    raw_items = result if isinstance(result, list) else list((result or {}).values())
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        complete = entry.get("IS_COMPLETE") or entry.get("isComplete")
        items.append(
            ChecklistItem(
                id=entry.get("ID") or entry.get("id"),
                title=str(entry.get("TITLE") or entry.get("title") or ""),
                is_complete=complete in ("Y", True, "true", 1),
            )
        )
    return items


def _format_message(entry: dict[str, Any]) -> TaskMessage:
    text = _pick(entry, "POST_MESSAGE", "postMessage") or _pick(entry, "MESSAGE", "message")
    if text is None:
        text = _pick(entry, "TEXT", "text") or _pick(entry, "POST", "post")
    plain = _strip_html(str(text)) if text else None
    author = entry.get("author")
    author_name = None
    if isinstance(author, dict):
        author_name = str(author.get("name") or "")
    return TaskMessage(
        id=_pick(entry, "ID", "id"),
        author_id=_pick(entry, "AUTHOR_ID", "authorId") or _pick(entry, "AUTHOR", "author"),
        author_name=author_name or str(_pick(entry, "AUTHOR_NAME", "authorName") or "") or None,
        date=str(
            _pick(entry, "POST_DATE", "postDate")
            or _pick(entry, "DATE_CREATE", "dateCreate")
            or _pick(entry, "CREATED_AT", "createdAt")
            or ""
        )
        or None,
        text=str(text) if text is not None else None,
        plain_text=plain,
    )


def list_task_messages(client: BitrixClient, task_id: int, *, limit: int = 50) -> list[TaskMessage]:
    messages: list[dict[str, Any]] = []
    try:
        result = client.call_v2(
            "task.commentitem.getlist",
            {"TASKID": task_id, "ORDER": {"POST_DATE": "asc"}},
        )
        if isinstance(result, list):
            messages = [item for item in result if isinstance(item, dict)]
        elif isinstance(result, dict):
            messages = [item for item in result.values() if isinstance(item, dict)]
    except BitrixClientError:
        messages = []

    if not messages:
        try:
            result = client.call_v3(
                "tasks.task.chat.message.list",
                {"taskId": task_id, "order": {"id": "asc"}},
            )
            if isinstance(result, dict):
                raw = result.get("messages") or result.get("items") or []
                messages = [item for item in raw if isinstance(item, dict)]
            elif isinstance(result, list):
                messages = [item for item in result if isinstance(item, dict)]
        except BitrixClientError:
            messages = []

    return [_format_message(entry) for entry in messages[-limit:]]


def add_task_message(client: BitrixClient, task_id: int, text: str) -> int | str | None:
    try:
        result = client.call_v3(
            "tasks.task.chat.message.send",
            {"fields": {"taskId": task_id, "text": text}},
        )
        if isinstance(result, dict):
            return result.get("id") or result.get("ID")
        return result
    except BitrixClientError:
        result = client.call_v2(
            "task.commentitem.add",
            {"TASKID": task_id, "FIELDS": {"POST_MESSAGE": text}},
        )
        if isinstance(result, dict):
            return result.get("ID") or result.get("id")
        return result


def resolve_stage_target(
    client: BitrixClient,
    task_id: int,
    *,
    stage_name: str,
) -> tuple[int, str]:
    task = get_task(client, task_id)
    group_id = int(_pick(task, "GROUP_ID", "groupId") or 0)
    if not group_id and isinstance(task.get("group"), dict):
        group_id = int(task["group"].get("id") or 0)
    if not group_id:
        raise BitrixClientError(f"Task {task_id} has no GROUP_ID")

    stages = get_project_stages(client, group_id)
    needle = _normalize_stage_name(stage_name)
    target = next((stage for stage in stages if _normalize_stage_name(stage.title) == needle), None)
    if not target:
        available = ", ".join(stage.title for stage in stages)
        raise BitrixClientError(
            f'Stage "{stage_name}" not found in project {group_id}. Available: {available}'
        )
    return target.id, target.title


def move_task_to_stage(client: BitrixClient, task_id: int, stage_name: str) -> None:
    stage_id, _ = resolve_stage_target(client, task_id, stage_name=stage_name)
    try:
        can_move = client.call_v2("task.stages.canmovetask", {"id": task_id})
        if can_move is False:
            raise BitrixClientError("Insufficient permissions to move task between stages")
    except BitrixClientError as exc:
        message = str(exc).lower()
        if "insufficient permissions" in message or "недостаточно прав" in message:
            raise
    client.call_v2("task.stages.movetask", {"id": task_id, "stageId": stage_id})

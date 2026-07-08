from __future__ import annotations

from pathlib import Path

from src.infra.object_store import get_file, job_key, list_keys, put_file
from src.jobs.storage import normalize_job_id, resolve_job_paths


SUBDIR_PREFIXES = {
    "input": "input",
    "templates": "templates",
    "output": "output",
    "consents": "consents",
    "reports": "reports",
    "archives": "archives",
}


def _job_id_or_raise(job_id: str | None) -> str:
    normalized = normalize_job_id(job_id)
    if not normalized:
        raise ValueError("job_id is required for S3 workspace sync")
    return normalized


def pull_job(job_id: str | None, subdirs: list[str] | None = None) -> None:
    normalized = _job_id_or_raise(job_id)
    paths = resolve_job_paths(normalized)
    prefixes = [SUBDIR_PREFIXES[name] for name in (subdirs or SUBDIR_PREFIXES.keys()) if name in SUBDIR_PREFIXES]
    for prefix in prefixes:
        s3_prefix = job_key(normalized, prefix)
        for key in list_keys(s3_prefix + "/"):
            relative = key.split(f"jobs/{normalized}/", 1)[-1]
            local_path = paths.root_dir / relative
            get_file(key, local_path)


def push_job(job_id: str | None, subdirs: list[str] | None = None) -> None:
    normalized = _job_id_or_raise(job_id)
    paths = resolve_job_paths(normalized)
    names = subdirs or list(SUBDIR_PREFIXES.keys())
    for name in names:
        local_dir = {
            "input": paths.data_xlsx.parent,
            "templates": paths.templates_dir,
            "output": paths.output_dir,
            "consents": paths.consents_dir,
            "reports": paths.root_dir / "reports",
            "archives": paths.root_dir / "archives",
        }.get(name)
        if local_dir is None or not Path(local_dir).exists():
            continue
        for file_path in Path(local_dir).rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(paths.root_dir).as_posix()
            put_file(job_key(normalized, relative), file_path)


def put_upload(job_id: str | None, relative_path: str, local_path: Path) -> None:
    normalized = _job_id_or_raise(job_id)
    put_file(job_key(normalized, relative_path), local_path)


def ensure_local_file(job_id: str | None, relative_path: str) -> Path:
    normalized = normalize_job_id(job_id)
    paths = resolve_job_paths(normalized)
    local_path = paths.root_dir / relative_path
    if local_path.exists():
        return local_path
    key = job_key(normalized or "__legacy__", relative_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    get_file(key, local_path)
    return local_path

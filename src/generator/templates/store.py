from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .models import CertificationResult, TemplatePackage


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


class AdaptiveTemplateStore:
    def __init__(self, templates_dir: Path, kind: str = "kp") -> None:
        self.templates_dir = Path(templates_dir)
        self.kind = str(kind).strip().lower()
        if not self.kind or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in self.kind):
            raise ValueError("Invalid template kind")
        self.root = self.templates_dir / ".adaptive" / self.kind
        self.versions_dir = self.root / "versions"
        self.active_path = self.root / "active.json"
        self.latest_path = self.root / "latest.json"

    def version_dir(self, template_id: str) -> Path:
        safe_id = str(template_id).strip()
        if not safe_id or Path(safe_id).name != safe_id or safe_id in {".", ".."}:
            raise ValueError("Invalid template id")
        return self.versions_dir / safe_id

    def save_package(self, package: TemplatePackage, source_path: Path) -> Path:
        version_dir = self.version_dir(package.template_id)
        version_dir.mkdir(parents=True, exist_ok=False)
        stored_source = version_dir / f"source{source_path.suffix.lower()}"
        shutil.copy2(source_path, stored_source)
        _write_json_atomic(version_dir / "manifest.json", package.to_dict())
        _write_json_atomic(
            self.latest_path,
            {"template_id": package.template_id, "created_at": package.created_at},
        )
        _write_json_atomic(
            version_dir / "certification.json",
            {
                "template_id": package.template_id,
                "status": "pending",
                "created_at": package.created_at,
                "checks": [],
                "artifacts": [],
                "error": "",
            },
        )
        return version_dir

    def source_path(self, template_id: str) -> Path:
        version_dir = self.version_dir(template_id)
        candidates = sorted(version_dir.glob("source.*"))
        if len(candidates) != 1:
            raise FileNotFoundError(f"Template source for {template_id} is missing or ambiguous")
        return candidates[0]

    def load_package(self, template_id: str) -> TemplatePackage:
        payload = _read_json(self.version_dir(template_id) / "manifest.json")
        if not payload:
            raise FileNotFoundError(f"Template package {template_id} not found")
        return TemplatePackage.from_dict(payload)

    def save_certification(self, result: CertificationResult) -> None:
        _write_json_atomic(self.version_dir(result.template_id) / "certification.json", result.to_dict())

    def load_certification(self, template_id: str) -> dict:
        return _read_json(self.version_dir(template_id) / "certification.json")

    def activate(self, template_id: str) -> None:
        package = self.load_package(template_id)
        certification = self.load_certification(template_id)
        if certification.get("status") != "passed":
            raise ValueError("Only a successfully certified template can be activated")
        _write_json_atomic(
            self.active_path,
            {
                "template_id": package.template_id,
                "source_sha256": package.source_sha256,
                "activated_at": certification.get("created_at"),
            },
        )

    def latest_template_id(self) -> str | None:
        payload = _read_json(self.latest_path)
        value = str(payload.get("template_id") or "").strip()
        return value or None
    def active_template_id(self) -> str | None:
        payload = _read_json(self.active_path)
        value = str(payload.get("template_id") or "").strip()
        return value or None

    def activation_state(self) -> dict:
        """Return whether the latest uploaded template is safe to render.

        An older active version must not mask a newer upload that is still
        pending or has failed certification. The caller can therefore fail
        closed instead of silently falling back to another rendering engine.
        """

        latest_id = self.latest_template_id()
        active_id = self.active_template_id()
        certification = self.load_certification(latest_id) if latest_id else {}
        certification_status = str(certification.get("status") or "").strip() or (
            "missing" if latest_id is None else "pending"
        )
        ready = bool(
            latest_id
            and active_id == latest_id
            and certification_status == "passed"
        )
        return {
            "latest_template_id": latest_id,
            "active_template_id": active_id,
            "certification_status": certification_status,
            "certification_error": str(certification.get("error") or "").strip(),
            "ready": ready,
        }

    def load_active(self) -> TemplatePackage | None:
        template_id = self.active_template_id()
        if not template_id:
            return None
        try:
            return self.load_package(template_id)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return None

    def list_versions(self) -> list[dict]:
        if not self.versions_dir.exists():
            return []
        active_id = self.active_template_id()
        latest_id = self.latest_template_id()
        versions: list[dict] = []
        for version_dir in sorted(self.versions_dir.iterdir(), reverse=True):
            if not version_dir.is_dir():
                continue
            try:
                package = self.load_package(version_dir.name)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            versions.append(
                {
                    **package.to_dict(),
                    "certification": self.load_certification(package.template_id),
                    "active": package.template_id == active_id,
                    "latest": package.template_id == latest_id,
                }
            )
        return versions

from __future__ import annotations

import hashlib
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import TemplatePackage
from .registry import TemplateAdapterRegistry
from .store import AdaptiveTemplateStore


def compile_template(
    source_path: Path,
    templates_dir: Path,
    *,
    kind: str = "kp",
    registry: TemplateAdapterRegistry | None = None,
    source_name: str | None = None,
    auto_discover: bool = True,
    reference_context: dict | None = None,
) -> TemplatePackage:
    source_path = Path(source_path)
    payload = source_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    adapter_registry = registry or TemplateAdapterRegistry()
    compile_temp_parent = Path(templates_dir).parent
    compile_temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adaptive-compile-", dir=str(compile_temp_parent)) as temp_dir:
        compiled_source = source_path
        discovery_report = None
        from .base import TemplateCompileError

        try:
            adapter = adapter_registry.resolve(compiled_source)
            occurrences, capabilities, warnings = adapter.inspect(compiled_source)
        except TemplateCompileError:
            if not auto_discover:
                raise
            from .auto_compiler import auto_compile_template

            auto_result = auto_compile_template(
                source_path,
                Path(temp_dir),
                reference_context=reference_context,
            )
            compiled_source = auto_result.source_path
            discovery_report = auto_result.report
            adapter = adapter_registry.resolve(compiled_source)
            occurrences, capabilities, warnings = adapter.inspect(compiled_source)
        except Exception as exc:
            raise TemplateCompileError(f"Could not inspect {source_path.name}: {exc}") from exc
        if discovery_report is not None:
            capabilities = {**capabilities, "auto_discovery": discovery_report}
            warnings = (*warnings, "Изменяемые зоны шаблона определены автоматически; требуется контроль превью.")
        created = datetime.now(timezone.utc)
        template_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-{checksum[:12]}"
        package = TemplatePackage(
            template_id=template_id,
            kind=kind,
            source_name=str(source_name or source_path.name),
            source_format=compiled_source.suffix.lower().lstrip("."),
            source_sha256=checksum,
            created_at=created.isoformat(timespec="seconds"),
            fields=tuple(sorted({item.field_name for item in occurrences})),
            occurrences=occurrences,
            adapter=adapter.name,
            capabilities=capabilities,
            warnings=warnings,
        )
        store = AdaptiveTemplateStore(templates_dir, kind)
        version_dir = store.save_package(package, compiled_source)
        if compiled_source.resolve() != source_path.resolve():
            shutil.copy2(source_path, version_dir / f"original{source_path.suffix.lower()}")
        return package
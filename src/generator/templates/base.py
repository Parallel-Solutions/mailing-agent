from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import TemplateOccurrence


class TemplateCompileError(ValueError):
    pass


class TemplateRenderError(RuntimeError):
    pass


class TemplateAdapter(ABC):
    name: str
    formats: tuple[str, ...]

    @abstractmethod
    def probe(self, source_path: Path) -> bool: ...

    @abstractmethod
    def inspect(self, source_path: Path) -> tuple[tuple[TemplateOccurrence, ...], dict[str, Any], tuple[str, ...]]: ...

    @abstractmethod
    def render(self, source_path: Path, context: dict[str, Any], output_path: Path) -> Path: ...

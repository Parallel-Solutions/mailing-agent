"""Adaptive, format-independent commercial proposal templates."""

from .compiler import compile_template
from .renderer import render_active_template
from .store import AdaptiveTemplateStore

__all__ = ["AdaptiveTemplateStore", "compile_template", "render_active_template"]

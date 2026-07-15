from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.public_router import create_public_router


ROOT = Path(__file__).resolve().parents[1]


def test_template_editor_assets_are_served_without_cache() -> None:
    app = FastAPI()
    app.include_router(create_public_router())
    client = TestClient(app)

    for path, media_type in (
        ("/public/template-editor.css", "text/css"),
        ("/public/template-editor.js", "application/javascript"),
        ("/public/service-design.css", "text/css"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert media_type in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store, no-cache, max-age=0, must-revalidate"


def test_template_editor_is_connected_to_main_navigation() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'data-nav-screen="templates"' in html
    assert 'id="s-templates"' in html
    assert 'id="template-editor-root"' in html
    assert 'id="template-editor-app"' in html
    assert "template-workspace-active" in html
    assert '/public/template-editor.css?v=4' in html
    assert '/public/template-editor.js?v=3' in html


def test_template_editor_uses_real_adaptive_endpoints() -> None:
    script = (ROOT / "src" / "web" / "static" / "template_editor.js").read_text(encoding="utf-8")

    assert "/api/templates/adaptive/status?template_kind=kp" in script
    assert "/api/upload/template" in script
    assert "/api/templates/adaptive/" in script
    assert "/activate" in script
    assert "/api/documents/template-preview" in script
    assert "/api/documents/template-analysis?document_mode=both" in script
    assert "/api/documents/chat" in script
    assert "/api/templates/editor-state" in script


def test_template_editor_contains_stitch_workspace_structure() -> None:
    script = (ROOT / "src" / "web" / "static" / "template_editor.js").read_text(encoding="utf-8")
    styles = (ROOT / "src" / "web" / "static" / "template_editor.css").read_text(encoding="utf-8")

    for marker in (
        "te-topbar",
        "te-library",
        "te-editor",
        "te-properties",
        "te-footer",
        "te-modal",
        "Выбор данных из сервиса",
        "Разрешение конфликтов",
        "Создайте свой следующий документ",
    ):
        assert marker in script
    assert "grid-template-columns: 280px minmax(0, 1fr) 280px" in styles


def test_service_shell_uses_canonical_entities_and_campaign_workspace() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "src" / "web" / "static" / "service_design.css").read_text(encoding="utf-8")

    for marker in (
        "Рабочее пространство",
        "Рассылки",
        "Шаблоны",
        "Аналитика",
        "Текущая рассылка",
        "Аудитория",
        "Документы",
        "Отправка",
        "Результаты",
        "Обзор рассылки",
        "settings-entity-grid",
        "service-topbar",
        "settings-inspector",
        "service-statusbar",
    ):
        assert marker in html
    assert '/public/service-design.css?v=3' in html
    assert "--se-primary" in styles
    assert ".entity-card" in styles
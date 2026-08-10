from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from src.web.static_assets import configure_response_compression


def test_static_assets_are_compressed_without_custom_cache_policy(tmp_path) -> None:
    asset = tmp_path / "app-content-hash.js"
    content = "const payload = 'compressible frontend asset';\n" * 200
    asset.write_text(content, encoding="utf-8")

    app = FastAPI()
    configure_response_compression(app)
    app.mount("/assets", StaticFiles(directory=tmp_path), name="assets")

    response = TestClient(app).get(
        "/assets/app-content-hash.js",
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.content == asset.read_bytes()
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert "cache-control" not in response.headers

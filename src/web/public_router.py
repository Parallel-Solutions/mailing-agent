from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


PUBLIC_ASSETS_DIR = Path("src/generator/assets")
WEB_STATIC_DIR = Path("src/web/static")


def create_public_router() -> APIRouter:
    router = APIRouter()

    @router.get("/public/mail-signature.png")
    async def public_mail_signature():
        signature_path = PUBLIC_ASSETS_DIR / "parresh-signature-logo-KI.png"
        if not signature_path.exists():
            raise HTTPException(status_code=404, detail="Mail signature image not found.")
        return FileResponse(
            signature_path,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @router.get("/public/documents-ui.js")
    async def public_documents_ui_script():
        script_path = WEB_STATIC_DIR / "documents_ui.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Documents UI script not found.")
        return FileResponse(script_path, media_type="application/javascript")

    @router.get("/public/sender-ui.js")
    async def public_sender_ui_script():
        script_path = WEB_STATIC_DIR / "sender_ui.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Sender UI script not found.")
        return FileResponse(script_path, media_type="application/javascript")

    @router.get("/public/statistics.css")
    async def public_statistics_css():
        css_path = WEB_STATIC_DIR / "statistics.css"
        if not css_path.exists():
            raise HTTPException(status_code=404, detail="Statistics CSS not found.")
        return FileResponse(css_path, media_type="text/css")

    @router.get("/public/statistics.js")
    async def public_statistics_js():
        script_path = WEB_STATIC_DIR / "statistics.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Statistics UI script not found.")
        return FileResponse(script_path, media_type="application/javascript")

    @router.get("/public/chart.min.js")
    async def public_chart_js():
        script_path = WEB_STATIC_DIR / "chart.min.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Chart.js not found.")
        return FileResponse(script_path, media_type="application/javascript")

    return router

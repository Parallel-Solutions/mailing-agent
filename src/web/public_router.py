from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response


PUBLIC_ASSETS_DIR = Path("src/generator/assets")
DOCXJS_VENDOR_DIR = Path("src/generator/generation/vendor/docxjs")

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def create_public_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        """Liveness for Docker healthchecks. Checks DB only; use /ready for deps."""
        try:
            from src.infra.db import check_db_connection

            check_db_connection()
        except Exception as exc:  # noqa: BLE001 - surface generic readiness failure
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "database": "down",
                    "detail": str(exc.__class__.__name__),
                },
            )
        return {"status": "ok", "database": "up"}

    @router.get("/ready")
    def ready():
        """Readiness: database, Redis, object store (MinIO), and Gotenberg."""
        from src.infra.readiness import collect_readiness

        payload = collect_readiness()
        if payload.get("status") != "ok":
            return JSONResponse(status_code=503, content=payload)
        return payload

    @router.get("/public/email/open/{token}.gif")
    def smtp_open_tracking_pixel(token: str):
        from src.generator.delivery.open_tracking import TRANSPARENT_GIF, record_smtp_open
        from src.utils.logger import logger

        try:
            record_smtp_open(token)
        except Exception:
            # Never expose storage failures to the recipient or break image rendering.
            logger.exception("smtp_open_tracking_record_failed")
        return Response(
            content=TRANSPARENT_GIF,
            media_type="image/gif",
            headers={
                **NO_CACHE_HEADERS,
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )

    @router.get("/public/email/click/{token}")
    def smtp_click_tracking_redirect(token: str):
        from src.generator.delivery.click_tracking import record_smtp_click
        from src.utils.config import settings
        from src.utils.logger import logger

        try:
            result = record_smtp_click(token)
        except Exception:
            # Never expose storage failures to the recipient.
            logger.exception("smtp_click_tracking_record_failed")
            result = {"found": False, "target_url": ""}

        target_url = str(result.get("target_url") or "").strip()
        if not target_url.lower().startswith(("http://", "https://")):
            # A stale/invalid/expired token must still redirect somewhere real
            # (unlike the open-tracking pixel, the recipient actually clicked
            # something) — never a 404/error page, and never reveal whether
            # the token existed.
            fallback = str(settings.public_base_url or "").strip().rstrip("/") or "/"
            return RedirectResponse(url=fallback, status_code=302)
        return RedirectResponse(url=target_url, status_code=302)

    @router.get("/public/mail-signature.png")
    def public_mail_signature():
        signature_path = PUBLIC_ASSETS_DIR / "parresh-signature-logo-KI.png"
        if not signature_path.exists():
            raise HTTPException(status_code=404, detail="Mail signature image not found.")
        return FileResponse(
            signature_path,
            media_type="image/png",
            headers=NO_CACHE_HEADERS,
        )

    @router.get("/public/vendor/jszip.min.js")
    def public_jszip_vendor():
        script_path = DOCXJS_VENDOR_DIR / "jszip.min.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="JSZip vendor script not found.")
        return FileResponse(script_path, media_type="application/javascript", headers=NO_CACHE_HEADERS)

    @router.get("/public/vendor/docx-preview.min.js")
    def public_docx_preview_vendor():
        script_path = DOCXJS_VENDOR_DIR / "docx-preview.min.js"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="docx-preview vendor script not found.")
        return FileResponse(script_path, media_type="application/javascript", headers=NO_CACHE_HEADERS)

    return router

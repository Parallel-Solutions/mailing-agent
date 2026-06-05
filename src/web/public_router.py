from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.generator.generation.config_generator import ONLYOFFICE_PUBLIC_FILES_DIR


PUBLIC_ASSETS_DIR = Path("src/generator/assets")


def create_public_router() -> APIRouter:
    router = APIRouter()

    @router.get("/public/mail-signature.png")
    async def public_mail_signature():
        signature_path = PUBLIC_ASSETS_DIR / "parresh-signature-logo.png"
        if not signature_path.exists():
            raise HTTPException(status_code=404, detail="Mail signature image not found.")
        return FileResponse(signature_path, media_type="image/png")

    @router.get("/public/onlyoffice/{token}/{filename}")
    async def public_onlyoffice_document(token: str, filename: str):
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise HTTPException(status_code=404, detail="Document not found.")
        safe_filename = Path(filename).name
        document_path = (ONLYOFFICE_PUBLIC_FILES_DIR / token / safe_filename).resolve()
        public_root = ONLYOFFICE_PUBLIC_FILES_DIR.resolve()
        if public_root not in document_path.parents or not document_path.exists():
            raise HTTPException(status_code=404, detail="Document not found.")
        return FileResponse(
            document_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=safe_filename,
        )

    return router

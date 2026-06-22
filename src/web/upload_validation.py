from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile


OOXML_SIGNATURE_MEMBERS = {
    ".xlsx": ("[Content_Types].xml", "xl/workbook.xml"),
    ".docx": ("[Content_Types].xml", "word/document.xml"),
}


def _format_upload_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes / (1024 * 1024):.0f} МБ"
    if max_bytes >= 1024:
        return f"{max_bytes / 1024:.0f} КБ"
    return f"{max_bytes} Б"


def _get_upload_size(upload: UploadFile) -> int | None:
    stream = getattr(upload, "file", None)
    if stream is None:
        return None
    try:
        current_position = stream.tell()
        stream.seek(0, 2)
        size = int(stream.tell())
        stream.seek(current_position)
        return size
    except Exception:
        return None


def _reject_format(human_name: str, extension: str) -> None:
    raise HTTPException(
        status_code=400,
        detail=f"Файл для {human_name} повреждён или не соответствует формату {extension}.",
    )


def _validate_ooxml_signature(upload: UploadFile, *, extension: str, human_name: str) -> None:
    required_members = OOXML_SIGNATURE_MEMBERS.get(extension)
    if not required_members:
        return

    stream = getattr(upload, "file", None)
    if stream is None:
        _reject_format(human_name, extension)

    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            normalized_names = {
                name.replace("\\", "/").lstrip("/"): name
                for name in archive.namelist()
            }
            if any(member not in normalized_names for member in required_members):
                _reject_format(human_name, extension)
            for member in required_members:
                with archive.open(normalized_names[member]) as member_file:
                    member_file.read(64)
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Файл для {human_name} повреждён или не соответствует формату {extension}.",
        ) from exc
    finally:
        try:
            stream.seek(0)
        except Exception:
            pass


def validate_uploaded_file(
    upload: UploadFile,
    *,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
    human_name: str,
) -> str:
    filename = Path(upload.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail=f"Не удалось определить имя файла для {human_name}.")
    extension = Path(filename).suffix.lower()
    normalized_allowed_extensions = tuple(item.lower() for item in allowed_extensions)
    if extension not in normalized_allowed_extensions:
        allowed_text = ", ".join(normalized_allowed_extensions)
        raise HTTPException(
            status_code=400,
            detail=f"Для {human_name} подходит только файл формата {allowed_text}.",
        )
    size = _get_upload_size(upload)
    if size is not None and size > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Файл слишком большой для {human_name}. "
                f"Максимальный размер: {_format_upload_size_limit(max_bytes)}."
            ),
        )
    _validate_ooxml_signature(upload, extension=extension, human_name=human_name)
    return filename

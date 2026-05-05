from __future__ import annotations

import json
from typing import Any, Dict

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .services import (
    generate_document_package,
    generate_documents_batch,
    handle_agent_message,
    review_generated_batch,
    review_text_content,
    review_uploaded_document,
)


def _json_response(payload: Dict[str, Any], status: int = 200) -> JsonResponse:
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False, "indent": 2})


def _parse_json(request: HttpRequest) -> Dict[str, Any]:
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


@csrf_exempt
def health_view(request: HttpRequest) -> JsonResponse:
    return _json_response({"status": "ok", "service": "kp_document_bot"})


@csrf_exempt
def generate_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    payload = _parse_json(request)
    row = payload.get("row")
    if not isinstance(row, dict):
        return _json_response({"status": "error", "message": "`row` must be an object"}, status=400)

    result = generate_document_package(
        row=row,
        outgoing_number=payload.get("outgoing_number"),
        generate_pdf=bool(payload.get("generate_pdf", True)),
        review_final_text=bool(payload.get("review_final_text", False)),
        review_model=payload.get("review_model"),
    )
    return _json_response(result)


@csrf_exempt
def generate_batch_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    payload = _parse_json(request)
    start_row = int(payload.get("start_row", 1))
    end_row = int(payload.get("end_row", start_row))
    result = generate_documents_batch(
        start_row=start_row,
        end_row=end_row,
        review_final_text=bool(payload.get("review_final_text", False)),
        review_model=payload.get("review_model"),
    )
    return _json_response(result)


@csrf_exempt
def review_text_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    payload = _parse_json(request)
    text = str(payload.get("text", "")).strip()
    if not text:
        return _json_response({"status": "error", "message": "`text` is required"}, status=400)

    return _json_response(review_text_content(text, model=payload.get("model")))


@csrf_exempt
def review_generated_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    payload = _parse_json(request)
    start_row = int(payload.get("start_row", 1))
    end_row = int(payload.get("end_row", start_row))
    return _json_response(review_generated_batch(start_row=start_row, end_row=end_row))


@csrf_exempt
def review_document_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    uploaded = request.FILES.get("file")
    if uploaded is None:
        payload = _parse_json(request)
        file_name = str(payload.get("file_name", "")).strip()
        content = payload.get("content")
        if not file_name or content is None:
            return _json_response({"status": "error", "message": "file is required"}, status=400)
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            return _json_response({"status": "error", "message": "`content` must be a string"}, status=400)
        result = review_uploaded_document(file_name=file_name, content=content_bytes, model=payload.get("model"))
        return _json_response(result)

    result = review_uploaded_document(
        file_name=uploaded.name,
        content=uploaded.read(),
        model=request.POST.get("model"),
    )
    return _json_response(result)


@csrf_exempt
def chat_view(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_response({"status": "error", "message": "POST required"}, status=405)

    payload = _parse_json(request)
    message = str(payload.get("message", "")).strip()
    if not message:
        return _json_response({"status": "error", "message": "`message` is required"}, status=400)

    uploaded_document = None
    uploaded = request.FILES.get("file")
    if uploaded is not None:
        uploaded_document = {
            "file_name": uploaded.name,
            "content": uploaded.read(),
        }

    result = handle_agent_message(
        message=message,
        session=payload.get("session"),
        row=payload.get("row"),
        text=payload.get("text"),
        uploaded_document=uploaded_document,
        outgoing_number=payload.get("outgoing_number"),
        review_final_text=bool(payload.get("review_final_text", True)),
        review_model=payload.get("review_model"),
    )
    return _json_response(result)

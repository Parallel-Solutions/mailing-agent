from __future__ import annotations

from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from src.utils.config import settings

_client = None


class ObjectNotFoundError(FileNotFoundError):
    pass


def _s3_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(s3={"addressing_style": "path" if settings.s3_use_path_style else "auto"}),
        )
    return _client


def ensure_bucket() -> None:
    client = _s3_client()
    bucket = settings.s3_bucket
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    kwargs: dict = {"Bucket": settings.s3_bucket, "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    _s3_client().put_object(**kwargs)


def get_bytes(key: str) -> bytes:
    try:
        response = _s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise ObjectNotFoundError(key) from exc
        raise
    return response["Body"].read()


def put_file(key: str, local_path: Path) -> None:
    local_path = Path(local_path)
    _s3_client().upload_file(str(local_path), settings.s3_bucket, key)


def get_file(key: str, local_path: Path) -> None:
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _s3_client().download_file(settings.s3_bucket, key, str(local_path))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise ObjectNotFoundError(key) from exc
        raise


def exists(key: str) -> bool:
    try:
        _s3_client().head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError:
        return False


def delete(key: str) -> None:
    _s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)


def list_keys(prefix: str) -> list[str]:
    client = _s3_client()
    keys: list[str] = []
    continuation: str | None = None
    while True:
        kwargs: dict = {"Bucket": settings.s3_bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            keys.append(str(item["Key"]))
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
    return keys


def presigned_url(key: str, expires: int = 3600) -> str:
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def job_key(job_id: str, *parts: str) -> str:
    normalized = [part.strip("/") for part in parts if str(part).strip("/")]
    return "/".join(["jobs", job_id, *normalized])

"""Hardened Site media upload client (Parser → Site Blog media API)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import requests

from config.settings import config
from utils.media_utils import (
    decode_media_payload,
    extract_raw_feed_cover_image_url,
    is_image_ref,
    is_video_ref,
    iter_media_candidates,
    media_ref_to_local_path,
    normalize_media_ref,
    normalize_raw_feed_media_ref,
)

LOGGER = logging.getLogger(__name__)

ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
ALLOWED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
}
MAGIC_MIME = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # refined below
)


@dataclass(slots=True)
class MediaUploadResult:
    ok: bool
    url: str = ""
    error: str = ""
    status_code: int | None = None
    checksum: str = ""
    mime_type: str = ""
    bytes: int = 0
    response: dict[str, Any] | None = None


def media_upload_url() -> str:
    full_url = str(getattr(config, "MEDIA_UPLOAD_URL", "") or "").strip()
    if full_url:
        return full_url
    endpoint = str(getattr(config, "MEDIA_UPLOAD_ENDPOINT", "") or "").strip()
    if not endpoint:
        return ""
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    base_url = (
        str(getattr(config, "SITE_BASE_URL", "") or "").strip().rstrip("/")
        or str(getattr(config, "PUBLIC_MEDIA_BASE_URL", "") or "").strip().rstrip("/")
    )
    if not base_url:
        return ""
    return urljoin(base_url + "/", endpoint.lstrip("/"))


def media_upload_configured() -> bool:
    return bool(media_upload_url() and str(getattr(config, "MEDIA_UPLOAD_TOKEN", "") or "").strip())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guess_mime(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or ""


def detect_mime_by_magic(path: Path) -> str:
    with path.open("rb") as fh:
        header = fh.read(32)
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and b"WEBP" in header[:16]:
        return "image/webp"
    if header[4:8] == b"ftyp":
        return "video/mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    return ""


def _idempotency_key(item_id: int, checksum: str, path: Path) -> str:
    raw = f"{item_id}:{checksum}:{path.name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _metadata_for_item(
    item_id: int,
    item: Mapping[str, Any] | None,
    *,
    checksum: str,
) -> dict[str, str]:
    item = item or {}
    return {
        "item_id": str(item_id),
        "source_url": str(item.get("link") or item.get("source_url") or "").strip(),
        "source_name": str(item.get("source") or item.get("source_name") or "").strip(),
        "checksum": checksum,
        "published_at": str(
            item.get("original_published_at") or item.get("date") or item.get("published_at") or ""
        ).strip(),
        "slug_hint": str(item.get("slug") or item.get("source_item_id") or item_id).strip(),
        "provenance": "parser_news",
        "source_item_id": str(item.get("source_item_id") or "").strip(),
    }


class SiteMediaClient:
    """Upload images (and optionally videos) to Site Blog media endpoint."""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = float(backoff_base)

    def upload_image(
        self,
        path: Path,
        *,
        item_id: int,
        item: Mapping[str, Any] | None = None,
    ) -> MediaUploadResult:
        return self._upload(
            path,
            item_id=item_id,
            item=item,
            allowed_mimes=ALLOWED_IMAGE_MIME_TYPES,
            media_kind="image",
        )

    def upload_video(
        self,
        path: Path,
        *,
        item_id: int,
        item: Mapping[str, Any] | None = None,
    ) -> MediaUploadResult:
        return self._upload(
            path,
            item_id=item_id,
            item=item,
            allowed_mimes=ALLOWED_VIDEO_MIME_TYPES,
            media_kind="video",
        )

    def _upload(
        self,
        path: Path,
        *,
        item_id: int,
        item: Mapping[str, Any] | None,
        allowed_mimes: set[str],
        media_kind: str,
    ) -> MediaUploadResult:
        upload_url = media_upload_url()
        token = str(getattr(config, "MEDIA_UPLOAD_TOKEN", "") or "").strip()
        if not upload_url or not token:
            return MediaUploadResult(ok=False, error="media_upload_not_configured")
        try:
            from utils.safe_http import assert_public_http_url

            assert_public_http_url(upload_url, allow_http=False, resolve_dns=False)
        except Exception as exc:  # noqa: BLE001 — UnsafeURLError or import
            return MediaUploadResult(ok=False, error=f"unsafe_upload_url:{exc}")
        if not path.is_file():
            return MediaUploadResult(ok=False, error="media_file_not_found")

        size = path.stat().st_size
        max_bytes = int(getattr(config, "MEDIA_UPLOAD_MAX_BYTES", 10 * 1024 * 1024))
        if size > max_bytes:
            return MediaUploadResult(ok=False, error="media_file_too_large", bytes=size)

        magic_mime = detect_mime_by_magic(path)
        guessed_mime = _guess_mime(path)
        mime_type = magic_mime or guessed_mime
        if mime_type not in allowed_mimes:
            return MediaUploadResult(
                ok=False,
                error="unsupported_media_type",
                mime_type=mime_type,
                bytes=size,
            )
        if magic_mime and guessed_mime and magic_mime != guessed_mime:
            # Prefer magic; reject obvious extension spoofing when both present and differ.
            if guessed_mime not in allowed_mimes:
                return MediaUploadResult(
                    ok=False,
                    error="mime_extension_mismatch",
                    mime_type=magic_mime,
                    bytes=size,
                )

        checksum = _sha256_file(path)
        metadata = {
            key: value
            for key, value in _metadata_for_item(item_id, item, checksum=checksum).items()
            if value
        }
        idem = _idempotency_key(item_id, checksum, path)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Media-Upload-Token": token,
            "X-Idempotency-Key": idem,
        }
        timeout = float(getattr(config, "MEDIA_UPLOAD_TIMEOUT_SECONDS", 60))
        last_error = "upload_failed"
        last_status: int | None = None
        last_payload: dict[str, Any] = {}

        for attempt in range(1, self.max_attempts + 1):
            try:
                with path.open("rb") as fh:
                    response = requests.post(
                        upload_url,
                        headers=headers,
                        data=metadata,
                        files={"file": (path.name, fh, mime_type)},
                        timeout=timeout,
                    )
            except requests.RequestException as exc:
                last_error = f"request_error:{exc.__class__.__name__}"
                LOGGER.warning(
                    "site_media_upload_retry",
                    extra={
                        "row_id": item_id,
                        "attempt": attempt,
                        "error": last_error,
                        "media_kind": media_kind,
                    },
                )
                if attempt < self.max_attempts:
                    time.sleep(self.backoff_base * (2 ** (attempt - 1)))
                continue

            last_status = int(response.status_code)
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = {}
            last_payload = payload if isinstance(payload, dict) else {}

            if last_status in {200, 201}:
                raw_url = str(
                    last_payload.get("url")
                    or last_payload.get("public_url")
                    or last_payload.get("cover_image_url")
                    or last_payload.get("image_url")
                    or last_payload.get("video_url")
                    or ""
                ).strip()
                public_url = normalize_raw_feed_media_ref(raw_url, media_type=media_kind)
                if not public_url:
                    return MediaUploadResult(
                        ok=False,
                        error="response_url_missing_or_invalid",
                        status_code=last_status,
                        checksum=checksum,
                        mime_type=mime_type,
                        bytes=size,
                        response=last_payload,
                    )
                try:
                    from utils.safe_http import assert_public_http_url

                    if public_url.startswith(("http://", "https://")):
                        assert_public_http_url(public_url, allow_http=True, resolve_dns=False)
                except Exception as exc:  # noqa: BLE001
                    return MediaUploadResult(
                        ok=False,
                        error=f"unsafe_response_url:{exc}",
                        status_code=last_status,
                        checksum=checksum,
                        mime_type=mime_type,
                        bytes=size,
                        response=last_payload,
                    )
                return MediaUploadResult(
                    ok=True,
                    url=public_url,
                    status_code=last_status,
                    checksum=str(last_payload.get("checksum") or checksum),
                    mime_type=str(last_payload.get("mime_type") or mime_type),
                    bytes=int(last_payload.get("bytes") or size),
                    response=last_payload,
                )

            last_error = (
                str(last_payload.get("error") or f"http_{last_status}")
                if last_payload
                else f"http_{last_status}"
            )
            # Retry only transient failures.
            if last_status in {408, 429, 500, 502, 503, 504} and attempt < self.max_attempts:
                LOGGER.warning(
                    "site_media_upload_retry",
                    extra={
                        "row_id": item_id,
                        "attempt": attempt,
                        "status_code": last_status,
                        "error": last_error,
                    },
                )
                time.sleep(self.backoff_base * (2 ** (attempt - 1)))
                continue
            break

        return MediaUploadResult(
            ok=False,
            error=last_error,
            status_code=last_status,
            checksum=checksum,
            mime_type=mime_type,
            bytes=size,
            response=last_payload,
        )


_DEFAULT_CLIENT = SiteMediaClient()


def _upload_image_sync(
    path: Path,
    *,
    item_id: int,
    item: Mapping[str, Any] | None,
) -> MediaUploadResult:
    return _DEFAULT_CLIENT.upload_image(path, item_id=item_id, item=item)


async def upload_cover_image(
    path: Path | str,
    *,
    item_id: int,
    item: Mapping[str, Any] | None = None,
) -> MediaUploadResult:
    return await asyncio.to_thread(
        _upload_image_sync,
        Path(path),
        item_id=item_id,
        item=item,
    )


def _split_media_refs(value: object) -> list[str]:
    if isinstance(value, str):
        refs = [part.strip() for part in value.splitlines() if part.strip()]
        decoded = decode_media_payload(value)
        if decoded is not value and not isinstance(decoded, str):
            refs.extend(candidate for _kind, candidate in iter_media_candidates(decoded))
        return refs
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _iter_local_cover_paths(item: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for key in ("cover_image_url", "image_url", "images", "raw_media", "media_json"):
        if key in {"raw_media", "media_json"}:
            candidates = [
                raw
                for media_type, raw in iter_media_candidates(item.get(key))
                if not is_video_ref(raw, media_type=media_type)
            ]
        else:
            candidates = _split_media_refs(item.get(key))
        for candidate in candidates:
            path = media_ref_to_local_path(candidate)
            if path is None:
                normalized = normalize_media_ref(candidate, media_type="image")
                if not normalized or not is_image_ref(normalized, media_type="image"):
                    continue
                path = media_ref_to_local_path(normalized)
            else:
                normalized = normalize_media_ref(candidate, media_type="image")
            if not normalized or not is_image_ref(normalized, media_type="image"):
                continue
            if path is None:
                continue
            key_path = str(path.resolve())
            if key_path in seen:
                continue
            seen.add(key_path)
            paths.append(path)
    return paths


def find_local_cover_paths(item: Mapping[str, Any]) -> list[Path]:
    return _iter_local_cover_paths(item)


def _prepend_ref(existing: object, new_ref: str) -> str:
    refs = [new_ref]
    for candidate in _split_media_refs(existing):
        normalized = normalize_media_ref(candidate, media_type="image")
        if normalized and normalized not in refs:
            refs.append(normalized)
    return "\n".join(refs)


async def prepare_item_media_for_raw_feed(
    item_id: int,
    item: Mapping[str, Any],
) -> tuple[dict[str, Any], MediaUploadResult | None]:
    out = dict(item)
    paths = _iter_local_cover_paths(out)
    existing_cover = extract_raw_feed_cover_image_url(out, prefer_largest=True)
    if existing_cover:
        local_cover_urls = {
            normalized
            for path in paths
            for normalized in [normalize_raw_feed_media_ref(str(path), media_type="image")]
            if normalized
        }
        parsed = urlparse(existing_cover)
        if existing_cover not in local_cover_urls and not parsed.path.startswith("/static/downloads/"):
            return out, None
    if existing_cover and not media_upload_configured():
        return out, None
    if not media_upload_configured():
        return out, None
    if not paths:
        return out, None

    result = await upload_cover_image(paths[0], item_id=item_id, item=out)
    if not result.ok:
        endpoint = media_upload_url()
        LOGGER.warning(
            "media upload failed for item %s path=%s endpoint=%s status=%s error=%s",
            item_id,
            paths[0],
            endpoint,
            result.status_code,
            result.error,
        )
        return out, result

    out["cover_image_url"] = result.url
    out["image_url"] = result.url
    out["images"] = _prepend_ref(out.get("images"), result.url)
    return out, result


async def maybe_autoupload_local_cover_and_sync_sheet(
    repo: Any,
    item_id: int,
    *,
    user_id: int | None = None,
    username: str | None = None,
    trigger: str = "owner_comment",
) -> MediaUploadResult | None:
    if not media_upload_configured():
        return None

    item = await repo.get_item(item_id)
    if not item:
        return None

    out, upload_result = await prepare_item_media_for_raw_feed(item_id, item)
    if upload_result is None:
        return None

    if not upload_result.ok:
        await repo.log_event(
            item_id,
            "warning",
            "owner_cover_auto_upload_failed",
            {
                "trigger": trigger,
                "error": upload_result.error,
                "status_code": upload_result.status_code,
                "user_id": user_id,
                "username": username,
            },
        )
        return upload_result

    prev_images = str(item.get("images") or "").strip()
    next_images = str(out.get("images") or "").strip()
    if next_images and next_images != prev_images:
        await repo.update_item_media(
            item_id,
            images=next_images,
            videos=item.get("videos"),
        )

    from services.raw_feed_sync import sync_media_fields

    item_after = await repo.get_item(item_id)
    synced = bool(item_after and await sync_media_fields(item_after))

    await repo.log_event(
        item_id,
        "info",
        "owner_cover_auto_uploaded",
        {
            "trigger": trigger,
            "cover_url": upload_result.url,
            "sheet_synced": synced,
            "user_id": user_id,
            "username": username,
        },
    )
    return upload_result


__all__ = [
    "MediaUploadResult",
    "SiteMediaClient",
    "detect_mime_by_magic",
    "find_local_cover_paths",
    "maybe_autoupload_local_cover_and_sync_sheet",
    "media_upload_configured",
    "media_upload_url",
    "prepare_item_media_for_raw_feed",
    "upload_cover_image",
]

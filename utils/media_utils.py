from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlparse

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv")
TELEGRAM_POST_HOSTS = {"t.me", "telegram.me"}

IMAGE_KEYS = (
    "cover_image_url",
    "image_url",
    "thumbnail_url",
    "poster_url",
    "thumbnail",
    "poster",
)
MEDIA_URL_KEYS = (
    *IMAGE_KEYS,
    "file_url",
    "secure_url",
    "url",
    "src",
    "href",
    "path",
)
NON_MEDIA_REFERENCE_KEYS = {"post_url", "source_url", "canonical_url"}
MEDIA_STATUS_OK = {"image_ready", "video_ready", "external_video", "missing", "ok", "cached", "external"}
MEDIA_STATUS_V2 = {
    "image_ready",
    "video_ready",
    "external_video",
    "missing",
    "failed",
    "unsupported",
}


def _strip_www(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _path_has_extension(value: str, extensions: tuple[str, ...]) -> bool:
    path = urlparse(value).path if "://" in value else value
    return Path(path.replace("\\", "/")).suffix.lower() in extensions


def _raw_feed_media_settings() -> tuple[str, str, bool]:
    try:
        from config.settings import config
    except Exception:
        return "", "", False
    public_media_base_url = str(getattr(config, "PUBLIC_MEDIA_BASE_URL", "") or "").strip().rstrip("/")
    site_base_url = str(getattr(config, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    allow_relative_static = bool(
        getattr(config, "ALLOW_RELATIVE_STATIC_MEDIA_IN_RAW_FEED", False)
    )
    return public_media_base_url, site_base_url, allow_relative_static


def _looks_like_local_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = text.replace("\\", "/")
    if text.startswith("file:"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    return normalized.startswith(("./", "../", "downloads/", "media/", "static/"))


def _static_url_for_raw_feed(static_url: str) -> str:
    if not static_url.startswith("/static/"):
        return ""
    public_media_base_url, site_base_url, allow_relative_static = _raw_feed_media_settings()
    if static_url.startswith("/static/downloads/"):
        if public_media_base_url:
            return f"{public_media_base_url}{static_url}"
        if allow_relative_static:
            return static_url
        return ""
    base_url = public_media_base_url or site_base_url
    if base_url:
        return f"{base_url}{static_url}"
    if allow_relative_static:
        return static_url
    return ""


def is_telegram_post_url(value: object) -> bool:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    parsed = urlparse(text)
    host = _strip_www(parsed.netloc)
    if host not in TELEGRAM_POST_HOSTS:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return False
    return parts[-1].isdigit()


def is_telegram_url(value: object) -> bool:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    parsed = urlparse(text)
    return _strip_www(parsed.netloc) in TELEGRAM_POST_HOSTS


def is_image_ref(value: object, *, media_type: str | None = None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    kind = str(media_type or "").strip().lower()
    if kind in {"image", "photo", "thumbnail", "poster"}:
        return True
    return _path_has_extension(text, IMAGE_EXTENSIONS)


def is_video_ref(value: object, *, media_type: str | None = None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    kind = str(media_type or "").strip().lower()
    if kind.startswith("video"):
        return True
    return _path_has_extension(text, VIDEO_EXTENSIONS)


def media_path_to_public_url(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/static/"):
        return text
    if text.startswith(("http://", "https://")):
        return "" if is_telegram_url(text) else text

    path = Path(text)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except Exception:
            rel = path.name
    else:
        rel = text.lstrip("./")

    rel = rel.replace("\\", "/").lstrip("/")
    if not _path_has_extension(rel, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS):
        return ""
    if rel.startswith("static/"):
        return "/" + rel
    return "/static/" + rel


def media_ref_to_local_path(value: object) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith(("http://", "https://")) or is_telegram_url(text):
        return None
    if text.startswith("/static/"):
        candidate = Path(text.removeprefix("/static/").lstrip("/"))
    elif text.startswith("static/"):
        candidate = Path(text.removeprefix("static/").lstrip("/"))
    else:
        candidate = Path(text)
    if not _path_has_extension(str(candidate), IMAGE_EXTENSIONS + VIDEO_EXTENSIONS):
        return None
    return candidate if candidate.is_file() else None


def _iter_raw_media_candidate_values(item: Mapping[str, Any]) -> Iterable[tuple[str | None, str]]:
    for key in (
        "cover_image_url",
        "image_url",
        "thumbnail_url",
        "poster_url",
        "video_preview_image_url",
        "images",
        "raw_media",
        "media_json",
    ):
        if key in {"raw_media", "media_json"}:
            yield from iter_media_candidates(item.get(key))
            continue
        value = item.get(key)
        if isinstance(value, str):
            for part in value.splitlines():
                part = part.strip()
                if part:
                    yield ("image", part)
        elif isinstance(value, (list, tuple, set)):
            for part in value:
                text = str(part or "").strip()
                if text:
                    yield ("image", text)


def extract_source_media_url(item: Mapping[str, Any]) -> str:
    """Return the first original media reference for diagnostics.

    This value is not necessarily safe for the website. It is intentionally
    separate from cover_image_url so operators can see what the parser received
    before upload/normalization.
    """
    explicit = str(item.get("source_media_url") or "").strip()
    if explicit:
        return explicit

    final_cover_candidates = {
        str(item.get("cover_image_url") or "").strip(),
        str(item.get("image_url") or "").strip(),
    }
    for _media_type, candidate in _iter_raw_media_candidate_values(item):
        text = str(candidate or "").strip()
        if text and text not in final_cover_candidates:
            return text

    raw_html = str(item.get("raw_html") or "").strip()
    if not raw_html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for selector, attr in (
        ("meta[property='og:image'][content]", "content"),
        ("meta[name='twitter:image'][content]", "content"),
        ("img[src]", "src"),
        ("img[data-src]", "data-src"),
        ("img[data-lazy-src]", "data-lazy-src"),
    ):
        for node in soup.select(selector):
            value = str(node.get(attr) or "").strip()
            if value:
                return value
    return ""


def extract_cover_image_path(item: Mapping[str, Any]) -> str:
    """Return a local cover path if the parser has one."""
    for _media_type, candidate in _iter_raw_media_candidate_values(item):
        path = media_ref_to_local_path(candidate)
        if path is not None:
            return str(path)
    return ""


def _url_host_matches_base(value: str, base_url: str) -> bool:
    if not value or not base_url:
        return False
    parsed = urlparse(value)
    base = urlparse(base_url)
    return _strip_www(parsed.netloc) == _strip_www(base.netloc)


def _public_media_status(value: str) -> str:
    if not value:
        return ""
    public_media_base_url, site_base_url, _allow_relative_static = _raw_feed_media_settings()
    if value.startswith("/static/"):
        return "image_ready"
    if _url_host_matches_base(value, public_media_base_url) or _url_host_matches_base(value, site_base_url):
        return "image_ready"
    return "image_ready"


@dataclass(slots=True)
class MediaContractDiagnostic:
    cover_image_url: str = ""
    cover_image_path: str = ""
    source_media_url: str = ""
    media_status: str = "missing"
    media_error: str = ""
    media_type: str = ""

    def as_fields(self) -> dict[str, str]:
        return {
            "cover_image_path": self.cover_image_path,
            "source_media_url": self.source_media_url,
            "media_status": self.media_status,
            "media_error": self.media_error,
        }


def build_media_contract_diagnostic(
    item: Mapping[str, Any],
    *,
    upload_error: str = "",
) -> MediaContractDiagnostic:
    """Classify media state for raw_feed exports.

    Statuses (v2): image_ready | video_ready | external_video | missing | failed | unsupported
    """
    from utils.video_providers import resolve_video_media

    cover = extract_raw_feed_cover_image_url(item, prefer_largest=True)
    source_media = extract_source_media_url(item)
    local_path = extract_cover_image_path(item)
    upload_error = str(upload_error or "").strip()
    video = resolve_video_media(item, poster_url=cover)

    if video.media_status == "unsupported":
        return MediaContractDiagnostic(
            cover_image_url=cover,
            cover_image_path=local_path,
            source_media_url=video.source_media_url or source_media,
            media_status="unsupported",
            media_error="unsupported_video_provider",
            media_type=video.media_type or "unsupported",
        )

    if video.media_status in {"external_video", "video_ready"}:
        return MediaContractDiagnostic(
            cover_image_url=cover or video.poster_url,
            cover_image_path=local_path,
            source_media_url=video.source_media_url or source_media,
            media_status=video.media_status,
            media_error="",
            media_type=video.media_type or video.media_status,
        )

    if video.media_status == "failed":
        return MediaContractDiagnostic(
            cover_image_url=cover,
            cover_image_path=local_path,
            source_media_url=video.source_media_url or source_media,
            media_status="failed",
            media_error="video_without_public_url",
            media_type=video.media_type or "video",
        )

    if cover:
        return MediaContractDiagnostic(
            cover_image_url=cover,
            cover_image_path=local_path,
            source_media_url=source_media,
            media_status=_public_media_status(cover) or "image_ready",
            media_error="",
            media_type="image",
        )

    if upload_error:
        return MediaContractDiagnostic(
            cover_image_url="",
            cover_image_path=local_path,
            source_media_url=source_media,
            media_status="failed",
            media_error=upload_error,
            media_type="",
        )

    if local_path:
        return MediaContractDiagnostic(
            cover_image_url="",
            cover_image_path=local_path,
            source_media_url=source_media,
            media_status="failed",
            media_error="local_media_without_public_url",
            media_type="image",
        )

    if source_media:
        if is_telegram_url(source_media):
            error = "source_media_is_telegram_page_url"
            status = "unsupported"
        elif _looks_like_local_path(source_media):
            error = "source_media_is_local_path"
            status = "failed"
        else:
            error = "source_media_without_public_cover"
            status = "failed"
        return MediaContractDiagnostic(
            cover_image_url="",
            cover_image_path="",
            source_media_url=source_media,
            media_status=status,
            media_error=error,
            media_type="",
        )

    return MediaContractDiagnostic(
        cover_image_url="",
        cover_image_path="",
        source_media_url="",
        media_status="missing",
        media_error="no_media_found",
        media_type="",
    )


def media_contract_is_publishable(item: Mapping[str, Any]) -> bool:
    status = str(item.get("media_status") or "").strip().lower()
    if not status:
        return True
    if status in {"failed", "unsupported"}:
        return False
    return status in MEDIA_STATUS_OK or status in MEDIA_STATUS_V2


def media_path_to_raw_feed_url(value: object) -> str:
    """Return a browser-usable raw_feed media URL, or empty if it is only local.

    raw_feed is consumed by the website in a browser, so local Parser paths and
    relative /static paths are unsafe unless the deployment explicitly exposes
    them via PUBLIC_MEDIA_BASE_URL or allows same-domain /static refs.
    """
    return normalize_raw_feed_media_ref(value)


def normalize_media_ref(
    value: object,
    *,
    base_url: str = "",
    media_type: str | None = None,
) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if is_telegram_url(text):
        return ""
    if text.startswith("/static/"):
        return text
    if text.startswith(("http://", "https://")):
        return text
    if base_url:
        resolved = urljoin(base_url, text)
        if not is_telegram_url(resolved) and resolved.startswith(("http://", "https://")):
            return resolved
    if _path_has_extension(text, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS):
        public_url = media_path_to_public_url(text)
        if public_url:
            return public_url
    return ""


def normalize_raw_feed_media_ref(
    value: object,
    *,
    base_url: str = "",
    media_type: str | None = None,
) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    if is_telegram_url(text):
        return ""
    if text.startswith("/static/"):
        return _static_url_for_raw_feed(text)
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = _strip_www(parsed.hostname or parsed.netloc)
        if host in {"127.0.0.1", "localhost"} and parsed.path.startswith("/static/downloads/"):
            return ""
        return text
    if base_url and not _looks_like_local_path(text):
        resolved = urljoin(base_url, text)
        if resolved.startswith(("http://", "https://")) and not is_telegram_url(resolved):
            return resolved
    if _path_has_extension(text, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS):
        local_public = media_path_to_public_url(text)
        if local_public.startswith("/static/"):
            return _static_url_for_raw_feed(local_public)
    return ""


def decode_media_payload(payload: object) -> object | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return payload


def iter_media_candidates(payload: object) -> Iterable[tuple[str | None, str]]:
    decoded = decode_media_payload(payload)
    if decoded is None:
        return
    yield from _iter_media_candidates_from_obj(decoded)


def _iter_media_candidates_from_obj(value: object) -> Iterable[tuple[str | None, str]]:
    if isinstance(value, Mapping):
        explicit_type = str(value.get("type") or "").strip().lower() or None
        for key in MEDIA_URL_KEYS:
            if key not in value:
                continue
            raw_value = value.get(key)
            if raw_value is None:
                continue
            key_type = "image" if key in IMAGE_KEYS else explicit_type
            yield (key_type, str(raw_value))
        for nested_key in ("media", "items", "images", "videos"):
            nested = value.get(nested_key)
            if nested is not None:
                yield from _iter_media_candidates_from_obj(nested)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_media_candidates_from_obj(item)
        return
    text = str(value or "").strip()
    if not text:
        return
    for part in text.splitlines():
        part = part.strip()
        if part:
            yield (None, part)


Normalizer = Callable[..., str]


def extract_cover_image_url(
    item: Mapping[str, Any],
    *,
    prefer_largest: bool = False,
) -> str:
    return _extract_cover_image_url(
        item,
        prefer_largest=prefer_largest,
        normalizer=normalize_media_ref,
    )


def extract_raw_feed_cover_image_url(
    item: Mapping[str, Any],
    *,
    prefer_largest: bool = False,
) -> str:
    return _extract_cover_image_url(
        item,
        prefer_largest=prefer_largest,
        normalizer=normalize_raw_feed_media_ref,
    )


def _extract_cover_image_url(
    item: Mapping[str, Any],
    *,
    prefer_largest: bool = False,
    normalizer: Normalizer,
) -> str:
    del prefer_largest  # Current contract is first valid image candidate.

    for key in ("cover_image_url", "image_url"):
        candidate = normalizer(item.get(key), media_type="image")
        if candidate and is_image_ref(candidate, media_type="image"):
            return candidate

    for key in ("images", "raw_media", "media_json"):
        for media_type, raw_candidate in iter_media_candidates(item.get(key)):
            candidate = normalizer(raw_candidate, media_type=media_type)
            if not candidate:
                continue
            if is_image_ref(candidate, media_type=media_type) and not is_video_ref(
                candidate, media_type=media_type
            ):
                return candidate

    raw_html = str(item.get("raw_html") or "").strip()
    if raw_html:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            BeautifulSoup = None  # type: ignore[assignment]
        if BeautifulSoup is not None:
            soup = BeautifulSoup(raw_html, "html.parser")
            for selector, attr in (
                ("meta[property='og:image'][content]", "content"),
                ("meta[name='twitter:image'][content]", "content"),
                ("img[src]", "src"),
                ("img[data-src]", "data-src"),
                ("img[data-lazy-src]", "data-lazy-src"),
            ):
                for node in soup.select(selector):
                    candidate = normalizer(
                        node.get(attr),
                        base_url=str(item.get("source_url") or item.get("link") or ""),
                        media_type="image",
                    )
                    if candidate and is_image_ref(candidate, media_type="image"):
                        return candidate
    return ""


def sanitize_media_json_payload(payload: object, *, for_raw_feed: bool = False) -> str:
    sanitized = _sanitize_media_json_obj(decode_media_payload(payload), for_raw_feed=for_raw_feed)
    if sanitized in (None, "", [], {}):
        return ""
    if isinstance(sanitized, str):
        return sanitized
    return json.dumps(sanitized, ensure_ascii=False)


def _sanitize_media_json_obj(value: object, *, for_raw_feed: bool = False) -> object | None:
    normalizer = normalize_raw_feed_media_ref if for_raw_feed else normalize_media_ref
    if value is None:
        return None
    if isinstance(value, list):
        result = [_sanitize_media_json_obj(item, for_raw_feed=for_raw_feed) for item in value]
        return [item for item in result if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return _sanitize_media_json_obj(list(value), for_raw_feed=for_raw_feed)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, raw_value in value.items():
            if key in NON_MEDIA_REFERENCE_KEYS:
                result[key] = raw_value
                continue
            if key in MEDIA_URL_KEYS:
                media_type = "image" if key in IMAGE_KEYS else str(value.get("type") or "")
                normalized = normalizer(raw_value, media_type=media_type)
                if normalized:
                    target_key = "url" if key == "path" else key
                    result[target_key] = normalized
                elif is_telegram_post_url(raw_value):
                    result.setdefault("post_url", str(raw_value).strip())
                continue
            if isinstance(raw_value, (dict, list, tuple)):
                nested = _sanitize_media_json_obj(raw_value, for_raw_feed=for_raw_feed)
                if nested not in (None, "", [], {}):
                    result[key] = nested
            else:
                result[key] = raw_value
        return result
    if isinstance(value, str):
        normalized = normalizer(value)
        return normalized or None
    return value


def sanitize_raw_media_payload(payload: object, *, for_raw_feed: bool = False) -> str:
    sanitized = _sanitize_raw_media_obj(decode_media_payload(payload), for_raw_feed=for_raw_feed)
    if not sanitized:
        return ""
    if len(sanitized) == 1:
        return sanitized[0]
    return json.dumps(sanitized, ensure_ascii=False)


def sanitize_raw_media_contract_payload(payload: object) -> str:
    decoded = decode_media_payload(payload)
    sanitized = _sanitize_raw_media_obj(decoded, for_raw_feed=True)
    if not sanitized:
        return ""
    if isinstance(decoded, (list, tuple, set)):
        return json.dumps(sanitized, ensure_ascii=False)
    if isinstance(payload, str) and payload.strip().startswith("["):
        return json.dumps(sanitized, ensure_ascii=False)
    if len(sanitized) == 1:
        return sanitized[0]
    return json.dumps(sanitized, ensure_ascii=False)


def _sanitize_raw_media_obj(value: object, *, for_raw_feed: bool = False) -> list[str]:
    normalizer = normalize_raw_feed_media_ref if for_raw_feed else normalize_media_ref
    if value is None:
        return []
    if isinstance(value, Mapping):
        refs: list[str] = []
        media_type = str(value.get("type") or "").strip().lower() or None
        for key in MEDIA_URL_KEYS:
            if key in value:
                normalized = normalizer(value.get(key), media_type=media_type)
                if normalized:
                    refs.append(normalized)
        return _dedupe(refs)
    if isinstance(value, (list, tuple, set)):
        refs: list[str] = []
        for item in value:
            refs.extend(_sanitize_raw_media_obj(item, for_raw_feed=for_raw_feed))
        return _dedupe(refs)
    normalized = normalizer(value)
    return [normalized] if normalized else []


def normalize_media_contract_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize media fields before exporting a row to raw_feed.

    Telegram permalinks are useful as source links, but they are HTML pages,
    not media files. Keep them out of cover/image/raw_media fields.
    """
    out = dict(item)

    if "raw_media" in out:
        out["raw_media"] = sanitize_raw_media_contract_payload(out.get("raw_media"))
    if "media_json" in out:
        out["media_json"] = sanitize_media_json_payload(out.get("media_json"), for_raw_feed=True)

    if "image_url" in out:
        image_url = normalize_raw_feed_media_ref(out.get("image_url"), media_type="image")
        out["image_url"] = image_url if image_url and is_image_ref(image_url, media_type="image") else ""

    for key in ("video_url", "embed_url", "video_embed_url"):
        if key in out:
            video_url = normalize_raw_feed_media_ref(out.get(key), media_type="video")
            out[key] = video_url if video_url and is_video_ref(video_url, media_type="video") else ""

    for key in ("poster_url", "thumbnail_url", "video_preview_image_url"):
        if key in out:
            image_url = normalize_raw_feed_media_ref(out.get(key), media_type="image")
            out[key] = image_url if image_url and is_image_ref(image_url, media_type="image") else ""

    cover = extract_raw_feed_cover_image_url(out, prefer_largest=True)
    if cover or "cover_image_url" in out:
        out["cover_image_url"] = cover

    return out


def validate_media_contract_fields(item: Mapping[str, Any]) -> tuple[bool, str]:
    for field in ("cover_image_url", "image_url"):
        value = str(item.get(field) or "").strip()
        if not value:
            continue
        if is_telegram_url(value):
            return False, f"{field} contains Telegram page URL"
        if not normalize_raw_feed_media_ref(value, media_type="image"):
            return False, f"{field} contains non-public media URL"

    for field in ("raw_media", "media_json"):
        for _media_type, value in iter_media_candidates(item.get(field)):
            if is_telegram_url(value):
                return False, f"{field} contains Telegram page URL"
            if value and not normalize_raw_feed_media_ref(value, media_type=_media_type):
                return False, f"{field} contains non-public media URL"

    return True, ""


def media_kind_from_path(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if _path_has_extension(text, IMAGE_EXTENSIONS):
        return "image"
    if _path_has_extension(text, VIDEO_EXTENSIONS):
        return "video"
    return None


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


async def download_media(message: Any, download_dir: str = "downloads/") -> tuple[str | None, str | None]:
    """Compatibility wrapper for older imports.

    Deterministic path by message.id. Existing non-empty file → skip (no Telethon ``(1)`` clones).
    """
    try:
        if not getattr(message, "media", None):
            return None, None
        from pathlib import Path

        out_dir = Path(download_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        msg_id = getattr(message, "id", None) or "media"
        ext = ".bin"
        if getattr(message, "photo", None):
            ext = ".jpg"
        elif getattr(message, "video", None):
            ext = ".mp4"
        elif getattr(message, "document", None):
            mime = str(getattr(message.document, "mime_type", "") or "")
            if "/" in mime:
                ext = "." + mime.split("/", 1)[-1].split(";")[0] or ".bin"
        target = out_dir / f"{msg_id}{ext}"
        if target.exists() and target.stat().st_size > 0:
            return str(target), media_kind_from_path(str(target))
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        path = await message.download_media(file=str(target))
        if not isinstance(path, str) or not path.strip():
            return None, None
        return path.strip(), media_kind_from_path(path)
    except Exception:
        return None, None

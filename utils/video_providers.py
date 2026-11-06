"""Resolve video provider URLs into canonical video_url / embed_url / media_type."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse, urlunparse

from utils.media_utils import (
    VIDEO_EXTENSIONS,
    is_telegram_url,
    iter_media_candidates,
    normalize_raw_feed_media_ref,
)

DIRECT_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS


def _looks_like_video_candidate(raw: object, media_type: str | None = None) -> bool:
    kind = str(media_type or "").strip().lower()
    if kind.startswith("video"):
        return True
    text = str(raw or "").strip()
    if not text:
        return False
    if _path_ext(text) in DIRECT_VIDEO_EXTENSIONS:
        return True
    return bool(detect_provider(text))


@dataclass(slots=True)
class VideoMediaFields:
    video_url: str = ""
    embed_url: str = ""
    poster_url: str = ""
    media_type: str = ""
    media_status: str = ""
    provider: str = ""
    source_media_url: str = ""

    def as_fields(self) -> dict[str, str]:
        return {
            "video_url": self.video_url,
            "embed_url": self.embed_url,
            "video_embed_url": self.embed_url,
            "poster_url": self.poster_url,
            "thumbnail_url": self.poster_url,
            "video_preview_image_url": self.poster_url,
            "source_media_url": self.source_media_url or self.video_url,
        }


def _strip_www(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _path_ext(url: str) -> str:
    path = urlparse(url).path if "://" in url else url
    idx = path.rfind(".")
    return path[idx:].lower() if idx >= 0 else ""


def is_direct_video_file(url: str) -> bool:
    return _path_ext(url) in DIRECT_VIDEO_EXTENSIONS


def is_iframe_provider_url(url: str) -> bool:
    return detect_provider(url) in {
        "youtube",
        "vk",
        "rutube",
        "vimeo",
        "kinescope",
    }


def detect_provider(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if is_telegram_url(text):
        return "telegram"
    host = _strip_www(urlparse(text).netloc)
    if host in {"youtube.com", "youtu.be", "m.youtube.com", "youtube-nocookie.com"}:
        return "youtube"
    if host in {"vk.com", "vk.ru", "m.vk.com"} and ("/video" in text or "video_ext" in text):
        return "vk"
    if "rutube.ru" in host:
        return "rutube"
    if host in {"vimeo.com", "player.vimeo.com"}:
        return "vimeo"
    if "kinescope.io" in host:
        return "kinescope"
    if is_direct_video_file(text):
        return "file"
    return ""


def _youtube_id(url: str) -> str:
    parsed = urlparse(url)
    host = _strip_www(parsed.netloc)
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0]
    if parsed.path.startswith("/embed/"):
        return parsed.path.split("/embed/", 1)[1].split("/", 1)[0]
    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    if "/watch" in parsed.path:
        return (parse_qs(parsed.query).get("v") or [""])[0]
    return ""


def _vk_embed(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if "video_ext.php" in parsed.path:
        return url, url
    # vk.com/video-123_456 or video123_456
    match = re.search(r"video(-?\d+)_(\d+)", parsed.path)
    if not match:
        oid = (parse_qs(parsed.query).get("oid") or [""])[0]
        vid = (parse_qs(parsed.query).get("id") or [""])[0]
        if oid and vid:
            embed = f"https://vk.com/video_ext.php?oid={oid}&id={vid}&hd=2"
            return url, embed
        return url, ""
    oid, vid = match.group(1), match.group(2)
    embed = f"https://vk.com/video_ext.php?oid={oid}&id={vid}&hd=2"
    return url, embed


def _rutube_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if "embed" in parts:
        idx = parts.index("embed")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "video" in parts:
        idx = parts.index("video")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1] if parts else ""


def _vimeo_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(parts):
        if part.isdigit():
            return part
    return ""


def _kinescope_embed(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if "/embed/" in parsed.path:
        return url, url
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return url, ""
    video_id = parts[-1]
    embed = urlunparse(("https", "kinescope.io", f"/embed/{video_id}", "", "", ""))
    return url, embed


def build_embed_url(url: str) -> tuple[str, str, str]:
    """Return (video_url, embed_url, provider)."""
    text = str(url or "").strip()
    if not text:
        return "", "", ""
    provider = detect_provider(text)
    if provider == "youtube":
        vid = _youtube_id(text)
        if not vid:
            return text, "", "youtube"
        watch = f"https://www.youtube.com/watch?v={vid}"
        embed = f"https://www.youtube.com/embed/{vid}"
        return watch, embed, "youtube"
    if provider == "vk":
        video_url, embed = _vk_embed(text)
        return video_url, embed, "vk"
    if provider == "rutube":
        vid = _rutube_id(text)
        if not vid:
            return text, "", "rutube"
        return f"https://rutube.ru/video/{vid}/", f"https://rutube.ru/play/embed/{vid}", "rutube"
    if provider == "vimeo":
        vid = _vimeo_id(text)
        if not vid:
            return text, "", "vimeo"
        return f"https://vimeo.com/{vid}", f"https://player.vimeo.com/video/{vid}", "vimeo"
    if provider == "kinescope":
        video_url, embed = _kinescope_embed(text)
        return video_url, embed, "kinescope"
    if provider == "file":
        return text, "", "file"
    if provider == "telegram":
        # Telegram post pages are not playable media files.
        return "", "", "telegram"
    return text, "", provider or "unknown"


def _candidate_urls(item: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def _add(value: object) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        refs.append(text)

    for key in ("video_url", "embed_url", "video_embed_url", "source_media_url"):
        _add(item.get(key))
    for value in str(item.get("videos") or "").splitlines():
        _add(value)
    for media_type, raw in iter_media_candidates(item.get("media_json")):
        if _looks_like_video_candidate(raw, media_type) or is_iframe_provider_url(str(raw)):
            _add(raw)
    for media_type, raw in iter_media_candidates(item.get("raw_media")):
        if _looks_like_video_candidate(raw, media_type):
            _add(raw)
    return refs


def resolve_video_media(
    item: Mapping[str, Any],
    *,
    poster_url: str = "",
) -> VideoMediaFields:
    poster = normalize_raw_feed_media_ref(poster_url or item.get("poster_url") or item.get("cover_image_url") or "", media_type="image")
    for candidate in _candidate_urls(item):
        provider = detect_provider(candidate)
        if not provider:
            # Unknown http URL that looks like video by extension handled above.
            continue
        if provider == "telegram":
            return VideoMediaFields(
                media_type="unsupported",
                media_status="unsupported",
                provider="telegram",
                source_media_url=candidate,
                poster_url=poster,
            )
        if provider == "unknown":
            continue
        video_url, embed_url, resolved_provider = build_embed_url(candidate)
        if resolved_provider in {"youtube", "vk", "rutube", "vimeo", "kinescope"}:
            if not embed_url:
                return VideoMediaFields(
                    video_url=normalize_raw_feed_media_ref(video_url or candidate, media_type="video") or video_url or candidate,
                    embed_url="",
                    poster_url=poster,
                    media_type="external_video",
                    media_status="failed",
                    provider=resolved_provider,
                    source_media_url=candidate,
                )
            return VideoMediaFields(
                video_url=video_url,
                embed_url=embed_url,
                poster_url=poster,
                media_type="external_video",
                media_status="external_video",
                provider=resolved_provider,
                source_media_url=candidate,
            )
        if resolved_provider == "file":
            public = normalize_raw_feed_media_ref(candidate, media_type="video")
            if not public:
                return VideoMediaFields(
                    video_url="",
                    embed_url="",
                    poster_url=poster,
                    media_type="video",
                    media_status="failed",
                    provider="file",
                    source_media_url=candidate,
                )
            return VideoMediaFields(
                video_url=public,
                embed_url="",
                poster_url=poster,
                media_type="video",
                media_status="video_ready",
                provider="file",
                source_media_url=candidate,
            )
    return VideoMediaFields(poster_url=poster)


__all__ = [
    "VideoMediaFields",
    "build_embed_url",
    "detect_provider",
    "is_direct_video_file",
    "is_iframe_provider_url",
    "resolve_video_media",
]

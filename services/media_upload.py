"""Backward-compatible re-exports for Site media upload."""
from __future__ import annotations

from services.site_media_client import (
    MediaUploadResult,
    SiteMediaClient,
    detect_mime_by_magic,
    find_local_cover_paths,
    maybe_autoupload_local_cover_and_sync_sheet,
    media_upload_configured,
    media_upload_url,
    prepare_item_media_for_raw_feed,
    upload_cover_image,
)

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

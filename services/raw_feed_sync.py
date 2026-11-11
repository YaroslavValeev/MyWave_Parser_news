"""Synchronization helpers between SQLite runtime state and Google Sheets raw_feed."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from config.settings import config
from services.media_upload import prepare_item_media_for_raw_feed
from services.site_cache import invalidate_site_blog_cache
from utils.card_preview_text import (
    PUBLIC_TITLE_MAX_LEN,
    lead_from_text,
    normalize_public_title,
    normalize_publication_text,
    public_text_quality_issues,
    strip_embedded_video_urls,
    to_card_preview_text,
)
from utils.item_context import get_item_text_context
from utils.owner_content import build_fallback_merged_text, strip_author_meta_labels
from utils.media_utils import (
    build_media_contract_diagnostic,
    extract_raw_feed_cover_image_url,
    is_video_ref,
    iter_media_candidates,
    media_contract_is_publishable,
    normalize_raw_feed_media_ref,
    normalize_media_contract_fields,
    sanitize_media_json_payload,
    sanitize_raw_media_payload,
)
from utils.russian_summary import ensure_russian_summary
from utils.sheet_gateway import (
    append_raw_feed_rows,
    init_sheet_gateway,
    update_item,
)
from utils.video_providers import resolve_video_media

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_source(item: Mapping[str, Any]) -> tuple[str, str]:
    source = str(item.get("source") or "").strip()
    if ":" in source:
        source_type, source_name = source.split(":", 1)
        return source_type.strip(), source_name.strip()
    source_type = str(item.get("source_type") or "").strip()
    source_name = str(item.get("source_name") or source or "").strip()
    return source_type, source_name


def _normalize_raw_media(item: Mapping[str, Any]) -> str:
    raw_media = item.get("raw_media")
    sanitized = sanitize_raw_media_payload(raw_media, for_raw_feed=True)
    if sanitized:
        return sanitized

    refs: list[str] = []
    for key in ("images", "videos"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, str):
            refs.extend([part.strip() for part in value.splitlines() if part.strip()])
        elif isinstance(value, (list, tuple)):
            refs.extend([str(part).strip() for part in value if str(part).strip()])
    normalized_refs = [
        normalized
        for ref in refs
        for normalized in [normalize_raw_feed_media_ref(ref)]
        if normalized
    ]
    if not normalized_refs:
        return ""
    return json.dumps(normalized_refs, ensure_ascii=False)


def _infer_source_type(source_type: str, url: str) -> str:
    normalized = str(source_type or "").strip().lower()
    if normalized:
        return normalized
    host = (urlparse(url).netloc or "").lower()
    if host in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
        return "telegram"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return normalized


def _resolve_source_url(item: Mapping[str, Any]) -> str:
    for key in ("link", "source_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_youtube_video_id(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0]
    if "youtube.com" in host:
        if parsed.path.startswith("/watch"):
            return (parse_qs(parsed.query).get("v") or [""])[0]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/", 1)[1].split("/", 1)[0]
    return ""


def _resolve_source_item_id(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("source_item_id") or "").strip()
    if explicit:
        return explicit

    source_type, _source_name = _split_source(item)
    link = str(item.get("link") or "").strip() or _resolve_source_url(item)
    source_type = _infer_source_type(source_type, link)
    if source_type == "telegram":
        path = urlparse(link).path.strip("/").split("/")
        if path:
            return path[-1]
    if source_type == "youtube":
        vid = _extract_youtube_video_id(link)
        if vid:
            return vid
    if source_type in {"rss", "website"} and link:
        return link

    fallback = str(item.get("id") or item.get("checksum") or "").strip()
    return fallback


def map_sheet_status(local_status: str | None) -> str:
    status = str(local_status or "").strip().lower()
    mapping = {
        "new": "DRAFT",
        "processing": "DRAFT",
        "review": "DRAFT",
        "approved": "READY_TO_PUBLISH",
        "deferred": "DRAFT",
        "ready_to_publish": "READY_TO_PUBLISH",
        "publish_retry": "READY_TO_PUBLISH",
        "published": "PUBLISHED",
        "discarded": "ARCHIVED",
        "expired": "ARCHIVED",
        "error": "DRAFT",
    }
    return mapping.get(status, "DRAFT")


def _normalized_summary(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> str:
    raw = str((nlp or {}).get("summary") or "").strip()
    if not raw:
        return ""
    summary = ensure_russian_summary(
        raw,
        item=item,
        source_text=get_item_text_context(item),
        lang=config.NL_LANG,
        max_len=260,
    )
    return to_card_preview_text(summary, max_len=260)


def compose_final_post_text(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> str:
    nlp = nlp or {}
    merged = strip_author_meta_labels(str(nlp.get("merged_text") or "").strip())
    if merged:
        return normalize_publication_text(merged, preserve_paragraphs=True)
    summary = _normalized_summary(item, nlp)
    notes = str(nlp.get("author_notes") or "").strip()
    if notes:
        return build_fallback_merged_text(
            source_text=summary or str(get_item_text_context(item) or ""),
            author_notes=notes,
            title=str(item.get("raw_title") or item.get("title") or ""),
        )
    if summary:
        return normalize_publication_text(summary, preserve_paragraphs=True)
    return normalize_publication_text(str(get_item_text_context(item) or ""), preserve_paragraphs=True)


def _public_text_payload(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
    *,
    sheet_status: str,
) -> dict[str, Any]:
    title = normalize_public_title(
        item.get("raw_title") or item.get("title") or "",
        max_len=PUBLIC_TITLE_MAX_LEN,
    )
    final_text = strip_embedded_video_urls(compose_final_post_text(item, nlp))
    summary = _normalized_summary(item, nlp)
    excerpt_source = final_text or summary or get_item_text_context(item)
    excerpt = to_card_preview_text(str(excerpt_source or ""), max_len=260)
    lead = lead_from_text(str(excerpt_source or ""))
    source_type, source_name = _split_source(item)
    source_url = _resolve_source_url(item)
    enforce = sheet_status in {"READY_TO_PUBLISH", "PUBLISHED"}
    issues = public_text_quality_issues(
        title=title,
        excerpt=excerpt,
        lead=lead,
        body=final_text,
        source_name=source_name or str(item.get("source_name") or ""),
        source_url=source_url,
        enforce_editorial=enforce,
    )
    publishable = enforce and not issues
    process_status = "needs_cleanup" if issues else "done"
    process_error = ";".join(issues)
    return {
        "raw_title": title,
        "title": title,
        "summary": summary or excerpt,
        "excerpt": excerpt,
        "lead": lead or excerpt,
        "meta_description": to_card_preview_text(str(excerpt_source or ""), max_len=160),
        "og_description": to_card_preview_text(str(excerpt_source or ""), max_len=200),
        "final_posts": final_text,
        "text": final_text,
        "final_version": final_text,
        "content_md": final_text,
        "final_ready": "true" if publishable else "false",
        "process_status": process_status,
        "process_error": process_error,
        "source_type": source_type or str(item.get("source_type") or ""),
        "source_name": source_name or str(item.get("source_name") or ""),
        "source_url": source_url,
    }


def _apply_media_quality_gate(payload: dict[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """Block ready state when parser detected broken mandatory media processing."""
    if media_contract_is_publishable(item):
        return payload
    media_status = str(item.get("media_status") or "").strip()
    media_error = str(item.get("media_error") or "").strip() or media_status or "media_not_ready"
    existing_error = str(payload.get("process_error") or "").strip()
    errors = [part for part in (existing_error, media_error) if part]
    payload["final_ready"] = "false"
    payload["process_status"] = "needs_media"
    payload["process_error"] = ";".join(errors)
    return payload


def _media_export_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "raw_media": item.get("raw_media") or "",
        "media_json": item.get("media_json") or "",
        "cover_image_url": item.get("cover_image_url") or "",
        "image_url": item.get("image_url") or "",
        "video_url": item.get("video_url") or "",
        "embed_url": item.get("embed_url") or "",
        "video_embed_url": item.get("video_embed_url") or "",
        "poster_url": item.get("poster_url") or "",
        "thumbnail_url": item.get("thumbnail_url") or "",
        "video_preview_image_url": item.get("video_preview_image_url") or "",
        "cover_image_path": item.get("cover_image_path") or "",
        "source_media_url": item.get("source_media_url") or "",
        "media_status": item.get("media_status") or "",
        "media_error": item.get("media_error") or "",
    }


def _base_lookup_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "checksum": str(item.get("checksum") or "").strip(),
        "updated_at": _now_iso(),
    }


def build_ingest_row(item_id: int, item: Mapping[str, Any]) -> dict[str, Any]:
    source_type, source_name = _split_source(item)
    raw_title = normalize_public_title(item.get("raw_title") or item.get("title") or "")
    raw_content = normalize_publication_text(
        str(item.get("raw_content") or item.get("content") or ""),
        preserve_paragraphs=True,
    )
    source_url = _resolve_source_url(item)
    source_type = _infer_source_type(source_type, source_url)
    source_value = f"{source_type}:{source_name}".strip(":")
    raw_media = _normalize_raw_media(item)
    media_json = sanitize_media_json_payload(item.get("media_json"), for_raw_feed=True)
    cover_image_url = extract_raw_feed_cover_image_url(
        {
            **item,
            "raw_media": raw_media,
            "media_json": media_json,
            "source_url": source_url,
        },
        prefer_largest=True,
    )
    if not media_json:
        media_json = _media_json_from_item(item, cover_image_url)
    video_aliases = _video_aliases_from_item(
        {
            **item,
            "media_json": media_json,
        },
        cover_image_url,
    )
    media_contract = build_media_contract_diagnostic(
        {
            **item,
            "raw_media": raw_media,
            "media_json": media_json,
            "cover_image_url": cover_image_url,
            "image_url": cover_image_url,
            **video_aliases,
        }
    )
    row = {
        "id": str(item_id),
        "source": source_value,
        "title": raw_title,
        "source_type": source_type,
        "source_name": source_name,
        "source_url": source_url,
        "source_item_id": _resolve_source_item_id(item),
        "created_at": str(item.get("created_at") or _now_iso()),
        "original_published_at": str(item.get("original_published_at") or item.get("date") or ""),
        "updated_at": str(item.get("updated_at") or item.get("created_at") or _now_iso()),
        "status": map_sheet_status(item.get("status")),
        "ingest_status": "ok",
        "raw_title": raw_title,
        "raw_content": raw_content,
        "raw_html": str(item.get("raw_html") or ""),
        "raw_media": raw_media,
        "media_json": media_json,
        "cover_image_url": cover_image_url,
        "image_url": cover_image_url,
        **video_aliases,
        **media_contract.as_fields(),
        "lang": str(item.get("lang") or config.NL_LANG),
        "raw_tags": str(item.get("raw_tags") or ""),
        "checksum": str(item.get("checksum") or "").strip(),
        "parse_error": str(item.get("parse_error") or ""),
        "debug_info": str(item.get("debug_info") or ""),
        "content_format": "text",
        "final_ready": "false",
        "process_status": "",
        "process_error": "",
        "summary": "",
        "questions": "",
        "expert_opinion": "",
        "need_opinion": "true",
        "excerpt": "",
        "lead": "",
        "final_posts": "",
        "text": "",
        "final_version": "",
        "content_md": "",
        "telegram_published": "false",
    }
    return normalize_media_contract_fields(row)


def _media_json_from_item(item: Mapping[str, Any], cover_image_url: str) -> str:
    media_items: list[dict[str, Any]] = []
    if cover_image_url:
        media_items.append({"type": "image", "url": cover_image_url})
    for value in (str(item.get("videos") or "").splitlines()):
        ref = normalize_raw_feed_media_ref(value, media_type="video")
        if ref:
            media: dict[str, Any] = {
                "type": "video",
                "url": ref,
                "video_url": ref,
            }
            if cover_image_url:
                media["poster_url"] = cover_image_url
                media["thumbnail_url"] = cover_image_url
            media_items.append(media)
    return sanitize_media_json_payload(media_items, for_raw_feed=True)


def _video_aliases_from_item(item: Mapping[str, Any], cover_image_url: str) -> dict[str, str]:
    video = resolve_video_media(item, poster_url=cover_image_url)
    fields = video.as_fields()
    if not fields.get("video_url") and not fields.get("embed_url"):
        # Fallback to legacy extraction for already-normalized direct refs.
        video_url = ""
        embed_url = ""
        for media_type, raw_candidate in iter_media_candidates(item.get("media_json")):
            if not is_video_ref(raw_candidate, media_type=media_type):
                continue
            normalized = normalize_raw_feed_media_ref(raw_candidate, media_type="video")
            if normalized:
                video_url = normalized
                break
        if not video_url:
            for value in str(item.get("videos") or "").splitlines():
                normalized = normalize_raw_feed_media_ref(value, media_type="video")
                if normalized:
                    video_url = normalized
                    break
        for key in ("embed_url", "video_embed_url"):
            normalized = normalize_raw_feed_media_ref(item.get(key), media_type="video")
            if normalized:
                embed_url = normalized
                break
        poster = cover_image_url if cover_image_url else ""
        return {
            "video_url": video_url,
            "embed_url": embed_url,
            "video_embed_url": embed_url,
            "poster_url": poster,
            "thumbnail_url": poster,
            "video_preview_image_url": poster,
        }
    return fields


async def _prepare_publishable_item_media(item: Mapping[str, Any]) -> dict[str, Any]:
    item_id = int(item.get("id") or 0)
    prepared_item = dict(item)
    upload_result = None
    if item_id:
        prepared_item, upload_result = await prepare_item_media_for_raw_feed(item_id, item)
        if upload_result and upload_result.ok:
            LOGGER.info("raw_feed media upload succeeded for item %s: %s", item_id, upload_result.url)
    raw_media = _normalize_raw_media(prepared_item)
    cover_image_url = extract_raw_feed_cover_image_url(
        {
            **prepared_item,
            "raw_media": raw_media,
        },
        prefer_largest=True,
    )
    media_json = _media_json_from_item(prepared_item, cover_image_url)
    video_aliases = _video_aliases_from_item(
        {
            **prepared_item,
            "media_json": media_json,
        },
        cover_image_url,
    )
    media_contract = build_media_contract_diagnostic(
        {
            **prepared_item,
            "raw_media": raw_media,
            "media_json": media_json,
            "cover_image_url": cover_image_url,
            "image_url": cover_image_url,
            **video_aliases,
        },
        upload_error=upload_result.error if upload_result and not upload_result.ok else "",
    )
    return normalize_media_contract_fields(
        {
            **prepared_item,
            "raw_media": raw_media,
            "media_json": media_json,
            "cover_image_url": cover_image_url,
            "image_url": cover_image_url,
            **video_aliases,
            **media_contract.as_fields(),
        }
    )


async def _get_doc():
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    if not config.GOOGLE_SHEET_ID or not config.GOOGLE_CREDENTIALS_FILE:
        return None
    return await init_sheet_gateway()


async def sync_ingest_item(item_id: int, item: Mapping[str, Any]) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    try:
        doc = await _get_doc()
        if not doc:
            return False
        prepared_item, upload_result = await prepare_item_media_for_raw_feed(item_id, item)
        if upload_result and upload_result.ok:
            LOGGER.info("raw_feed media upload succeeded for item %s: %s", item_id, upload_result.url)
        written = await append_raw_feed_rows(doc, [build_ingest_row(item_id, prepared_item)])
        return bool(written)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed ingest sync failed for item %s: %s", item_id, exc)
        return False


async def sync_ingest_items_batch(items: list[tuple[int, Mapping[str, Any]]]) -> int:
    prepared: list[dict[str, Any]] = []
    try:
        doc = await _get_doc()
        if not doc:
            return 0
        for item_id, item in items:
            checksum = str(item.get("checksum") or "").strip()
            if not checksum:
                continue
            prepared_item, upload_result = await prepare_item_media_for_raw_feed(item_id, item)
            if upload_result and upload_result.ok:
                LOGGER.info("raw_feed media upload succeeded for item %s: %s", item_id, upload_result.url)
            prepared.append(build_ingest_row(item_id, prepared_item))
        if not prepared:
            return 0
        return int(await append_raw_feed_rows(doc, prepared))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed batch ingest sync failed for %s items: %s", len(prepared), exc)
        return 0


async def sync_nlp_state(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
    *,
    status: str | None = None,
    process_error: str = "",
) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    sheet_status = map_sheet_status(status or item.get("status"))
    summary = _normalized_summary(item, nlp)
    payload = _base_lookup_payload(item)
    payload.update(
        {
            "status": sheet_status,
            "summary": summary,
            "excerpt": to_card_preview_text(summary, max_len=260),
            "questions": json.dumps((nlp or {}).get("questions") or [], ensure_ascii=False),
            "process_status": "error" if process_error else "done",
            "processed_at": _now_iso(),
            "process_error": process_error,
            "need_opinion": "false" if str((nlp or {}).get("author_notes") or "").strip() else "true",
        }
    )
    try:
        doc = await _get_doc()
        if not doc:
            return False
        return bool(await update_item(doc, "raw_feed", payload, lookup_field="checksum"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed NLP sync failed for checksum=%s: %s", checksum, exc)
        return False


async def sync_owner_comment(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    payload = _base_lookup_payload(item)
    payload.update(
        {
            "expert_opinion": str((nlp or {}).get("author_notes") or ""),
            "need_opinion": "false",
            "status": map_sheet_status(item.get("status")),
        }
    )
    try:
        doc = await _get_doc()
        if not doc:
            return False
        return bool(await update_item(doc, "raw_feed", payload, lookup_field="checksum"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed owner comment sync failed for checksum=%s: %s", checksum, exc)
        return False


async def sync_final_text(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    sheet_status = map_sheet_status(item.get("status"))
    payload = _base_lookup_payload(item)
    public_payload = _public_text_payload(item, nlp, sheet_status=sheet_status)
    payload.update(
        {
            "status": sheet_status,
            **public_payload,
        }
    )
    try:
        doc = await _get_doc()
        if not doc:
            return False
        return bool(await update_item(doc, "raw_feed", payload, lookup_field="checksum"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed final text sync failed for checksum=%s: %s", checksum, exc)
        return False


async def sync_media_fields(item: Mapping[str, Any]) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    try:
        doc = await _get_doc()
        if not doc:
            return False
        prepared_item = await _prepare_publishable_item_media(item)
        payload = {
            **_base_lookup_payload(prepared_item),
            **_media_export_payload(prepared_item),
        }
        updated = False
        if await update_item(doc, "raw_feed", payload, lookup_field="checksum"):
            updated = True
        else:
            item_id = int(prepared_item.get("id") or 0)
            if item_id and await update_item(
                doc,
                "raw_feed",
                {**payload, "id": str(item_id)},
                lookup_field="id",
            ):
                updated = True
            elif item_id:
                updated = bool(await append_raw_feed_rows(doc, [build_ingest_row(item_id, prepared_item)]))
        if updated:
            await invalidate_site_blog_cache(
                item_id=int(prepared_item.get("id") or 0) or None,
                reason="raw_feed_media_sync",
            )
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed media sync failed for checksum=%s: %s", checksum, exc)
        return False


async def _update_or_append_publishable_row(
    doc,
    prepared_item: Mapping[str, Any],
    payload: dict[str, Any],
    *,
    append_status: str,
) -> bool:
    if await update_item(doc, "raw_feed", payload, lookup_field="checksum"):
        return True

    item_id = int(prepared_item.get("id") or 0)
    if item_id and await update_item(
        doc,
        "raw_feed",
        {**payload, "id": str(item_id)},
        lookup_field="id",
    ):
        return True

    if not item_id:
        return False

    appended = bool(
        await append_raw_feed_rows(
            doc,
            [
                build_ingest_row(
                    item_id,
                    {
                        **prepared_item,
                        "status": append_status,
                    },
                )
            ],
        )
    )
    if not appended:
        return False
    return bool(await update_item(doc, "raw_feed", payload, lookup_field="checksum"))


async def sync_publication_queue(item: Mapping[str, Any], nlp: Mapping[str, Any] | None) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    prepared_item = await _prepare_publishable_item_media(item)
    public_payload = _apply_media_quality_gate(
        _public_text_payload(prepared_item, nlp, sheet_status="READY_TO_PUBLISH"),
        prepared_item,
    )
    payload = _base_lookup_payload(prepared_item)
    payload.update(
        {
            "status": "READY_TO_PUBLISH",
            **public_payload,
            **_media_export_payload(prepared_item),
        }
    )
    try:
        doc = await _get_doc()
        if not doc:
            return False
        updated = await _update_or_append_publishable_row(
            doc,
            prepared_item,
            payload,
            append_status="ready_to_publish",
        )
        if updated:
            await invalidate_site_blog_cache(
                item_id=int(prepared_item.get("id") or 0) or None,
                reason="raw_feed_ready_to_publish",
            )
        return updated
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed queue sync failed for checksum=%s: %s", checksum, exc)
        return False


async def sync_publication_result(
    item: Mapping[str, Any],
    nlp: Mapping[str, Any] | None,
    *,
    published: bool,
    error: str = "",
) -> bool:
    checksum = str(item.get("checksum") or "").strip()
    if not checksum:
        return False
    prepared_item = await _prepare_publishable_item_media(item)
    payload = _base_lookup_payload(prepared_item)
    if published:
        public_payload = _apply_media_quality_gate(
            _public_text_payload(prepared_item, nlp, sheet_status="PUBLISHED"),
            prepared_item,
        )
        payload.update(
            {
                "status": "PUBLISHED",
                "telegram_published": "true",
                **public_payload,
            }
        )
    else:
        public_payload = _apply_media_quality_gate(
            _public_text_payload(prepared_item, nlp, sheet_status="READY_TO_PUBLISH"),
            prepared_item,
        )
        payload.update(
            {
                "status": "READY_TO_PUBLISH",
                "telegram_published": "false",
                "publish_error": error,
                **public_payload,
            }
        )
    payload.update(
        _media_export_payload(prepared_item)
    )
    try:
        doc = await _get_doc()
        if not doc:
            return False
        updated = await _update_or_append_publishable_row(
            doc,
            prepared_item,
            payload,
            append_status="published" if published else "ready_to_publish",
        )
        if updated:
            await invalidate_site_blog_cache(
                item_id=int(prepared_item.get("id") or 0) or None,
                reason="raw_feed_published" if published else "raw_feed_publish_retry",
            )
        return updated
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("raw_feed publication sync failed for checksum=%s: %s", checksum, exc)
        return False


__all__ = [
    "build_ingest_row",
    "compose_final_post_text",
    "map_sheet_status",
    "sync_final_text",
    "sync_ingest_item",
    "sync_ingest_items_batch",
    "sync_media_fields",
    "sync_nlp_state",
    "sync_owner_comment",
    "sync_publication_queue",
    "sync_publication_result",
]

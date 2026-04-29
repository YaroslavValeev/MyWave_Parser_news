"""Client for uploading local media files to the website."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from config.settings import config


@dataclass(slots=True)
class MediaUploadConfig:
    """Website media upload endpoint settings."""

    url: str | None = None
    token: str | None = None
    timeout_seconds: float = 30.0


class SiteMediaConfigError(RuntimeError):
    """Raised when website media upload settings are incomplete."""


class SiteMediaUploadError(RuntimeError):
    """Raised when website media upload cannot be completed."""


@dataclass(slots=True)
class SiteMediaUploadResult:
    """Normalized media upload response."""

    url: str
    raw: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        if key == "url":
            return self.url
        if key == "raw":
            return self.raw
        raise KeyError(key)


class SiteMediaClient:
    """Small token-authenticated client for the website media upload endpoint."""

    def __init__(
        self,
        upload_config: MediaUploadConfig | None = None,
        *,
        upload_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float = 30.0,
        http_client: Any = requests,
    ) -> None:
        upload_config = upload_config or MediaUploadConfig()
        self._upload_url = upload_url or upload_config.url or config.MEDIA_UPLOAD_URL
        self._token = token or upload_config.token or config.MEDIA_UPLOAD_TOKEN
        self._timeout_seconds = upload_config.timeout_seconds or timeout_seconds
        self._http_client = http_client

    def upload_file(
        self,
        file_path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> SiteMediaUploadResult:
        """Upload one local media file and return its public URL."""

        if not self._upload_url:
            raise SiteMediaConfigError("MEDIA_UPLOAD_URL is not configured")
        if not self._token:
            raise SiteMediaConfigError("MEDIA_UPLOAD_TOKEN is not configured")

        path = Path(file_path)
        if not path.is_file():
            raise SiteMediaUploadError(f"Media file does not exist: {path}")

        headers = {"Authorization": f"Bearer {self._token}"}
        with path.open("rb") as handle:
            response = self._http_client.post(
                self._upload_url,
                headers=headers,
                files={"file": (path.name, handle)},
                data=metadata or {},
                timeout=self._timeout_seconds,
            )

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SiteMediaUploadError(f"Website media upload failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SiteMediaUploadError("Website media upload returned invalid JSON") from exc

        url = payload.get("url") or payload.get("media_url") or payload.get("public_url")
        if not isinstance(url, str) or not url:
            raise SiteMediaUploadError("Website media upload response does not contain media URL")

        return SiteMediaUploadResult(url=url, raw=payload)


__all__ = ["SiteMediaClient", "SiteMediaUploadError", "SiteMediaUploadResult"]

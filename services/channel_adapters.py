"""Multi-channel publish adapters (Content Engine Stage 8) — interface only.

Бизнес-логика editorial/media живёт вне адаптеров.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True, frozen=True)
class PublishResult:
    channel: str
    ok: bool
    external_id: str = ""
    error: str = ""


class ChannelAdapter(ABC):
    name: str

    @abstractmethod
    async def publish(self, payload: Mapping[str, Any]) -> PublishResult:
        raise NotImplementedError


class TelegramChannelAdapter(ChannelAdapter):
    """Обёртка-маркер: фактическая публикация остаётся в PublicationService."""

    name = "telegram"

    async def publish(self, payload: Mapping[str, Any]) -> PublishResult:
        return PublishResult(
            channel=self.name,
            ok=False,
            error="use_PublicationService",
        )


class BlogChannelAdapter(ChannelAdapter):
    name = "blog"

    async def publish(self, payload: Mapping[str, Any]) -> PublishResult:
        return PublishResult(
            channel=self.name,
            ok=False,
            error="site_owned_publish_path",
        )


def list_mvp_adapters() -> list[ChannelAdapter]:
    return [TelegramChannelAdapter(), BlogChannelAdapter()]


__all__ = [
    "BlogChannelAdapter",
    "ChannelAdapter",
    "PublishResult",
    "TelegramChannelAdapter",
    "list_mvp_adapters",
]

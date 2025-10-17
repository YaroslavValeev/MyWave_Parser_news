"""Дополнительные фильтры для aiogram."""
from __future__ import annotations

from typing import Any, Iterable

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from storage.repository import AsyncNewsRepository


class RoleFilter(BaseFilter):
    """Разрешить доступ только пользователям с указанными ролями."""

    def __init__(self, roles: Iterable[str]):
        self._roles = {role.lower() for role in roles}

    async def __call__(
        self,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> bool:
        repo: AsyncNewsRepository = data["repository"]
        user = data.get("current_user")
        event_user = data.get("event_from_user")
        if user is None and event_user is not None:
            user = await repo.get_user(event_user.id)
            data["current_user"] = user
        if not user:
            return False
        role = str(user.get("role") or "").lower()
        return role in self._roles


__all__ = ["RoleFilter"]

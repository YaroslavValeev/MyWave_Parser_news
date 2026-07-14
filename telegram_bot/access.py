"""Права оператора бота (Owner / editors). Без PII в логах."""
from __future__ import annotations

from config.settings import config


def is_bot_operator(user_id: int | None) -> bool:
    """True если user_id в OWNER_USER_ID; пустой OWNER_USER_ID = разрешить всем (dev)."""
    if user_id is None:
        return False
    raw = str(getattr(config, "OWNER_USER_ID", None) or "").strip()
    if not raw:
        return True
    allowed = {int(part.strip()) for part in raw.split(",") if part.strip().isdigit()}
    return int(user_id) in allowed


__all__ = ["is_bot_operator"]

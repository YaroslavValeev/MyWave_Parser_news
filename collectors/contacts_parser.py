import re
import hashlib
from datetime import datetime
from telethon.errors import FloodWaitError, ChannelPrivateError
import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _contacts_message_limit() -> int:
    try:
        return max(0, int(os.getenv("COLLECT_CONTACTS_MESSAGE_LIMIT", "20")))
    except ValueError:
        return 20


def _contacts_delay_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("COLLECT_CONTACTS_DELAY_SECONDS", "0.05")))
    except ValueError:
        return 0.05


class ContactsParser:
    """Парсинг контактов (email, телефон, username) из текста постов канала."""

    def __init__(self, telethon_client):
        self.client = telethon_client
        self.email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        self.phone_pattern = re.compile(
            r"[+]?[(]?[0-9]{1,4}[)]?[-\s.]?[0-9]{3,}[\s.-]?[0-9]{2,}"
        )
        self.username_pattern = re.compile(r"@[0-9A-Za-z_]+")

    async def parse_contacts(self, source):
        """Вернуть список контактов; без искусственной паузы 2с на каждое сообщение."""
        contacts = []
        limit = _contacts_message_limit()
        if limit <= 0:
            return contacts
        delay = _contacts_delay_seconds()
        try:
            entity = await self.client.get_entity(source.url)
            async for msg in self.client.iter_messages(entity, limit=limit):
                if msg.text:
                    contacts.extend(self._parse_text(msg.text, source.url))
                if delay > 0:
                    await asyncio.sleep(delay)
        except (ChannelPrivateError, FloodWaitError) as e:
            logger.warning("Ошибка доступа или лимит контактов: %s", e)
            await asyncio.sleep(getattr(e, "seconds", 5))
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка парсинга контактов: %s", e)
        return contacts

    def filter_new_contacts(self, contacts, existing_contact_ids):
        return [c for c in contacts if c["contact_id"] not in existing_contact_ids]

    def _parse_text(self, text, source_url):
        found = []
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for email in self.email_pattern.findall(text):
            if self._is_valid_email(email):
                contact_id = self._make_id("email", email, source_url)
                found.append(
                    {
                        "type": "email",
                        "value": email,
                        "source": source_url,
                        "date_found": now,
                        "contact_id": contact_id,
                    }
                )
        for phone in self.phone_pattern.findall(text):
            norm_phone = self._normalize_phone(phone)
            if norm_phone:
                contact_id = self._make_id("phone", norm_phone, source_url)
                found.append(
                    {
                        "type": "phone",
                        "value": norm_phone,
                        "source": source_url,
                        "date_found": now,
                        "contact_id": contact_id,
                    }
                )
        for user in self.username_pattern.findall(text):
            user = user.lower()
            if "." not in user:
                contact_id = self._make_id("username", user, source_url)
                found.append(
                    {
                        "type": "username",
                        "value": user,
                        "source": source_url,
                        "date_found": now,
                        "contact_id": contact_id,
                    }
                )
        return found

    def _normalize_phone(self, phone):
        digits = re.sub(r"[^0-9+]", "", phone)
        if len(digits) < 7:
            return None
        return digits

    def _is_valid_email(self, email):
        return "@" in email and "." in email.split("@")[-1]

    def _make_id(self, type_, value, source):
        s = f"{type_}:{value}:{source}"
        return hashlib.md5(s.encode("utf-8")).hexdigest()

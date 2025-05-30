import re
import hashlib
from datetime import datetime
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import PeerChannel
from telethon.errors import FloodWaitError, ChannelPrivateError
import asyncio
import logging

logger = logging.getLogger(__name__)

class ContactsParser:
    """Класс для парсинга контактов (email, телефон, username) из Telegram-канала и комментариев."""
    def __init__(self, telethon_client):
        self.client = telethon_client
        self.email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        self.phone_pattern = re.compile(r'[+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{3,}[\s\.-]?[0-9]{2,}')
        self.username_pattern = re.compile(r'@[0-9A-Za-z_]+')

    async def parse_contacts(self, source):
        """Парсинг контактов из канала. Возвращает список словарей с type, value, source, date_found, contact_id."""
        contacts = []
        try:
            entity = await self.client.get_entity(source.url)
            async for msg in self.client.iter_messages(entity, limit=100):
                if msg.text:
                    contacts.extend(self._parse_text(msg.text, source.url))
                await asyncio.sleep(2)
        except (ChannelPrivateError, FloodWaitError) as e:
            logger.warning(f"Ошибка доступа или лимит: {e}")
            await asyncio.sleep(getattr(e, 'seconds', 5))
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
        return contacts

    def filter_new_contacts(self, contacts, existing_contact_ids):
        """Фильтрует контакты по contact_id (md5(type+value+source))."""
        return [c for c in contacts if c['contact_id'] not in existing_contact_ids]

    def _parse_text(self, text, source_url):
        """Извлекает контакты из текста, возвращает список словарей."""
        found = []
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        # Emails
        for email in self.email_pattern.findall(text):
            if self._is_valid_email(email):
                contact_id = self._make_id('email', email, source_url)
                found.append({
                    'type': 'email', 'value': email, 'source': source_url, 'date_found': now, 'contact_id': contact_id
                })
        # Phones
        for phone in self.phone_pattern.findall(text):
            norm_phone = self._normalize_phone(phone)
            if norm_phone:
                contact_id = self._make_id('phone', norm_phone, source_url)
                found.append({
                    'type': 'phone', 'value': norm_phone, 'source': source_url, 'date_found': now, 'contact_id': contact_id
                })
        # Usernames
        for user in self.username_pattern.findall(text):
            user = user.lower()
            if '.' not in user:
                contact_id = self._make_id('username', user, source_url)
                found.append({
                    'type': 'username', 'value': user, 'source': source_url, 'date_found': now, 'contact_id': contact_id
                })
        return found

    def _normalize_phone(self, phone):
        # Удаляем пробелы, скобки, дефисы, точки
        digits = re.sub(r'[^0-9+]', '', phone)
        if len(digits) < 7:
            return None
        return digits

    def _is_valid_email(self, email):
        # Простая валидация (наличие @ и . после @)
        if '@' in email and '.' in email.split('@')[-1]:
            return True
        return False

    def _make_id(self, type_, value, source):
        s = f"{type_}:{value}:{source}"
        return hashlib.md5(s.encode('utf-8')).hexdigest()

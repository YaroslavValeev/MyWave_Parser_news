import re
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
        self.phone_pattern = re.compile(r'[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}')
        self.username_pattern = re.compile(r'(?<=\s|^)@[0-9A-Za-z_]+')
    
    async def parse_contacts(self, source):
        """Парсинг контактов из приватного канала."""
        contacts = {'emails': set(), 'phones': set(), 'usernames': set()}
        try:
            entity = await self.client.get_entity(source.url)
            async for msg in self.client.iter_messages(entity, limit=100):
                if msg.text:
                    self._parse_text(msg.text, contacts)
                await asyncio.sleep(2)  # Задержка 2 секунды между сообщениями
        except ChannelPrivateError:
            logger.error(f"Нет доступа к каналу {source.url}. Пробуем альтернативный метод.")
            try:
                # Альтернативный метод для приватного канала: итерация по сообщениям с задержкой
                async for msg in self.client.iter_messages(entity, limit=100):
                    if msg.text:
                        self._parse_text(msg.text, contacts)
                    await asyncio.sleep(2)
            except FloodWaitError as e:
                logger.warning(f"Ожидание {e.seconds} секунд...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Ошибка парсинга: {e}")
        except FloodWaitError as e:
            logger.warning(f"Ожидание {e.seconds} секунд...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
        return contacts

    def filter_new_contacts(self, contacts, existing_contacts):
        """Фильтрует контакты, удаляя те, которые уже присутствуют (например, полученные из Google Sheets)."""
        return {
            'emails': contacts['emails'] - existing_contacts.get('emails', set()),
            'phones': contacts['phones'] - existing_contacts.get('phones', set()),
            'usernames': contacts['usernames'] - existing_contacts.get('usernames', set())
        }
    
    def _parse_text(self, text, contacts):
        """Извлекает email, телефон и username из переданного текста."""
        if not text:
            return
        # Emails
        contacts['emails'].update(self.email_pattern.findall(text))
        # Phones
        contacts['phones'].update(self.phone_pattern.findall(text))
        # Usernames (приводим к нижнему регистру и фильтруем по отсутствию '.')
        for user in self.username_pattern.findall(text):
            user = user.lower()
            if '.' not in user:
                contacts['usernames'].add(user)

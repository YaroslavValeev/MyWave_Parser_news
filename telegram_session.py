import logging
from telethon.sessions import StringSession
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from config.settings import config

logger = logging.getLogger(__name__)

class TelegramSessionManager:
    def __init__(self, api_id: int, api_hash: str, phone: str):
        """
        Инициализация менеджера сессий.
        
        Args:
            api_id: Ваш API ID из my.telegram.org
            api_hash: Ваш API Hash
            phone: Номер телефона в формате '+79991112233'
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.client = None
        self.session_string = None  # Будет хранить сессию для повторного использования

    async def get_client(self) -> TelegramClient:
        """
        Возвращает готового к работе клиента Telegram.
        При первом запуске запрашивает код авторизации.
        """
        try:
            self.client = TelegramClient(
                StringSession(self.session_string),
                self.api_id,
                self.api_hash,
                device_model="MyWaveParser",
                system_version="1.0",
                app_version="1.0"
            )
            
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                logger.info("Начало процесса авторизации...")
                await self._authorize()
                
            return self.client
            
        except Exception as e:
            logger.error(f"Ошибка инициализации клиента: {e}")
            raise

    async def _authorize(self):
        """Процесс авторизации с обработкой 2FA"""
        try:
            await self.client.send_code_request(self.phone)
            code = input("Введите код из Telegram: ")
            
            try:
                await self.client.sign_in(self.phone, code)
            except SessionPasswordNeededError:
                password = input("Включена 2FA. Введите пароль: ")
                await self.client.sign_in(password=password)
            
            # Сохраняем сессию для будущих запусков
            self.session_string = self.client.session.save()
            logger.info("Авторизация успешно завершена")
            
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            await self.close()
            raise

    async def close(self):
        """Корректное отключение клиента"""
        if self.client and await self.client.is_connected():
            await self.client.disconnect()
            logger.info("Telegram клиент отключен")

    def get_session_string(self) -> str:
        """Возвращает строку сессии для сохранения"""
        return self.session_string or (self.client.session.save() if self.client else None)
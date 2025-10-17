import asyncio
import os
import logging
from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, SessionPasswordNeededError
from telethon.sessions import StringSession
import os

logger = logging.getLogger(__name__)

class TelegramSessionManager:
    def __init__(self, api_id, api_hash, phone):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        # session_file is a fallback file-based session
        self.session_file = os.getenv('TELETHON_SESSION_FILE', 'session_name.session')
        # allow using a pre-generated string session to avoid interactive auth
        # TELETHON_STRING_SESSION can contain the session string directly
        self.string_session = os.getenv('TELETHON_STRING_SESSION')
        # Alternatively, read the session string from a dedicated file
        self.string_session_file = os.getenv('TELETHON_STRING_SESSION_FILE', 'session_string.txt')
        if not self.string_session and os.path.exists(self.string_session_file):
            try:
                with open(self.string_session_file, 'r', encoding='utf-8') as fh:
                    self.string_session = fh.read().strip()
            except Exception:
                # non-fatal — we'll fall back to file-based session or interactive login
                self.string_session = None
        self.client = None

    async def get_client(self):
        if self.client is None:
            self.client = await self._create_client()
        return self.client

    async def _create_client(self):
        """Enhanced Telethon client initialization with session management."""

        # If a string session is provided via env, use it (no interactive code needed)
        if self.string_session:
            client = TelegramClient(StringSession(self.string_session), self.api_id, self.api_hash)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    # String session appears invalid
                    await client.disconnect()
                else:
                    return client
            except Exception:
                # Fall through and try file-based session as a fallback
                try:
                    await client.disconnect()
                except Exception:
                    pass

        # Remove or backup an invalid session file with multiple attempts.
        if os.path.exists(self.session_file):
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    test_client = TelegramClient(
                        self.session_file,
                        self.api_id,
                        self.api_hash
                    )
                    await test_client.connect()
                    if not await test_client.is_user_authorized():
                        logger.warning("Невалидная сессия. Бэкапирую и удаляю...")
                        await test_client.disconnect()  # Явное отключение перед удалением
                        try:
                            # backup instead of outright deleting to avoid accidental data loss
                            os.replace(self.session_file, self.session_file + '.bak')
                            logger.info(f"Файл сессии переименован в {self.session_file}.bak")
                            break
                        except PermissionError as pe:
                            if attempt == max_retries - 1:
                                logger.error(f"Failed to backup session file after {max_retries} attempts: {pe}")
                                return None
                            await asyncio.sleep(1)  # Wait before retrying.
                            continue
                    else:
                        await test_client.disconnect()
                        break
                except Exception as e:
                    logger.error(f"Ошибка при проверке сессии: {e}")
                    if os.path.exists(self.session_file):
                        try:
                            os.remove(self.session_file)
                        except PermissionError as pe:
                            logger.warning(f"Не удалось удалить файл сессии: {pe}")
                    break

        # Create a new Telethon client with connection retries enabled.
        client = TelegramClient(
            self.session_file,
            self.api_id,
            self.api_hash,
            connection_retries=3
        )

        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.info("Требуется авторизация...")
                try:
                    await client.start(
                        phone=self.phone,
                        code_callback=lambda: input("Введите код: ")
                    )
                except SessionPasswordNeededError:
                    logger.warning("Требуется 2FA пароль")
                    await client.start(
                        phone=self.phone,
                        password=lambda: input("Введите 2FA пароль: ")
                    )

            logger.info(f"Авторизован как: {await client.get_me()}")
            # After successful authorization, save string session to a file so
            # future runs can avoid interactive login. Also print instruction
            # so the user can set TELETHON_STRING_SESSION if desired.
            try:
                ss = StringSession.save(client.session)
                # Save to configured session string file and inform the user.
                try:
                    # Write atomically
                    tmp = f"{self.string_session_file}.tmp"
                    with open(tmp, 'w', encoding='utf-8') as fh:
                        fh.write(ss)
                    os.replace(tmp, self.string_session_file)
                    logger.info(
                        f"String session saved to {self.string_session_file}.\n"
                        "To avoid future code prompts, set TELETHON_STRING_SESSION env var to its contents or keep this file secure."
                    )
                except Exception as write_err:
                    logger.warning(f"Failed to persist string session to {self.string_session_file}: {write_err}")
            except Exception:
                # ignore saving errors — session still works in-memory
                pass
            return client

        except AuthKeyUnregisteredError:
            logger.error("Недействительная сессия")
            if os.path.exists(self.session_file):
                try:
                    os.remove(self.session_file)
                except PermissionError as pe:
                    logger.warning("Не удалось удалить файл сессии: {pe}")
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
        return None

    async def close_client(self):
        if self.client:
            await self.client.disconnect()
            self.client = None

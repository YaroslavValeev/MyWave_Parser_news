# MyWave Parser News

![CI](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/workflows/ci.yml/badge.svg)

MyWave_Parser_News — это Telegram-бот для парсинга новостей из различных источников (Telegram-каналы, RSS-ленты, YouTube, веб-сайты) с последующим анализом и публикацией в Telegram-канале.

## Архитектура

```mermaid
graph TD
    A[Источники (RSS, Telegram, YouTube)] --> B[collectors]
    B --> C[processors]
    C --> D[services: Google Sheets, Telegram]
    D --> E[Make.com, AI]
```

## Быстрый старт

1. Клонируйте репозиторий:

   ```
   git clone ...
   cd mywave_parser_news
   ```

2. Создайте и заполните файл `.env`:

   ```env
   TELEGRAM_API_ID=...
   TELEGRAM_API_HASH=...
   TELEGRAM_BOT_TOKEN=...
   GOOGLE_CREDENTIALS_PATH=path/to/creds.json
   GOOGLE_SHEET_ID=...
   ```

3. Установите зависимости:

   ```
   pip install -r requirements.txt
   ```

4. Запустите бота:

   ```
   python bot.py run
   ```

## Основные команды

* **Линтинг:** `ruff core/`
* **Тесты:** `pytest`
* **Запуск:** `python bot.py run`

## Переменные окружения (.env)

| Имя                       | Описание                  |
| ------------------------- | ------------------------- |
| TELEGRAM\_API\_ID         | API ID Telegram           |
| TELEGRAM\_API\_HASH       | API Hash Telegram         |
| TELEGRAM\_BOT\_TOKEN      | Токен Telegram-бота       |
| GOOGLE\_CREDENTIALS\_PATH | Путь до JSON-файла Google |
| GOOGLE\_SHEET\_ID         | ID Google-таблицы         |

## CI/CD

![CI](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/workflows/ci.yml/badge.svg)

## Лицензия

MIT

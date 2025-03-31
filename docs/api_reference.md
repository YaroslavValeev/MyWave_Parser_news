# 📌 MyWave_Parser API Reference

## 🔹 Базовая информация
MyWave_Parser — это Telegram-бот, который парсит новости из различных источников (RSS, YouTube, Telegram, веб-сайты) и публикует их в Telegram-канале.

## 🛠 Поддерживаемые команды
| Команда                                  | Описание                                                         |
|------------------------------------------|------------------------------------------------------------------|
| `/start`                                 | Начало работы с ботом                                            |
| `/parse`                                 | Ручной запуск парсинга                                           |
| `/publish`                               | Публикация новостей в Telegram                                   |
| `/addsource <type> <url>`                  | Добавление нового источника (telegram, rss, youtube, website)      |
| `/setfilters <keywords>`                 | Установка фильтров ключевых слов                                 |
| `/getcontacts`                           | Извлечение контактов из Telegram-каналов                         |
| `/report`                                | Вывод отчёта о парсинге                                            |

---

## 🔹 Структура новости
Пример JSON-ответа с новостью:
```json
{
  "source": "Telegram: WakeSurf Channel",
  "title": "Новая техника выполнения трюков!",
  "content": "Сегодня мы разберём...",
  "link": "https://t.me/wakesurf/123",
  "date": "2025-03-05",
  "images": ["https://example.com/image.jpg"],
  "videos": ["https://example.com/video.mp4"],
  "transcript": "Текстовое описание видео",
  "comment": "Интересная новость"
}

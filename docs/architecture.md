# Архитектура проекта: MyWave Parser

## 📌 Общая информация
**MyWave Parser** – это бот для Telegram, который собирает новости из различных источников (Telegram, YouTube, RSS, веб-сайты), анализирует их и публикует в Telegram-канале. 

## 🏗️ Основные компоненты
### 1. **Сбор данных (collectors)**
Модули:
- `telegram_collector.py` – парсинг Telegram-каналов.
- `rss_collector.py` – парсинг RSS-лент.
- `youtube_collector.py` – парсинг видео и транскриптов с YouTube.
- `website_collector.py` – парсинг статей с веб-сайтов.

### 2. **Обработка данных (processors)**
Модули:
- `data_processor.py` – фильтрация и нормализация данных.
- `nlp_processor.py` – анализ текста с использованием OpenAI.

### 3. **Сохранение данных (storage)**
Модули:
- `data.py` – база данных SQLite.
- `sources.py` – управление списком источников.

### 4. **Публикация (publishers)**
Модули:
- `telegram_publisher.py` – отправка новостей в Telegram.
- `report_generator.py` – генерация отчетов.

### 5. **Аналитика (analytics)**
Модули:
- `metrics_calculator.py` – вычисление метрик парсинга.

### 6. **Инфраструктура**
- `config/settings.py` – управление конфигурацией.
- `utils/logger.py` – логирование событий.

## ⚡ Запуск проекта
1. Установите зависимости:  
   ```sh
   pip install -r requirements.txt

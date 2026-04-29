# 1. Используем официальный образ Python 3.12 (облегчённая версия slim)
FROM python:3.12-slim

# 2. Устанавливаем переменные среды (например, не создаём .pyc файлы)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. Указываем рабочую директорию внутри контейнера
WORKDIR /app

# 4. Копируем файл зависимостей (чтобы кэширование работало правильно)
COPY requirements.txt .

# 5. Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# 6. Копируем остальные файлы проекта в контейнер (исключая ненужные из .dockerignore)
COPY . .

# 7. Указываем, что файл .env может содержать переменные окружения
ENV ENV_FILE_PATH=/app/.env

# 8. Запуск бота при старте контейнера
CMD ["python", "bot.py"]

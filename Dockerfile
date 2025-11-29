# Dockerfile
FROM python:3.11-slim

# Установка локали (чтобы не было ошибок с кодировкой)
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

# Рабочая папка в контейнере
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем ВЕСЬ код
COPY . .

# Установка uvloop (ускорение)
RUN pip install uvloop --no-cache-dir

# Запуск бота
CMD ["python", "main.py"]

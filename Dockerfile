FROM python:3.11-slim

# Установка локали
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Рабочая директория
WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем uvloop (ускорение асинхронности)
RUN pip install --no-cache-dir uvloop

# Копируем код
COPY . .

# Запуск бота
CMD ["python", "main.py"]

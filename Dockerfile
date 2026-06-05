FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget gnupg curl libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

ENV PYTHONPATH=/app

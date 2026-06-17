# rozetka-scrapper

FastAPI-микросервис для парсинга цен товаров с Rozetka. Принимает список URL, возвращает цены пакетом.

[English](./README.md)

## API

**`POST /update`**

```json
{
  "urls": [
    "https://rozetka.com.ua/...",
    "https://rozetka.com.ua/..."
  ]
}
```

Ответ:

```json
{
  "data": {
    "https://rozetka.com.ua/...": "1299",
    "https://rozetka.com.ua/...": "4599"
  }
}
```

## Как работает

- Запускает headless Chromium через Playwright
- Использует `playwright-stealth` для обхода антибот-защиты
- Достаёт цены из JSON-LD разметки, fallback — CSS-селекторы
- До 5 страниц одновременно, 3 попытки на каждый URL
- In-memory кэш с TTL 1 час

## Запуск через Docker

```bash
docker compose up -d
```

Сервис стартует на `http://localhost:9001`

## Локальный запуск

```bash
pip install -r requirements.txt
playwright install chromium
python server.py
```

## Технологии

- Python 3
- FastAPI + Uvicorn
- Playwright + playwright-stealth
- Docker

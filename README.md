# rozetka-scrapper

A FastAPI microservice that scrapes product prices from Rozetka. Accepts a list of URLs, returns prices in bulk.

[Русский](./README.ru.md)

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

Response:

```json
{
  "data": {
    "https://rozetka.com.ua/...": "1299",
    "https://rozetka.com.ua/...": "4599"
  }
}
```

## How it works

- Launches a headless Chromium browser via Playwright
- Uses `playwright-stealth` to bypass bot detection
- Extracts prices from JSON-LD structured data, falls back to CSS selectors
- Up to 5 concurrent pages, 3 retries per URL
- In-memory cache with 1-hour TTL

## Run with Docker

```bash
docker compose up -d
```

Service starts on `http://localhost:9001`

## Run locally

```bash
pip install -r requirements.txt
playwright install chromium
python server.py
```

## Tech

- Python 3
- FastAPI + Uvicorn
- Playwright + playwright-stealth
- Docker

# server.py
from fastapi import FastAPI, Request
import uvicorn
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import json
import time
import random
from urllib.parse import urlparse

app = FastAPI()

cache: dict[str, dict] = {}
CACHE_TTL = 3600  # 1 час
MAX_CONCURRENCY = 5  # сколько страниц одновременно


def is_valid_http_url(s: str) -> bool:
    """Проверка, что строка выглядит как нормальный http/https URL."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


async def scrape_price_single(page, url: str) -> str | None:
    """Скрапит цену для одного товара в уже созданной вкладке."""
    # кэш
    if url in cache and time.time() - cache[url]["time"] < CACHE_TTL:
        return cache[url]["price"]

    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2, 5))

        # 1) JSON-LD Product
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for script in scripts:
            text = await script.inner_text()
            try:
                data = json.loads(text)

                # Rozetka может отдавать список объектов
                if isinstance(data, list):
                    for item in data:
                        price = extract_price_from_ld(item)
                        if price:
                            cache[url] = {"price": price, "time": time.time()}
                            return price
                else:
                    price = extract_price_from_ld(data)
                    if price:
                        cache[url] = {"price": price, "time": time.time()}
                        return price
            except Exception:
                # иногда JSON битый — просто пропускаем
                continue

        # 2) Fallback HTML — по селекторам цены
        price_locator = page.locator(
            '.product-price__big, [itemprop="price"], .product-prices__big'
        )
        if await price_locator.count() > 0:
            price_elem = await price_locator.first.inner_text()
            price = (
                price_elem.strip()
                .replace("₴", "")
                .replace(" ", "")
                .replace("\xa0", "")
            )
            if price:
                cache[url] = {"price": price, "time": time.time()}
                return price

        return None

    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None


def extract_price_from_ld(item) -> str | None:
    """Достать цену из JSON-LD объекта Product."""
    if not isinstance(item, dict):
        return None
    if item.get("@type") != "Product":
        return None
    offers = item.get("offers")
    if not offers:
        return None
    price = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
    if not price:
        return None
    return str(price).replace(" ", "")


async def scrape_batch(urls: list[str]) -> dict[str, str]:
    """Параллельно скрапит батч URL-ов с ограничением по конкарренси."""
    results: dict[str, str] = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        await context.set_extra_http_headers(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }
        )

        async def worker(u: str):
            async with semaphore:
                page = await context.new_page()
                try:
                    for attempt in range(3):
                        price = await scrape_price_single(page, u)
                        if price:
                            results[u] = price
                            break
                        await asyncio.sleep(5 + random.uniform(0, 3))
                finally:
                    await page.close()

        tasks = [asyncio.create_task(worker(u)) for u in urls]
        await asyncio.gather(*tasks)

        await browser.close()

    return results


@app.post("/update")
async def update(request: Request):
    body = await request.json()
    raw_urls = body.get("urls", [])

    # фильтруем мусор (типа "ссылка" и пустые строки)
    urls = [u for u in raw_urls if is_valid_http_url(u)]
    if not urls:
        print("No valid URLs received:", raw_urls)
        return {"data": {}}

    print("Scraping URLs:", urls)

    prices = await scrape_batch(urls)
    # формат ответа: { "data": { url: price, ... } }
    return {"data": prices}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001)

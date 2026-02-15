# server.py
from fastapi import FastAPI, Request
import uvicorn
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import json
import time
import random
import requests

app = FastAPI()

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxgEKCOZlUDR93EMbfF2OD3WMp3rqzxjESFwWN05-rLj2T-F3NH1HJlq6YFnBOtxuQ6/exec"

cache = {}
CACHE_TTL = 3600  # 1 час
MAX_CONCURRENCY = 5  # сколько страниц одновременно открываем (баланс скорость/баны)


async def scrape_price_single(page, url: str) -> str | None:
    # кэш
    if url in cache and time.time() - cache[url]["time"] < CACHE_TTL:
        return cache[url]["price"]

    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2, 5))

        # JSON-LD
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for script in scripts:
            text = await script.inner_text()
            try:
                data = json.loads(text)
                # иногда там массив из Product
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Product" and "offers" in item:
                            offers = item["offers"]
                            price = (
                                offers.get("price")
                                or offers.get("lowPrice")
                                or offers.get("highPrice")
                            )
                            if price:
                                cache[url] = {"price": price, "time": time.time()}
                                return str(price)
                else:
                    if data.get("@type") == "Product" and "offers" in data:
                        offers = data["offers"]
                        price = (
                            offers.get("price")
                            or offers.get("lowPrice")
                            or offers.get("highPrice")
                        )
                        if price:
                            cache[url] = {"price": price, "time": time.time()}
                            return str(price)
            except Exception:
                continue

        # Fallback HTML – подстраховка по селекторам
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


async def scrape_batch(urls: list[str]) -> dict[str, str]:
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


async def process_batch(urls: list[str], webhook: str):
    if not urls:
        return

    try:
        prices = await scrape_batch(urls)
        if prices:
            print("SENDING TO WEBHOOK:", webhook, prices)  # <<< добавь

            # отправка обратно в Apps Script
            try:
                requests.post(webhook, json={"data": prices}, timeout=30)
            except Exception as e:
                print(f"Error posting to webhook: {e}")
    except Exception as e:
        print(f"Error in process_batch: {e}")


@app.post("/update")
async def update(request: Request):
    body = await request.json()
    urls = body.get("urls", [])
    webhook = body.get("webhook", WEBHOOK_URL)

    # ВАЖНО: НЕ ждём окончания, запускаем в фоне
    asyncio.create_task(process_batch(urls, webhook))

    return {"status": "processing"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001)

from fastapi import FastAPI, Request
import uvicorn
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import json
import time
import random
import requests

app = FastAPI()

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxY_dIzWRVXaFPCW-6WfZB1Uhdh2Z5b-e-4lsWzYVz4psv-7RNJ6RNcgxnn8SFY9nxc/exec"  # Replace with your Apps Script webhook URL

cache = {}
CACHE_TTL = 3600  # 1 hour


async def scrape_price(url):
    if url in cache and time.time() - cache[url]['time'] < CACHE_TTL:
        return cache[url]['price']

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await stealth_async(page)
        await page.context.set_extra_http_headers({
                                                      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"})

        try:
            await page.goto(url, timeout=60000)
            await asyncio.sleep(random.uniform(2, 5))  # Random delay for anti-bot

            # Try JSON-LD
            scripts = await page.query_selector_all('script[type="application/ld+json"]')
            for script in scripts:
                text = await script.inner_text()
                try:
                    data = json.loads(text)
                    if '@type' in data and data['@type'] == 'Product' and 'offers' in data:
                        price = data['offers'].get('price') or data['offers'].get('lowPrice')
                        if price:
                            cache[url] = {'price': price, 'time': time.time()}
                            return price
                except:
                    pass

            # Fallback HTML (adjust class if changes)
            price_elem = await page.locator('.product-price__big, [itemprop="price"]').inner_text()
            price = price_elem.strip().replace('₴', '').replace(' ', '').replace('\xa0', '')
            cache[url] = {'price': price, 'time': time.time()}
            return price

        except Exception as e:
            print(f"Error scraping {url}: {e}")
            return None

        finally:
            await browser.close()


async def process_batch(urls, webhook):
    prices = {}
    for url in urls:
        for retry in range(3):
            price = await scrape_price(url)
            if price:
                prices[url] = price
                break
            await asyncio.sleep(5 + random.uniform(0, 3))  # Retry delay
    if prices:
        requests.post(webhook, json={"data": prices})


@app.post("/update")
async def update(request: Request):
    body = await request.json()
    urls = body.get('urls', [])
    webhook = body.get('webhook', WEBHOOK_URL)
    asyncio.create_task(process_batch(urls, webhook))
    return {"status": "processing"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
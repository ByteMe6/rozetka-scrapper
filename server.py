from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import stealth_async
import re
from typing import List, Optional, Tuple
import json
import asyncio
from datetime import datetime
import random

app = FastAPI()

# ========================
# CONFIG
# ========================

POOL_SIZE = 5
CACHE_TTL = 60
MIN_DELAY = 3.5
MAX_PER_MINUTE = 10

# ========================
# GLOBALS
# ========================

playwright_instance: Optional[Playwright] = None
browser: Optional[Browser] = None
browser_pool: List[Tuple[BrowserContext, Page]] = []
pool_index = 0

request_times = []
cache = {}

# ========================
# MODELS
# ========================

class LinksRequest(BaseModel):
    urls: List[str]

# ========================
# RATE LIMIT
# ========================

async def smart_delay():
    global request_times
    now = datetime.now()

    request_times = [t for t in request_times if (now - t).total_seconds() < 60]

    if request_times:
        delta = (now - max(request_times)).total_seconds()
        if delta < MIN_DELAY:
            await asyncio.sleep(MIN_DELAY - delta + random.uniform(0.5, 1.2))

    if len(request_times) >= MAX_PER_MINUTE:
        await asyncio.sleep(10)

    request_times.append(datetime.now())

# ========================
# BROWSER POOL
# ========================

async def create_context_and_page(index: int):
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    ]

    vp_list = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
    ]

    context = await browser.new_context(
        user_agent=ua_list[index % len(ua_list)],
        viewport=vp_list[index % len(vp_list)],
        locale="uk-UA",
        timezone_id="Europe/Kiev"
    )

    page = await context.new_page()
    await stealth_async(page)

    return (context, page)

async def get_browser_pool():
    global playwright_instance, browser, browser_pool

    if not browser_pool:
        playwright_instance = await async_playwright().start()
        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )

        for i in range(POOL_SIZE):
            ctx_page = await create_context_and_page(i)
            browser_pool.append(ctx_page)

    return browser_pool

def get_next_page():
    global pool_index
    ctx, page = browser_pool[pool_index]
    pool_index = (pool_index + 1) % len(browser_pool)
    return page

# ========================
# FETCH
# ========================

async def fetch_html(url: str) -> Tuple[str, int]:

    if not url.endswith('/'):
        url += '/'

    await smart_delay()

    page = get_next_page()

    for attempt in range(2):

        try:
            response = await page.goto(
                url,
                wait_until="networkidle",
                timeout=20000
            )

            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except:
                pass

            html = await page.content()

            if len(html) < 100000:
                await asyncio.sleep(5)
                continue

            return html, response.status if response else 200

        except:
            await asyncio.sleep(3)

    return "", 502

# ========================
# PARSER
# ========================

def extract_price(html: str) -> Optional[float]:
    try:
        match = re.search(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL
        )
        if match:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "offers" in data:
                return float(data["offers"]["price"])
    except:
        pass
    return None

# ========================
# ENDPOINTS
# ========================

@app.get("/price")
async def get_price(url: str):

    if not url.startswith("http") or "rozetka.com.ua" not in url:
        raise HTTPException(400, "invalid url")

    # CACHE
    if url in cache:
        value, ts = cache[url]
        if (datetime.now() - ts).total_seconds() < CACHE_TTL:
            return JSONResponse({"price": value})

    html, status = await fetch_html(url)

    if status != 200 or not html:
        raise HTTPException(502, "fetch error")

    price = extract_price(html)

    if not price:
        raise HTTPException(404, "not found")

    cache[url] = (price, datetime.now())

    return JSONResponse({"price": price})

@app.post("/prices")
async def get_prices(request: LinksRequest):
    results = []
    for url in request.urls:
        try:
            r = await get_price(url)
            results.append(json.loads(r.body)["price"])
        except:
            results.append("error")
    return JSONResponse({"prices": results})

@app.on_event("startup")
async def startup():
    await get_browser_pool()

@app.on_event("shutdown")
async def shutdown():
    for ctx, page in browser_pool:
        await page.close()
        await ctx.close()
    if browser:
        await browser.close()
    if playwright_instance:
        await playwright_instance.stop()
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
from playwright_stealth import Stealth
import re
from typing import List, Optional, Tuple
import json
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
import random

app = FastAPI()

# Global browser pool
playwright_instance: Optional[Playwright] = None
browser: Optional[Browser] = None
browser_pool: List[BrowserContext] = []
POOL_SIZE = 5
pool_index = 0

# Rate limiting
request_times = []
MIN_DELAY_BETWEEN_REQUESTS = 2.5
MAX_REQUESTS_PER_MINUTE = 12


# --- Models ---
class LinksRequest(BaseModel):
    urls: List[str]


async def smart_delay():
    """Smart rate limiting"""
    global request_times
    now = datetime.now()

    request_times = [t for t in request_times if (now - t).total_seconds() < 60]

    if request_times:
        last_request = max(request_times)
        time_since_last = (now - last_request).total_seconds()

        if time_since_last < MIN_DELAY_BETWEEN_REQUESTS:
            wait_time = MIN_DELAY_BETWEEN_REQUESTS - time_since_last + random.uniform(0.3, 0.7)
            await asyncio.sleep(wait_time)

    if len(request_times) >= MAX_REQUESTS_PER_MINUTE:
        oldest = min(request_times)
        wait_time = 60 - (now - oldest).total_seconds() + random.uniform(2, 4)
        print(f"⏸️  Rate limit: waiting {wait_time:.1f}s...")
        await asyncio.sleep(wait_time)
        request_times.clear()

    request_times.append(datetime.now())


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720}
]


async def create_browser_context(index: int) -> BrowserContext:
    """Create stealth browser context"""
    global browser

    ua = USER_AGENTS[index % len(USER_AGENTS)]
    vp = VIEWPORTS[index % len(VIEWPORTS)]

    context = await browser.new_context(
        user_agent=ua,
        locale="uk-UA",
        viewport=vp,
        screen=vp,
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        timezone_id="Europe/Kiev",
        geolocation={
            "latitude": 50.4501 + random.uniform(-0.05, 0.05),
            "longitude": 30.5234 + random.uniform(-0.05, 0.05)
        },
        permissions=["geolocation"],
        color_scheme="light",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
    )

    print(f"🌐 Browser #{index + 1}: {vp['width']}x{vp['height']}")

    return context


async def get_browser_pool() -> List[BrowserContext]:
    """Get or create browser pool"""
    global playwright_instance, browser, browser_pool

    if not browser_pool:
        if playwright_instance is None:
            playwright_instance = await async_playwright().start()

        if browser is None:
            browser = await playwright_instance.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--ignore-certificate-errors',
                    '--no-first-run',
                    '--disable-infobars',
                    '--window-size=1920,1080'
                ]
            )

        print(f"🚀 Creating pool of {POOL_SIZE} contexts with STEALTH...")
        for i in range(POOL_SIZE):
            ctx = await create_browser_context(i)
            browser_pool.append(ctx)
        print(f"✅ Pool ready: {len(browser_pool)} contexts!")

    return browser_pool


def get_next_browser() -> BrowserContext:
    """Round-robin"""
    global pool_index
    context = browser_pool[pool_index]
    pool_index = (pool_index + 1) % len(browser_pool)
    return context


async def fetch_rozetka_html(url: str) -> Tuple[str, int]:
    """Fetch with STEALTH + rotation"""

    await smart_delay()

    if not url.endswith('/'):
        url += '/'

    contexts = await get_browser_pool()
    context = get_next_browser()

    page = await context.new_page()

    # 🥷 APPLY STEALTH (this is the magic!)
    stealth = Stealth()
    await stealth.apply_async(page)

    try:
        await page.wait_for_timeout(random.randint(500, 1000))

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            status_code = response.status if response else 200
        except Exception as e:
            try:
                response = await page.goto(url, wait_until="load", timeout=15000)
                status_code = response.status if response else 200
            except:
                return ("", 502)

        await page.wait_for_timeout(random.randint(1000, 1500))

        for attempt in range(2):
            try:
                await page.wait_for_selector(
                    'p.product-price__big, [class*="product-price"]',
                    timeout=3000
                )
                break
            except:
                if attempt < 1:
                    await page.wait_for_timeout(1500)

        html = await page.content()
        html_size = len(html)

        is_product = any(m in html for m in [
            'product-about', 'product-main', 'product-price',
            'productId', '"@type":"Product"', 'data-goods-id'
        ])

        if html_size < 50000 or not is_product:
            if '404' in html or 'не знайдено' in html.lower():
                print(f"⚠️  404: {url} ({html_size}b)")
                return (html, 404)
            print(f"⚠️  Small: {url} ({html_size}b)")
        else:
            print(f"✅ OK: {url} ({html_size}b)")

        return (html, status_code)

    except Exception as e:
        print(f"❌ Error: {url} - {e}")
        return ("", 502)
    finally:
        try:
            await page.close()
        except:
            pass


def extract_price_from_json_ld(html: str) -> Optional[float]:
    """Extract from JSON-LD"""
    try:
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL | re.I)

        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, dict):
                    if data.get('@type') == 'Product' and 'offers' in data:
                        offers = data['offers']
                        if isinstance(offers, dict) and 'price' in offers:
                            return float(offers['price'])
                        elif isinstance(offers, list) and offers:
                            price = offers[0].get('price')
                            if price:
                                return float(price)
            except:
                continue
    except:
        pass
    return None


def parse_price_from_html(html: str) -> Optional[float]:
    """Universal parser"""

    price = extract_price_from_json_ld(html)
    if price:
        print(f"💰 JSON-LD: {price}")
        return price

    patterns = [
        r'class="[^"]*product-price__big[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        r'<p[^>]*product-price__big[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        r'product-price__big[^>]*>\s*([0-9\s\u00A0]+)',
        r'data-price=["\'](\d+)["\']',
        r'"price":\s*(\d+)',
        r'class="[^"]*price[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*(?:₴|грн)',
    ]

    for i, pattern in enumerate(patterns):
        try:
            m = re.search(pattern, html, re.I | re.DOTALL)
            if m:
                raw = m.group(1)
                cleaned = ''.join(c for c in raw if c.isdigit())
                if cleaned and len(cleaned) > 1:
                    price_val = float(cleaned)
                    if 10 < price_val < 10000000:
                        print(f"💰 Pattern #{i + 1}: {price_val}")
                        return price_val
        except:
            continue

    return None


def check_availability(html: str) -> str:
    """Check status"""
    is_product = any(m in html for m in [
        'product-about', 'product-main', 'product-price',
        'productId', '"@type":"Product"'
    ])

    if len(html) < 30000:
        is_product = False

    if not is_product:
        if '404' in html or 'не знайдено' in html.lower():
            return "page_not_found"
        return "invalid_page"

    if any(m in html.lower() for m in ["немає в наявності", "закінчився", "out of stock"]):
        return "out_of_stock"

    return "not_found"


@app.get("/price")
async def get_price(url: str):
    """Single price"""
    if not url.startswith("http") or 'rozetka.com.ua' not in url:
        raise HTTPException(status_code=400, detail="invalid url")

    try:
        html, status_code = await fetch_rozetka_html(url)
        if status_code == 502:
            raise HTTPException(status_code=502, detail="fetch error")
        if status_code == 404:
            raise HTTPException(status_code=404, detail="page_not_found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"error: {e}")

    price = parse_price_from_html(html)
    if price is None:
        status = check_availability(html)
        raise HTTPException(status_code=404, detail=status)

    return JSONResponse({"price": price})


@app.post("/prices")
async def get_prices(request: LinksRequest):
    """Batch prices"""
    if not request.urls:
        return JSONResponse({"prices": []})

    try:
        await get_browser_pool()
    except Exception as e:
        return JSONResponse({"prices": ["browser error" for _ in request.urls]})

    results = []

    for url in request.urls:
        if not url or not isinstance(url, str) or not url.startswith("http") or 'rozetka.com.ua' not in url:
            results.append("invalid url")
            continue

        try:
            html, status_code = await fetch_rozetka_html(url)
            if status_code == 502:
                results.append("error")
                continue

            price = parse_price_from_html(html)
            if price is not None:
                results.append(price)
            else:
                results.append(check_availability(html))
        except Exception as e:
            results.append("error")

    return JSONResponse({"prices": results})


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "rozetka-parser-stealth",
        "version": "5.0-stealth",
        "pool_size": POOL_SIZE,
        "features": [
            "playwright-stealth integration",
            f"{POOL_SIZE} browser contexts",
            "Smart rate limiting",
            "100% human-like behavior"
        ]
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "browser_pool": len(browser_pool)
    }


@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print("🥷 Rozetka Parser with PLAYWRIGHT-STEALTH")
    print("=" * 60)
    await get_browser_pool()
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    print("=" * 60)
    print("🛑 Shutdown")
    print("=" * 60)

    global playwright_instance, browser, browser_pool

    try:
        for ctx in browser_pool:
            await ctx.close()
        if browser:
            await browser.close()
        if playwright_instance:
            await playwright_instance.stop()
        print("✅ Clean shutdown")
    except Exception as e:
        print(f"⚠️  {e}")

    print("=" * 60)
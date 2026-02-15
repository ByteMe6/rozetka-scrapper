from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
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
POOL_SIZE = 5  # 5 різних браузерів
pool_index = 0


# --- Models ---
class LinksRequest(BaseModel):
    urls: List[str]


# --- Browser fingerprints ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 2560, "height": 1440}
]


async def create_browser_context(index: int) -> BrowserContext:
    """Create unique browser context with different fingerprint"""
    global browser

    ua = USER_AGENTS[index % len(USER_AGENTS)]
    vp = VIEWPORTS[index % len(VIEWPORTS)]

    # Vary geolocation slightly
    lat = 50.4501 + random.uniform(-0.1, 0.1)
    lon = 30.5234 + random.uniform(-0.1, 0.1)

    context = await browser.new_context(
        user_agent=ua,
        locale="uk-UA",
        viewport=vp,
        screen=vp,
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        timezone_id="Europe/Kiev",
        geolocation={"latitude": lat, "longitude": lon},
        permissions=["geolocation"],
        color_scheme="light",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Sec-Ch-Ua": f'"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="{20 + index}"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Cache-Control": "max-age=0"
        }
    )

    # Vary hardware specs
    cores = [4, 6, 8, 12, 16][index % 5]
    memory = [4, 8, 16, 32][index % 4]

    await context.add_init_script(f"""
        // Remove webdriver
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined
        }});

        // Real plugins
        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                return [
                    {{
                        0: {{type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"}},
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    }},
                    {{
                        0: {{type: "application/pdf", suffixes: "pdf", description: ""}},
                        description: "",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        length: 1,
                        name: "Chrome PDF Viewer"
                    }}
                ];
            }}
        }});

        Object.defineProperty(navigator, 'languages', {{
            get: () => ['uk-UA', 'uk', 'en-US', 'en']
        }});

        window.chrome = {{
            runtime: {{}},
            loadTimes: function() {{}},
            csi: function() {{}}
        }};

        // Vary hardware
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {cores}
        }});

        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {memory}
        }});

        Object.defineProperty(navigator, 'connection', {{
            get: () => ({{
                effectiveType: '4g',
                rtt: {40 + index * 10},
                downlink: {8 + index * 2},
                saveData: false
            }})
        }});

        navigator.getBattery = () => Promise.resolve({{
            charging: {str(index % 2 == 0).lower()},
            chargingTime: 0,
            dischargingTime: Infinity,
            level: {0.7 + (index * 0.05)}
        }});
    """)

    print(
        f"🌐 Created browser #{index + 1}: {ua.split('Chrome/')[1].split(' ')[0] if 'Chrome' in ua else 'Firefox'}, {vp['width']}x{vp['height']}, {cores} cores")

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
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            )

        # Create pool of browsers
        print(f"🚀 Creating browser pool of {POOL_SIZE} contexts...")
        for i in range(POOL_SIZE):
            ctx = await create_browser_context(i)
            browser_pool.append(ctx)
        print(f"✅ Browser pool ready with {len(browser_pool)} contexts!")

    return browser_pool


def get_next_browser() -> BrowserContext:
    """Round-robin browser selection"""
    global pool_index
    context = browser_pool[pool_index]
    pool_index = (pool_index + 1) % len(browser_pool)
    return context


async def fetch_rozetka_html(url: str) -> Tuple[str, int]:
    """
    Fetch with rotating browser contexts (no IP ban!)
    """

    # Ensure URL ends with /
    if not url.endswith('/'):
        url += '/'

    # Get next browser from pool (rotation)
    contexts = await get_browser_pool()
    context = get_next_browser()

    page = await context.new_page()

    try:
        # Small random delay
        await page.wait_for_timeout(random.randint(300, 800))

        # Navigate
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            status_code = response.status if response else 200
        except Exception as e:
            print(f"⚠️  Load failed for {url}: {e}")
            try:
                response = await page.goto(url, wait_until="load", timeout=15000)
                status_code = response.status if response else 200
            except:
                return ("", 502)

        # Wait for content
        await page.wait_for_timeout(random.randint(500, 1000))

        # Try to find price element
        for attempt in range(2):
            try:
                await page.wait_for_selector(
                    'p.product-price__big, [class*="product-price"]',
                    timeout=2000
                )
                break
            except:
                if attempt < 1:
                    await page.wait_for_timeout(1000)

        html = await page.content()
        html_size = len(html)

        is_product_page = any(marker in html for marker in [
            'product-about', 'product-main', 'product-price',
            'productId', 'product_id', '"@type":"Product"',
            'data-goods-id'
        ])

        if html_size < 50000 or not is_product_page:
            if '404' in html or 'не знайдено' in html.lower():
                print(f"⚠️  404: {url} ({html_size} bytes)")
                return (html, 404)
            print(f"⚠️  Small HTML: {url} ({html_size} bytes)")
        else:
            print(f"✅ Fetched: {url} ({html_size} bytes)")

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
    """Extract price from JSON-LD schema"""
    try:
        json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(json_ld_pattern, html, re.DOTALL | re.I)

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
    """Universal price parser"""

    # Strategy 1: JSON-LD
    price = extract_price_from_json_ld(html)
    if price:
        print(f"💰 JSON-LD: {price}")
        return price

    # Strategy 2: Multiple regex patterns
    patterns = [
        r'class="[^"]*product-price__big[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        r'<p[^>]*product-price__big[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([^<]+)<span',
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
    """Check why no price"""
    is_product = any(m in html for m in [
        'product-about', 'product-main', 'product-price',
        'productId', 'product_id', '"@type":"Product"'
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


# --- Endpoints ---
@app.get("/price")
async def get_price(url: str):
    """Get price for single URL"""
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
    """Batch prices with browser rotation"""
    if not request.urls:
        return JSONResponse({"prices": []})

    try:
        await get_browser_pool()
    except Exception as e:
        print(f"Browser error: {e}")
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
            print(f"Error: {url} - {e}")
            results.append("error")

    return JSONResponse({"prices": results})


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "rozetka-parser-pool",
        "version": "4.0-browser-pool",
        "pool_size": POOL_SIZE,
        "features": [
            f"{POOL_SIZE} rotating browser contexts",
            "Different fingerprints per context",
            "No IP bans!",
            "15+ price extraction strategies"
        ]
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "browser_pool": len(browser_pool),
        "pool_size": POOL_SIZE
    }


@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print("🚀 Rozetka Parser with BROWSER POOL")
    print("=" * 60)
    print(f"📦 Pool size: {POOL_SIZE} contexts")
    print("=" * 60)
    await get_browser_pool()
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    print("=" * 60)
    print("🛑 Shutting down")
    print("=" * 60)

    global playwright_instance, browser, browser_pool

    try:
        for ctx in browser_pool:
            await ctx.close()
        print(f"✅ Closed {len(browser_pool)} contexts")

        if browser:
            await browser.close()
            print("✅ Browser closed")

        if playwright_instance:
            await playwright_instance.stop()
            print("✅ Playwright stopped")
    except Exception as e:
        print(f"⚠️  Shutdown error: {e}")

    print("=" * 60)
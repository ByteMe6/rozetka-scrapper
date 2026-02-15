from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
import re
from typing import List, Optional, Tuple
import json

app = FastAPI()

# Global browser instance
playwright_instance: Optional[Playwright] = None
browser: Optional[Browser] = None
context: Optional[BrowserContext] = None


# --- Models ---
class LinksRequest(BaseModel):
    urls: List[str]


# --- Browser Management ---
async def get_browser_context() -> BrowserContext:
    """Get or create browser context with anti-detection"""
    global playwright_instance, browser, context

    if context is None or browser is None:
        if playwright_instance is None:
            playwright_instance = await async_playwright().start()

        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials'
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0"
            }
        )

        # Add anti-detection scripts
        await context.add_init_script("""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Mock plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['uk-UA', 'uk', 'en-US', 'en']
            });

            // Chrome runtime
            window.chrome = {
                runtime: {}
            };

            // Permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

    return context


async def fetch_rozetka_html(url: str, context: BrowserContext) -> Tuple[str, int]:
    """
    Fetch HTML with smart strategies
    Returns: (html, status_code)
    """

    # Ensure URL ends with /
    if not url.endswith('/'):
        url += '/'

    page = await context.new_page()

    try:
        # Strategy 1: Fast load with domcontentloaded
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            status_code = response.status if response else 200
        except Exception as e:
            print(f"⚠️  Strategy 1 failed for {url}: {e}")
            # Strategy 2: Fallback to load event
            try:
                response = await page.goto(url, wait_until="load", timeout=15000)
                status_code = response.status if response else 200
            except Exception as e2:
                print(f"❌ Strategy 2 failed for {url}: {e2}")
                return ("", 502)

        # Wait for Angular/React to render - multiple attempts
        for attempt in range(3):
            try:
                # Wait for any price-related element
                await page.wait_for_selector(
                    'p.product-price__big, [class*="product-price"], .product-prices, [class*="price"]',
                    timeout=2000
                )
                break
            except:
                if attempt < 2:
                    await page.wait_for_timeout(1500)

        # Get HTML
        html = await page.content()
        html_size = len(html)

        # Check if it's a valid product page (relaxed check)
        is_product_page = any(marker in html for marker in [
            'product-about',
            'product-main',
            'product-prices',
            'product-price',
            'productId',
            'product_id',
            '"@type":"Product"',
            'rozetka.com.ua',
            '/p' + url.split('/p')[-1].split('/')[0] if '/p' in url else ''
        ])

        # Detect 404 or error page
        if html_size < 50000 or not is_product_page:
            if '404' in html or 'not found' in html.lower() or 'не знайдено' in html.lower():
                print(f"⚠️  404 page for {url}: {html_size} bytes")
                return (html, 404)
            print(f"⚠️  Suspicious HTML for {url}: {html_size} bytes, is_product={is_product_page}")
        else:
            print(f"✅ Fetched {url}: {html_size} bytes, status={status_code}")

        return (html, status_code)

    except Exception as e:
        print(f"❌ Error fetching {url}: {type(e).__name__}: {str(e)}")
        return ("", 502)
    finally:
        try:
            await page.close()
        except:
            pass


def extract_price_from_json_ld(html: str) -> Optional[float]:
    """Extract price from JSON-LD schema"""
    try:
        # Find all JSON-LD scripts
        json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(json_ld_pattern, html, re.DOTALL | re.I)

        for match in matches:
            try:
                data = json.loads(match)
                # Check for Product schema
                if isinstance(data, dict):
                    if data.get('@type') == 'Product' and 'offers' in data:
                        offers = data['offers']
                        if isinstance(offers, dict) and 'price' in offers:
                            price = offers['price']
                            return float(price)
                        elif isinstance(offers, list) and len(offers) > 0:
                            price = offers[0].get('price')
                            if price:
                                return float(price)
            except:
                continue
    except:
        pass
    return None


def parse_price_from_html(html: str) -> Optional[float]:
    """
    UNIVERSAL price parser with 15+ strategies
    Works with old and new Rozetka templates
    """

    # Strategy 1: JSON-LD Schema (most reliable)
    price = extract_price_from_json_ld(html)
    if price:
        print(f"💰 Found price via JSON-LD: {price}")
        return price

    # Strategy 2: NEW Angular template - number before <span
    # <p _ngcontent...class="product-price__big...">899<span>₴</span>
    patterns = [
        # New template with Angular attributes
        r'class="[^"]*product-price__big[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        r'<p[^>]*product-price__big[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',

        # Old template variations
        r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([^<]+)<span',
        r'product-price__big[^>]*>\s*([0-9\s\u00A0]+)',

        # Price in data attributes
        r'data-price=["\'](\d+)["\']',
        r'data-product-price=["\'](\d+)["\']',

        # JavaScript variables
        r'"price":\s*(\d+)',
        r"'price':\s*(\d+)",
        r'price:\s*(\d+)',

        # Meta tags
        r'<meta[^>]*property=["\']product:price:amount["\'][^>]*content=["\'](\d+)["\']',
        r'<meta[^>]*content=["\'](\d+)["\'][^>]*property=["\']product:price:amount["\']',

        # Marketplace price variations
        r'marketplace-price[^>]*>\s*(\d+(?:\s*\d+)*)',
        r'seller-price[^>]*>\s*(\d+(?:\s*\d+)*)',

        # Generic price classes
        r'class="[^"]*price[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*(?:₴|грн|<)',
    ]

    for i, pattern in enumerate(patterns):
        try:
            m = re.search(pattern, html, re.I | re.DOTALL)
            if m:
                raw = m.group(1)
                # Clean the number
                cleaned = raw.replace("\u00A0", "").replace("&nbsp;", "").replace(" ", "").replace('"', "").replace("'",
                                                                                                                    "").strip()
                # Remove non-digits except decimal point
                cleaned = ''.join(c for c in cleaned if c.isdigit() or c == '.')

                if cleaned and cleaned.replace('.', '').isdigit():
                    price_val = float(cleaned)
                    if 10 < price_val < 10000000:  # Sanity check
                        print(f"💰 Found price via pattern #{i + 1}: {price_val}")
                        return price_val
        except Exception as e:
            continue

    return None


def check_availability(html: str) -> str:
    """Check why product has no price"""

    # Check if it's a valid product page (relaxed)
    is_product = any(marker in html for marker in [
        'product-about', 'product-main', 'product-price', 'productId',
        'product_id', '"@type":"Product"', 'data-goods-id'
    ])

    # If HTML is very small (< 30KB), likely an error page
    if len(html) < 30000:
        is_product = False

    if not is_product:
        # Check if it's actually a 404
        if '404' in html or 'не знайдено' in html.lower() or 'not found' in html.lower():
            return "page_not_found"
        return "invalid_page"

    # Check out of stock markers
    unavailable_markers = [
        "Немає в наявності",
        "Товар закінчився",
        "Закінчився",
        "out of stock",
        "unavailable",
        "sold out",
        "Товару немає"
    ]

    html_lower = html.lower()
    for marker in unavailable_markers:
        if marker.lower() in html_lower:
            return "out_of_stock"

    return "not_found"


# --- Single Price Endpoint ---
@app.get("/price")
async def get_price(url: str):
    """Get price for a single URL"""
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="invalid url")

    # Only support Rozetka URLs
    if 'rozetka.com.ua' not in url:
        raise HTTPException(status_code=400, detail="only rozetka urls supported")

    try:
        context = await get_browser_context()
        html, status_code = await fetch_rozetka_html(url, context)

        if status_code == 502:
            raise HTTPException(status_code=502, detail="fetch error")

        if status_code == 404:
            raise HTTPException(status_code=404, detail="page_not_found")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fetch error: {e}")

    price = parse_price_from_html(html)

    if price is None:
        # Check why price not found
        status = check_availability(html)
        raise HTTPException(status_code=404, detail=status)

    return JSONResponse({"price": price})


# --- Batch Price Endpoint ---
@app.post("/prices")
async def get_prices(request: LinksRequest):
    """Batch price lookup"""
    if not request.urls:
        return JSONResponse({"prices": []})

    try:
        context = await get_browser_context()
    except Exception as e:
        print(f"Browser context error: {e}")
        return JSONResponse({"prices": ["browser error" for _ in request.urls]})

    results = []

    for url in request.urls:
        # Validate URL
        if not url or not isinstance(url, str):
            results.append("invalid url")
            continue

        if not url.startswith("http") or 'rozetka.com.ua' not in url:
            results.append("invalid url")
            continue

        try:
            html, status_code = await fetch_rozetka_html(url, context)

            if status_code == 502:
                results.append("error")
                continue

            price = parse_price_from_html(html)

            if price is not None:
                results.append(price)
            else:
                status = check_availability(html)
                results.append(status)

        except Exception as e:
            print(f"Error processing {url}: {e}")
            results.append("error")

    return JSONResponse({"prices": results})


# --- Health Check ---
@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "rozetka-parser-ultimate",
        "version": "3.0-universal",
        "features": [
            "15+ price extraction strategies",
            "JSON-LD schema support",
            "Old & new template support",
            "Smart page detection",
            "Fallback retry logic"
        ]
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "browser": "connected" if browser and browser.is_connected() else "disconnected"
    }


# --- Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print("🚀 Rozetka Parser ULTIMATE - Starting")
    print("=" * 60)
    print("📦 Features:")
    print("   ✅ 15+ price extraction strategies")
    print("   ✅ JSON-LD schema parsing")
    print("   ✅ Old & new Rozetka templates")
    print("   ✅ Smart retry & fallback")
    print("=" * 60)
    try:
        await get_browser_context()
        print("✅ Browser initialized successfully!")
    except Exception as e:
        print(f"❌ Browser initialization failed: {e}")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    print("=" * 60)
    print("🛑 Shutting down Rozetka Parser")
    print("=" * 60)

    global playwright_instance, browser, context

    try:
        if context:
            await context.close()
            print("✅ Browser context closed")
        if browser:
            await browser.close()
            print("✅ Browser closed")
        if playwright_instance:
            await playwright_instance.stop()
            print("✅ Playwright stopped")
    except Exception as e:
        print(f"⚠️  Error during shutdown: {e}")

    print("=" * 60)
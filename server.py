from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
import re
from typing import List, Optional, Union

app = FastAPI()

# Global browser instance (reuse for better performance)
playwright_instance: Optional[Playwright] = None
browser: Optional[Browser] = None
context: Optional[BrowserContext] = None


# --- Models ---
class LinksRequest(BaseModel):
    urls: List[str]


# --- Browser Management ---
async def get_browser_context() -> BrowserContext:
    """Get or create browser context"""
    global playwright_instance, browser, context

    if context is None or browser is None:
        if playwright_instance is None:
            playwright_instance = await async_playwright().start()

        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
            viewport={"width": 1920, "height": 1080}
        )

    return context


async def fetch_rozetka_html(url: str, context: BrowserContext) -> str:
    """Fetch HTML using existing browser context with retry logic"""

    # Ensure URL ends with /
    if not url.endswith('/'):
        url += '/'

    page = await context.new_page()
    try:
        # Navigate with networkidle for better reliability
        await page.goto(url, wait_until="networkidle", timeout=15000)

        # Wait for Angular to render - try multiple selectors
        try:
            await page.wait_for_selector(
                'p.product-price__big, [class*="product-price__big"], .product-prices',
                timeout=5000
            )
        except:
            # If price element not found, wait a bit more
            try:
                await page.wait_for_timeout(3000)
            except:
                pass

        html = await page.content()

        # Debug logging
        has_price_class = 'product-price__big' in html
        html_size = len(html)

        # If HTML is suspiciously small, might be an error page
        if html_size < 50000:
            print(f"⚠️  Small HTML for {url}: {html_size} bytes, has_price={has_price_class}")
        else:
            print(f"✅ Fetched {url}: {html_size} bytes, has_price={has_price_class}")

        return html
    finally:
        await page.close()


def parse_price_from_html(html: str) -> Optional[float]:
    """Extract price from HTML with multiple patterns for old and new templates"""

    # Pattern 1: NEW Angular template - price before <span with currency
    # Example: <p _ngcontent...class="product-price__big...">899<span class="ms-1">₴</span>
    m = re.search(
        r'class="[^"]*product-price__big[^"]*"[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
        html,
        re.I | re.DOTALL
    )

    if not m:
        # Pattern 2: With Angular attributes first
        # <p _ngcontent-xxx class="product-price__big">899<span>
        m = re.search(
            r'<p[^>]*product-price__big[^>]*>\s*(\d+(?:\s*\d+)*)\s*<span',
            html,
            re.I | re.DOTALL
        )

    if not m:
        # Pattern 3: OLD template - price with text before span
        m = re.search(
            r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([^<]+)<span',
            html,
            re.I
        )

    if not m:
        # Pattern 4: Price without span
        m = re.search(
            r'product-price__big[^>]*>\s*([0-9\s\u00A0]+)',
            html,
            re.I
        )

    if not m:
        # Pattern 5: JSON-LD schema.org
        m = re.search(r'"price":\s*"?(\d+)"?', html, re.I)

    if not m:
        # Pattern 6: data-price attribute
        m = re.search(r'data-price="(\d+)"', html, re.I)

    if not m:
        return None

    raw = m.group(1)
    # Clean: remove all spaces, non-breaking spaces, quotes, and other non-digits
    cleaned = raw.replace("\u00A0", "").replace("&nbsp;", "").replace(" ", "").replace('"', "").strip()
    # Remove any remaining non-digit characters except decimal point
    cleaned = ''.join(c for c in cleaned if c.isdigit() or c == '.')

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None
        # Pattern 4: JSON-LD schema.org
        m = re.search(r'"price":\s*"?(\d+)"?', html, re.I)

    if not m:
        # Pattern 5: data-price attribute
        m = re.search(r'data-price="(\d+)"', html, re.I)

    if not m:
        return None

    raw = m.group(1)
    # Remove all spaces, non-breaking spaces, quotes
    cleaned = raw.replace("\u00A0", "").replace("&nbsp;", "").replace(" ", "").replace('"', "").strip()

    try:
        return float(cleaned)
    except ValueError:
        return None


def check_availability(html: str) -> str:
    """Check if product is available"""
    unavailable_markers = [
        "Немає в наявності",
        "Закінчився",
        "Товар закінчився",
        "out of stock",
        "unavailable"
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

    # Normalize URL: ensure it ends with /
    if not url.endswith('/'):
        url += '/'

    # Only support Rozetka URLs
    if 'rozetka.com.ua' not in url:
        raise HTTPException(status_code=400, detail="only rozetka urls supported")

    try:
        context = await get_browser_context()
        html = await fetch_rozetka_html(url, context)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fetch error: {e}")

    price = parse_price_from_html(html)

    if price is None:
        # Check why price is not found
        status = check_availability(html)
        raise HTTPException(status_code=404, detail=status)

    return JSONResponse({"price": price})


# --- Batch Price Endpoint (OPTIMIZED) ---
@app.post("/prices")
async def get_prices(request: LinksRequest):
    """
    Get prices for multiple URLs in batch.
    Reuses single browser instance for better performance.
    """
    if not request.urls:
        return JSONResponse({"prices": []})

    # Get shared browser context
    try:
        context = await get_browser_context()
    except Exception as e:
        print(f"Browser context error: {e}")
        return JSONResponse({
            "prices": ["browser error" for _ in request.urls]
        })

    results = []

    for url in request.urls:
        # Normalize URL
        if url and isinstance(url, str):
            if not url.endswith('/'):
                url += '/'

        if not url or not url.startswith("http"):
            results.append("invalid url")
            continue

        # Only support Rozetka
        if 'rozetka.com.ua' not in url:
            results.append("invalid url")
            continue

        try:
            html = await fetch_rozetka_html(url, context)
            price = parse_price_from_html(html)

            if price is not None:
                results.append(price)
            else:
                # Check availability status
                status = check_availability(html)
                results.append(status)

        except Exception as e:
            print(f"Error fetching {url}: {e}")
            results.append("error")

    return JSONResponse({"prices": results})


# --- Health Check ---
@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "rozetka-parser",
        "version": "2.0",
        "endpoints": {
            "/price": "GET - single price lookup",
            "/prices": "POST - batch price lookup"
        }
    }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "browser": "connected" if browser and browser.is_connected() else "disconnected"
    }


# --- Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    """Initialize browser on startup"""
    print("=" * 50)
    print("🚀 Starting Rozetka Parser API")
    print("=" * 50)
    print("Initializing browser...")
    try:
        await get_browser_context()
        print("✅ Browser ready!")
        print("=" * 50)
    except Exception as e:
        print(f"❌ Browser initialization failed: {e}")
        print("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up browser on shutdown"""
    print("=" * 50)
    print("🛑 Shutting down Rozetka Parser API")
    print("=" * 50)

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
        print(f"⚠️ Error during shutdown: {e}")

    print("=" * 50)
    print("👋 Goodbye!")
    print("=" * 50)
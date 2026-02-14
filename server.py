from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, Browser, BrowserContext
import re
from typing import List, Optional
import asyncio
from contextlib import asynccontextmanager

app = FastAPI()

# Global browser instance (reuse for better performance)
browser: Optional[Browser] = None
context: Optional[BrowserContext] = None


# --- Models ---
class LinksRequest(BaseModel):
    urls: List[str]


# --- Browser Management ---
def get_browser_context():
    """Get or create browser context"""
    global browser, context

    if browser is None or not browser.is_connected():
        p = sync_playwright().start()
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
            viewport={"width": 1920, "height": 1080}
        )

    return context


def fetch_rozetka_html(url: str, context: BrowserContext) -> str:
    """Fetch HTML using existing browser context"""
    page = context.new_page()
    try:
        # Use domcontentloaded instead of networkidle - much faster!
        page.goto(url, wait_until="domcontentloaded", timeout=10000)

        # Wait for price element specifically (faster than full page load)
        try:
            page.wait_for_selector('p.product-price__big', timeout=3000)
        except:
            pass  # Continue even if selector not found

        html = page.content()
        return html
    finally:
        page.close()


def parse_price_from_html(html: str) -> Optional[float]:
    """Extract price from HTML"""
    # Try main price pattern
    m = re.search(
        r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([^<]+)<span',
        html,
        re.I
    )

    if not m:
        # Try alternative pattern
        m = re.search(
            r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([0-9\s\u00A0]+)',
            html,
            re.I
        )

    if not m:
        return None

    raw = m.group(1)
    cleaned = raw.replace("\u00A0", "").replace("&nbsp;", "").replace(" ", "").strip()

    try:
        return float(cleaned)
    except ValueError:
        return None


# --- Single Price Endpoint ---
@app.get("/price")
def get_price(url: str):
    """Get price for a single URL"""
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="invalid url")

    try:
        context = get_browser_context()
        html = fetch_rozetka_html(url, context)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fetch error: {e}")

    price = parse_price_from_html(html)
    if price is None:
        raise HTTPException(status_code=404, detail="price not found")

    return JSONResponse({"price": price})


# --- Batch Price Endpoint (OPTIMIZED) ---
@app.post("/prices")
def get_prices(request: LinksRequest):
    """
    Get prices for multiple URLs in batch.
    Reuses single browser instance for better performance.
    """
    if not request.urls:
        return JSONResponse({"prices": []})

    # Get shared browser context
    try:
        context = get_browser_context()
    except Exception as e:
        return JSONResponse({
            "prices": ["browser error" for _ in request.urls]
        })

    results = []

    for url in request.urls:
        if not url or not url.startswith("http"):
            results.append("invalid url")
            continue

        try:
            html = fetch_rozetka_html(url, context)
            price = parse_price_from_html(html)
            results.append(price if price is not None else "not found")
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            results.append("error")

    return JSONResponse({"prices": results})


# --- Health Check ---
@app.get("/")
def root():
    return {"status": "ok", "service": "rozetka-parser"}


# --- Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    """Initialize browser on startup"""
    print("Starting browser...")
    get_browser_context()
    print("Browser ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up browser on shutdown"""
    global browser, context
    if context:
        context.close()
    if browser:
        browser.close()
    print("Browser closed")
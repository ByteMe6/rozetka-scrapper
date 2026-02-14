from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import re
from typing import List, Optional

app = FastAPI()

# --- Модель для POST запроса ---
class LinksRequest(BaseModel):
    urls: List[str]

def fetch_rozetka_html(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        html = page.content()
        browser.close()
    return html

def parse_price_from_html(html: str) -> Optional[float]:
    m = re.search(
        r'<p[^>]*class="[^"]*product-price__big[^"]*"[^>]*>([^<]+)<span',
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

# --- Существующий endpoint для одного товара ---
@app.get("/price")
def get_price(url: str):
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="invalid url")
    try:
        html = fetch_rozetka_html(url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fetch error: {e}")

    price = parse_price_from_html(html)
    if price is None:
        raise HTTPException(status_code=404, detail="price not found")
    return JSONResponse({"price": price})

# --- Новый endpoint для нескольких товаров ---
@app.post("/prices")
def get_prices(request: LinksRequest):
    results = []
    for url in request.urls:
        if not url.startswith("http"):
            results.append("invalid url")
            continue
        try:
            html = fetch_rozetka_html(url)
            price = parse_price_from_html(html)
            results.append(price if price is not None else "not found")
        except Exception:
            results.append("error")
    return JSONResponse({"prices": results})
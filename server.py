import re
import asyncio
import requests
from fastapi import FastAPI
from playwright.async_api import async_playwright

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbwt1suUTQNxwqTpJ0fk__WSfH_XvHBeQ32TJfqI33J1fnlHub1xSEdLvKKd6MxPbjhT/exec"

app = FastAPI()


async def scrape_price(url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        content = await page.content()
        await browser.close()

        match = re.search(r'"price":\s?([\d.]+)', content)
        if match:
            return float(match.group(1))

        match2 = re.search(r'(\d[\d\s]+)₴', content)
        if match2:
            return float(match2.group(1).replace(" ", ""))

        return None


@app.get("/update")
async def update():
    urls = [
        "https://hard.rozetka.com.ua/ua/amd-100-100000457box/p342325708/"
    ]

    for index, url in enumerate(urls, start=2):  # row 2 in sheet
        price = await scrape_price(url)

        requests.post(WEBHOOK_URL, json={
            "row": index,
            "price": price
        })

    return {"status": "done"}
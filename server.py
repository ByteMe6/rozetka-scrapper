@app.post("/prices")
async def get_prices(request: LinksRequest):
    results = []

    # Відкриваємо браузер ОДИН РАЗ для всіх URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="uk-UA",
        )

        for url in request.urls:
            if not url.startswith("http"):
                results.append("invalid url")
                continue
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=10000)  # ⚡ швидше
                html = page.content()
                page.close()  # закриваємо тільки таб

                price = parse_price_from_html(html)
                results.append(price if price is not None else "not found")
            except Exception as e:
                results.append("error")

        browser.close()

    return JSONResponse({"prices": results})
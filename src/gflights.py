"""Google Flights data via the unofficial fast-flights scraper.

Two roles in the hybrid setup:
  - full search source: primary when Amadeus keys are absent, and automatic
    backfill for any query Amadeus returns empty
  - spot-check: cross-checks top-ranked cities against what a person would see in
    the browser (stored as source 'gflights-check' so it never mixes into
    the ranking data)

Scraping is brittle by nature, so fetch modes are tried in order per query:
  1. "common"  — plain HTTP fetch of the lite page; fast, works for big routes
  2. "local"   — own headless Chromium via Playwright; handles routes the lite
                 page won't render (BOD, LIS, NCE, ...). Needs
                 `pip install playwright && playwright install chromium`.
  3. hosted fallback service — only when Playwright is missing; it is
                 token-rate-limited and unreliable.
Failures degrade to "no data for that query" instead of a failed sweep.

NOTE: Google Flights displays the total price for the whole party, so the
scraped number is used as-is.
"""
import os
import re
import time

from .timewin import filter_offers

_tls_patched = False


def _maybe_patch_local_tls():
    """Opt-in for TLS-intercepting proxies (corporate networks, CI sandboxes):
    GFLIGHTS_INSECURE_TLS=1 lets the local Chromium accept the proxy's cert."""
    global _tls_patched
    if _tls_patched or os.environ.get("GFLIGHTS_INSECURE_TLS") != "1":
        return
    import fast_flights.local_playwright as lp
    from playwright.async_api import async_playwright

    async def fetch_with_playwright(url):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context(ignore_https_errors=True)
            page = await ctx.new_page()
            await page.goto(url)
            if page.url.startswith("https://consent.google.com"):
                await page.click('text="Accept all"')
            await page.locator(".eQ35Ce").wait_for()
            body = await page.evaluate(
                "() => document.querySelector('[role=\"main\"]').innerHTML")
            await browser.close()
        return body

    lp.fetch_with_playwright = fetch_with_playwright
    _tls_patched = True


def _modes(need_times=False):
    try:
        import playwright  # noqa: F401
        _maybe_patch_local_tls()
        # the lite "common" page often omits departure times; when a hard time
        # window must be enforced, the rendered page (local) is authoritative
        return ["local", "common"] if need_times else ["common", "local"]
    except ImportError:
        return ["common", "force-fallback"]


def _fetch(origin, dest, dep_date, adults, children, retries=2, need_times=False):
    from fast_flights import FlightData, Passengers, get_flights
    last_exc = None
    for attempt in range(retries):
        for mode in _modes(need_times):
            try:
                return get_flights(
                    flight_data=[FlightData(date=dep_date, from_airport=origin,
                                            to_airport=dest)],
                    trip="one-way",
                    seat="economy",
                    passengers=Passengers(adults=adults, children=children,
                                          infants_in_seat=0, infants_on_lap=0),
                    fetch_mode=mode,
                )
            except Exception as exc:  # noqa: BLE001 — scraper failures must never kill a sweep
                last_exc = exc
        time.sleep(10 * (attempt + 1))
    print(f"  ! gflights {origin}->{dest} {dep_date}: "
          f"{type(last_exc).__name__}: {str(last_exc)[:90]}")
    return None


def _duration_min(text):  # "11 hr 35 min" -> 695
    if not text:
        return None
    h = re.search(r"(\d+)\s*hr", text)
    m = re.search(r"(\d+)\s*min", text)
    if not (h or m):
        return None
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def _clock(text):  # "12:34 PM on Thu, Oct 8" -> "12:34" (24h)
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", text or "")
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    return f"{h:02d}:{m.group(2)}"


def search(origin, dest, dep_date, adults, children, currency="USD",
           max_offers=5, window=None, **_):
    """Same contract as the other fare sources: party-total offers, cheapest
    first. The time window is applied to the FULL parsed list before
    truncation — a $900 in-window evening flight must beat out-of-window
    cheaper fares, not get cut with them."""
    result = _fetch(origin, dest, dep_date, adults, children,
                    need_times=bool(window))
    if result is None:
        return []
    offers = []
    for f in result.flights:
        digits = "".join(ch for ch in str(f.price) if ch.isdigit())
        if not digits or int(digits) <= 0:
            continue
        dep = _clock(getattr(f, "departure", None))
        offers.append({
            "price": float(digits),
            "currency": currency,
            "carrier": (f.name or None) and f.name[:32],
            "stops": f.stops if isinstance(f.stops, int) else None,
            "duration_min": _duration_min(getattr(f, "duration", None)),
            "dep_time": f"{dep_date}T{dep}" if dep else None,
            "arr_time": getattr(f, "arrival", None) or None,  # raw text, may roll to next day
        })
    offers = filter_offers(offers, window)
    offers.sort(key=lambda o: o["price"])
    time.sleep(3)  # pace queries politely
    return offers[:max_offers]


def spot_check(origin, dest, dep_date, adults, children):
    offers = search(origin, dest, dep_date, adults, children, max_offers=1)
    return offers[0] if offers else None

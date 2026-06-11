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


# ---- Layover enrichment ----------------------------------------------------
# fast-flights' parsed Flight objects expose stops as a bare count. The
# rendered results page carries the full story in each result row's
# aria-label, e.g.: "From 1505 US dollars. 2 stops flight with American.
# Leaves Dallas Fort Worth International Airport at 1:20 PM ... Layover
# (1 of 2) is a 1 hr 42 min layover at Charlotte Douglas International
# Airport. Layover (2 of 2) is a 2 hr 10 min overnight layover at Heathrow
# Airport." One extra Playwright fetch per query parses those labels to attach
# stops_detail (layover airports + durations) and backfill a missing carrier.
# Decoration tier: any failure leaves the offers as they were.

_PRICE_RE = re.compile(r"([\d,]+)\s*US dollars")
_WITH_RE = re.compile(r"flights? with ([^.]+?)\.")
_LEAVES_RE = re.compile(r"Leaves .+? at (\d{1,2}:\d{2}\s*[AP]M)", re.I)
_LAYOVER_RE = re.compile(
    r"(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?\s*(?:overnight\s+)?layover at ([^.]+?)\.", re.I)


def _shorten_airport(name):
    """Label-friendly place name: prefer the city ("Harry Reid International
    Airport in Las Vegas" -> "Las Vegas"), else strip the Airport suffix
    ("Charlotte Douglas International Airport" -> "Charlotte Douglas")."""
    name = name.strip()
    m = re.search(r"\bin\s+(.+)$", name)
    if m:
        return m.group(1).strip()
    return re.sub(r"\s+(international\s+)?airport$", "", name, flags=re.I)


def _parse_label(label):
    pm = _PRICE_RE.search(label)
    if not pm:
        return None
    layovers = [
        {"airport": _shorten_airport(a),
         "minutes": (int(h) if h else 0) * 60 + (int(m) if m else 0)}
        for h, m, a in _LAYOVER_RE.findall(label)
    ]
    wm = _WITH_RE.search(label)
    lm = _LEAVES_RE.search(label)
    return {
        "price": float(pm.group(1).replace(",", "")),
        "carrier": wm.group(1).replace(" and ", ", ") if wm else None,
        "dep_clock": _clock(lm.group(1)) if lm else None,
        "layovers": layovers,
    }


def _fetch_result_labels(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, timeout=45000)
        if page.url.startswith("https://consent.google.com"):
            page.click('text="Accept all"')
        page.locator(".eQ35Ce").first.wait_for(timeout=20000)
        labels = page.eval_on_selector_all(
            "[aria-label]", "els => els.map(e => e.getAttribute('aria-label'))")
        browser.close()
    return [t for t in labels if t and "US dollars" in t]


def _enrich_offers(origin, dest, dep_date, adults, children, offers):
    """Attach stops_detail and backfill carrier by matching page labels to
    offers on price (party total), tie-broken by departure clock."""
    from .links import gf_link
    url = gf_link(origin, dest, dep_date, adults, children)
    if not url:
        return
    parsed = [p for t in _fetch_result_labels(url) if (p := _parse_label(t))]
    if not parsed:
        return
    import json as _json
    for o in offers:
        cands = [p for p in parsed if p["price"] == o["price"]]
        if len(cands) > 1 and o.get("dep_time"):
            clock = o["dep_time"][11:16]
            timed = [p for p in cands if p["dep_clock"] == clock]
            cands = timed or cands
        if not cands:
            continue
        p = cands[0]
        if p["layovers"] and o.get("stops_detail") is None:
            o["stops_detail"] = _json.dumps(p["layovers"])
            if o.get("stops") is None:
                o["stops"] = len(p["layovers"])
        if not o.get("carrier") and p["carrier"]:
            o["carrier"] = p["carrier"][:32]


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
    offers = offers[:max_offers]
    # Enrich when something is worth fetching the rendered page again for:
    # an itinerary with stops (layover detail) or a missing carrier.
    if any((o.get("stops") or 0) >= 1 or o.get("stops") is None
           or not o.get("carrier") for o in offers):
        try:
            import playwright  # noqa: F401
            _enrich_offers(origin, dest, dep_date, adults, children, offers)
        except Exception as exc:  # noqa: BLE001 — detail is decoration
            print(f"  ~ layover enrich {origin}->{dest} {dep_date}: "
                  f"{type(exc).__name__}: {str(exc)[:70]}")
    time.sleep(3)  # pace queries politely
    return offers


def spot_check(origin, dest, dep_date, adults, children):
    offers = search(origin, dest, dep_date, adults, children, max_offers=1)
    return offers[0] if offers else None

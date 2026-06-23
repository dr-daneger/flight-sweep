"""Tier: Slickdeals — deal-event capture (community floor signal, spec §4.1).

Slickdeals surfaces human-vetted deals, which catch the flash-floor prices a
once-a-day price poll misses: the cheapest transactable units sell out before a
daily sampler sees them (survivorship / left-censoring). We read the public
per-keyword search RSS (no account) and pull the price out of each deal title.

These observations are tagged so they show on the dashboard but cannot, on their
own, clear the §4.4 BUY gates (no verified returns or seller trust) — a deal post
is a WATCH signal to go verify a live listing, never an auto-buy. Because the
seller is unknown, normalize() tags them authorization='unknown', so they inform
the price picture without entering best_buyable_net.

The exact SKU number returns nothing (deal posts use human phrasing), so we query
broad ("samsung s95f") and keep only recent titles that name the model AND match
the SKU's screen size.
"""
import html
import re
import urllib.parse
from email.utils import parsedate_to_datetime

from .base import http_get, make_obs, now_utc

_RSS = ("https://slickdeals.net/newsearch.php?q={q}"
        "&searcharea=deals&searchin=first&rss=1")
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S | re.I)
_LINK_RE = re.compile(r"<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>", re.S | re.I)
_DATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S | re.I)
_PRICE_RE = re.compile(r"\$\s?([0-9][0-9,]{1,6})(?:\.(\d{2}))?")
# condition keyword in the title -> our normalize vocab; default 'new'
_COND_KW = [("open box", "open_box"), ("open-box", "open_box"),
            ("refurb", "refurb"), ("renewed", "refurb"), ("certified", "refurb"),
            ("scratch", "scratch_dent"), ("used", "used")]


def _condition(title_lc):
    for kw, canon in _COND_KW:
        if kw in title_lc:
            return canon
    return "new"


def _size_match(title, size):
    if not size:
        return True
    # 77" / 77-prime / 77 inch / 77-inch / 77in / 77 Class, but not 770 or a price
    return re.search(rf'(?<!\d){size}\s*(?:"|″|-?\s?inch|in\b|class)',
                     title, re.I) is not None


def _deal_price(title, floor=500.0):
    """Lowest plausible TV price in the title (skips small bundle/credit amounts
    and a struck 'was $X' anchor)."""
    prices = []
    for whole, cents in _PRICE_RE.findall(title):
        prices.append(float(whole.replace(",", "") + ("." + cents if cents else "")))
    big = [p for p in prices if p >= floor]
    if big:
        return min(big)
    return min(prices) if prices else None


def _ascii(s):
    """cp1252-safe for the Windows console (titles carry the prime mark, smart
    quotes, etc.; printing them raw would raise UnicodeEncodeError)."""
    return s.encode("ascii", "replace").decode("ascii")


def fetch(sku_key, source_id, query, *, size=None, max_age_days=45,
          proxies=None, timeout=20):
    """Emit a raw observation per recent Slickdeals deal matching the SKU."""
    url = _RSS.format(q=urllib.parse.quote_plus(query))
    status, text = http_get(url, timeout=timeout, proxies=proxies)
    if status != 200 or not text:
        if status is not None:
            print(f"  ! {source_id} {sku_key}: HTTP {status}")
        return []

    fetched = now_utc()
    cutoff = fetched.timestamp() - max_age_days * 86400
    out = []
    for block in _ITEM_RE.findall(text):
        tm = _TITLE_RE.search(block)
        title = html.unescape(tm.group(1).strip()) if tm else ""
        title_lc = title.lower()
        if "s95f" not in title_lc or not _size_match(title, size):
            continue
        dm = _DATE_RE.search(block)
        if dm:
            try:
                if parsedate_to_datetime(dm.group(1).strip()).timestamp() < cutoff:
                    continue
            except (TypeError, ValueError):
                pass
        price = _deal_price(title)
        if price is None:
            continue
        lm = _LINK_RE.search(block)
        out.append(make_obs(
            sku_key, source_id, fetched,
            source_url=(lm.group(1).strip() if lm else None),
            fetch_tier="slickdeals", http_status=status, raw_price=price,
            in_stock=True, availability_text="deal_posted",
            condition_text=_condition(title_lc),
            seller_text=None,               # unknown seller -> gated out of BUY
            payload=title[:2000]))
        print(f"  $ {source_id} {sku_key}: ${price:,.0f}  {_ascii(title)[:58]}")
    if not out:
        print(f"  ~ {source_id} {sku_key}: no matching Slickdeals deals")
    return out

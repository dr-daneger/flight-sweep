"""Tier-2 fetch: structured data in the page (spec §4.1).

Parsing schema.org/Product + Offer blocks (JSON-LD) is far more stable than
CSS-selector scraping and is the preferred tier wherever an official pricing API
is absent. Any failure returns [] so a broken source never kills a run.
"""
import json

import requests

from .base import make_obs, now_utc

_HEADERS = {  # identify politely; respect rate limits at the caller
    "User-Agent": "price-oracle/0.1 (+https://github.com/dr-daneger/flight-sweep)",
    "Accept": "text/html,application/xhtml+xml",
}

_AVAIL_IN_STOCK = ("instock", "limitedavailability", "onlineonly", "instoreonly",
                   "presale", "preorder")
_COND_MAP = {
    "newcondition": "new", "usedcondition": "used", "refurbishedcondition": "refurb",
    "damagedcondition": "scratch_dent",
}


def _iter_jsonld(soup):
    import bs4
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        # JSON-LD may be a single object, a list, or an @graph wrapper.
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "@graph" in node:
                    stack.extend(node["@graph"])
                yield node


def _offers_of(node):
    offers = node.get("offers")
    if offers is None:
        return []
    return offers if isinstance(offers, list) else [offers]


def fetch(sku_key, source_id, url, *, session=None, timeout=20, proxies=None):
    """Fetch `url` and emit one raw observation per schema.org Offer found."""
    from bs4 import BeautifulSoup
    sess = session or requests.Session()
    fetched = now_utc()
    try:
        resp = sess.get(url, headers=_HEADERS, timeout=timeout, proxies=proxies)
    except requests.RequestException as exc:  # network down / blocked
        print(f"  ! {source_id} {sku_key}: {type(exc).__name__}: {str(exc)[:80]}")
        return []
    if resp.status_code != 200:
        print(f"  ! {source_id} {sku_key}: HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for node in _iter_jsonld(soup):
        if node.get("@type") not in ("Product", "IndividualProduct"):
            continue
        for off in _offers_of(node):
            price = off.get("price") or off.get("lowPrice")
            if price is None:
                continue
            avail = str(off.get("availability", "")).rsplit("/", 1)[-1].lower()
            cond = str(off.get("itemCondition", "")).rsplit("/", 1)[-1].lower()
            seller = off.get("seller", {})
            out.append(make_obs(
                sku_key, source_id, fetched, source_url=url, fetch_tier="jsonld",
                http_status=resp.status_code,
                raw_price=float(str(price).replace(",", "")),
                currency=off.get("priceCurrency", "USD"),
                in_stock=(avail in _AVAIL_IN_STOCK) if avail else None,
                availability_text=avail or None,
                condition_text=_COND_MAP.get(cond, cond or None),
                seller_text=(seller.get("name") if isinstance(seller, dict) else None),
                payload=json.dumps(off)[:2000]))
    if not out:
        print(f"  ~ {source_id} {sku_key}: no JSON-LD offers parsed")
    return out

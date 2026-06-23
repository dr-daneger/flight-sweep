"""Tier-2 fetch: structured data in the page (spec §4.1).

Parsing schema.org/Product + Offer blocks (JSON-LD) is far more stable than
CSS-selector scraping and is the preferred tier wherever an official pricing API
is absent. Any failure returns [] so a broken source never kills a run.

Transport (polite request, then curl_cffi browser-fingerprint fallback) lives in
base.http_get, so a retailer that 403s a plain client on TLS fingerprint — Abt,
for instance — is still readable, while a JS-sensor wall (Best Buy/Akamai) simply
degrades to [].
"""
import json

from .base import http_get, make_obs, now_utc

_AVAIL_IN_STOCK = ("instock", "limitedavailability", "onlineonly", "instoreonly",
                   "presale", "preorder")
_COND_MAP = {
    "newcondition": "new", "usedcondition": "used", "refurbishedcondition": "refurb",
    "damagedcondition": "scratch_dent",
}


def _iter_jsonld(soup):
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
    fetched = now_utc()
    status, text = http_get(url, timeout=timeout, proxies=proxies)
    if status != 200:
        if status is not None:
            print(f"  ! {source_id} {sku_key}: HTTP {status}")
        return []

    soup = BeautifulSoup(text, "html.parser")
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
                http_status=status,
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

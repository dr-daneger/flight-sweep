"""Tier: eBay Browse API — the secondary market (spec §4.1, §4.4).

The sub-$3k hard-trigger zone for an 83" clearance OLED is open-box / used, which
is eBay's domain. The official Browse API (no scraping, no bot wall) returns
price + condition + seller-trust for active listings — exactly the fields the
§4.4 gates need. Credentials are a free Production keyset, read from the env:

    EBAY_CLIENT_ID / EBAY_CLIENT_SECRET

Unset -> [] (logged, not fatal), so the pipeline runs without eBay. Auth is the
OAuth client-credentials grant (app-to-app; no user login needed for Browse).

Return-eligibility is not in item_summary, so returns_ok is left None here and a
listing stays gated OUT of an auto-BUY until a getItem follow-up confirms a
return-safe policy (Phase 2). eBay listings therefore inform the price picture
and the dashboard, but do not by themselves fire the hard trigger.
"""
import base64
import os

from .base import make_obs, now_utc

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"
_MARKETPLACE = "EBAY_US"

# eBay conditionId -> our normalize vocab (IDs are stable; the strings vary).
_COND_BY_ID = {
    "1000": "new", "1500": "open_box", "1750": "open_box",
    "2000": "refurb", "2010": "refurb", "2020": "refurb", "2030": "refurb",
    "2500": "refurb", "3000": "used", "4000": "used", "5000": "used",
    "6000": "used", "7000": "scratch_dent",
}
_COND_BY_TEXT = {
    "new": "new", "open box": "open_box", "new other": "open_box",
    "certified - refurbished": "refurb", "certified refurbished": "refurb",
    "excellent - refurbished": "refurb", "very good - refurbished": "refurb",
    "good - refurbished": "refurb", "seller refurbished": "refurb",
    "manufacturer refurbished": "refurb", "used": "used",
    "for parts or not working": "scratch_dent",
}


def _condition(item):
    cid = str(item.get("conditionId") or "")
    if cid in _COND_BY_ID:
        return _COND_BY_ID[cid]
    return _COND_BY_TEXT.get((item.get("condition") or "").strip().lower(), "unknown")


def _app_token(client_id, client_secret, timeout):
    import requests
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        r = requests.post(_TOKEN_URL,
                          headers={"Authorization": f"Basic {basic}",
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          data={"grant_type": "client_credentials", "scope": _SCOPE},
                          timeout=timeout)
    except requests.RequestException as exc:
        print(f"  ! ebay token: {type(exc).__name__}")
        return None
    if r.status_code != 200:
        print(f"  ! ebay token: HTTP {r.status_code} {r.text[:140]}")
        return None
    return r.json().get("access_token")


def fetch(sku_key, source_id, query, *, cfg=None, limit=50, proxies=None, timeout=30):
    """Search eBay for `query` and emit a raw observation per active listing."""
    cid = os.environ.get("EBAY_CLIENT_ID")
    secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not (cid and secret):
        print(f"  ~ {source_id} {sku_key}: EBAY_CLIENT_ID/SECRET unset, skipping")
        return []
    import requests
    token = _app_token(cid, secret, timeout)
    if not token:
        return []
    fetched = now_utc()
    try:
        r = requests.get(_SEARCH_URL,
                         params={"q": query, "limit": limit, "sort": "price"},
                         headers={"Authorization": f"Bearer {token}",
                                  "X-EBAY-C-MARKETPLACE-ID": _MARKETPLACE},
                         timeout=timeout, proxies=proxies)
    except requests.RequestException as exc:
        print(f"  ! {source_id} {sku_key}: {type(exc).__name__}")
        return []
    if r.status_code != 200:
        print(f"  ! {source_id} {sku_key}: HTTP {r.status_code} {r.text[:140]}")
        return []

    out = []
    for it in r.json().get("itemSummaries", []):
        if "s95f" not in (it.get("title") or "").lower():
            continue                          # drop accessories / wrong-model noise
        price = (it.get("price") or {}).get("value")
        if price is None:
            continue
        seller = it.get("seller") or {}
        rating = seller.get("feedbackPercentage")
        out.append(make_obs(
            sku_key, source_id, fetched, source_url=it.get("itemWebUrl"),
            fetch_tier="ebay", http_status=200, raw_price=float(price),
            currency=(it.get("price") or {}).get("currency", "USD"),
            in_stock=True, availability_text="active_listing",
            condition_text=_condition(it),
            seller_text=seller.get("username"),
            seller_rating=(float(rating) / 100.0 if rating is not None else None),
            seller_volume=seller.get("feedbackScore"),
            returns_ok=None,                  # needs a getItem follow-up (Phase 2)
            payload=str(it.get("itemId", ""))))
    if out:
        print(f"  + {source_id} {sku_key}: {len(out)} eBay listings")
    else:
        print(f"  ~ {source_id} {sku_key}: no eBay listings")
    return out

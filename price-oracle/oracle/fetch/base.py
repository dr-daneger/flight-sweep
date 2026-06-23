"""The raw-observation contract (spec §5.1) and helpers shared by every source.

A source adapter returns a list of raw-observation dicts; the tiered runner in
sources.py records which tier produced them. The raw layer is immutable: it is
captured verbatim (prices/text pre-normalization) so normalization is fully
reproducible from it.
"""
import datetime as dt
import hashlib
import uuid

# Verbatim, pre-normalization fields. Seller-trust + returns fields back the
# secondary-market BUY gates (spec §4.4); they are NULL for first-party retail.
RAW_FIELDS = (
    "observation_id", "fetched_at_utc", "sku_key", "source_id", "source_url",
    "fetch_tier", "http_status", "raw_price", "currency", "in_stock",
    "availability_text", "condition_text", "seller_text", "bundle_text",
    "seller_rating", "seller_volume", "returns_ok", "raw_payload_hash",
)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


_POLITE_HEADERS = {  # honest, self-identifying; respect rate limits at the caller
    "User-Agent": "buy-signal/0.1 (+https://github.com/dr-daneger/buy-signal)",
    "Accept": "text/html,application/xhtml+xml",
}


def http_get(url, *, timeout=20, proxies=None, headers=None):
    """Fetch `url` honestly first, then escalate to a browser fingerprint.

    Returns (status_code, text); (None, "") if the request could not complete at
    all. Shared by every HTML/RSS source adapter.

    Many retailers (Abt, Slickdeals, ...) reject a plain Python client with a
    403/503 keyed on its TLS / HTTP-2 *fingerprint*, not its IP — so we send a
    polite, self-identifying request first, and only if it is blocked do we retry
    with curl_cffi, which performs a real Chrome TLS+HTTP2 handshake. Sites gated
    behind a JS-sensor challenge (Akamai, e.g. Best Buy) still fail both; the
    caller then degrades to [] (logged, not fatal — spec §4.1).
    """
    hdrs = headers or _POLITE_HEADERS
    # 1) polite, self-identifying
    try:
        import requests
        r = requests.get(url, headers=hdrs, timeout=timeout, proxies=proxies)
        if r.status_code == 200:
            return r.status_code, r.text
        polite = f"HTTP {r.status_code}"
    except Exception as exc:                  # network down / refused / reset
        polite = type(exc).__name__
    # 2) escalate: impersonate a real browser's handshake
    try:
        from curl_cffi import requests as creq
    except ImportError:
        print(f"  ! blocked ({polite}); curl_cffi not installed to escalate")
        return None, ""
    try:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if proxies:
            kw["proxies"] = proxies
        r = creq.get(url, **kw)
        return r.status_code, r.text
    except Exception as exc:
        print(f"  ! blocked (polite {polite}; impersonate {type(exc).__name__})")
        return None, ""


def make_obs(sku_key, source_id, fetched_at, *, source_url=None, fetch_tier=None,
             http_status=None, raw_price=None, currency="USD", in_stock=None,
             availability_text=None, condition_text=None, seller_text=None,
             bundle_text=None, seller_rating=None, seller_volume=None,
             returns_ok=None, payload=""):
    """Build one raw_observations row. `payload` is the captured snippet that the
    price was read from; its hash is stored for audit (spec §5.1)."""
    return {
        "observation_id": str(uuid.uuid4()),
        "fetched_at_utc": fetched_at.isoformat(),
        "sku_key": sku_key,
        "source_id": source_id,
        "source_url": source_url,
        "fetch_tier": fetch_tier,
        "http_status": http_status,
        "raw_price": raw_price,
        "currency": currency,
        "in_stock": in_stock,
        "availability_text": availability_text,
        "condition_text": condition_text,
        "seller_text": seller_text,
        "bundle_text": bundle_text,
        "seller_rating": seller_rating,
        "seller_volume": seller_volume,
        "returns_ok": returns_ok,
        "raw_payload_hash": hashlib.sha256(
            (payload or str(raw_price)).encode("utf-8", "replace")).hexdigest()[:16],
    }

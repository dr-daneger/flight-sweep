"""Keyless, deterministic-but-plausible observations so the whole pipeline runs
without network or API keys (the analog of flight-sweep's mock_fares).

Prices follow the same terminal-decay generative model the forecaster expects —
a floor plus an exponentially decaying launch premium, pulled down by active
sale events and jittered — so a synthetic trajectory looks like a real one. The
listing mix exercises every downstream branch: corroborated authorized-new,
open-box, a gated marketplace listing, occasional out-of-stock transitions, and
a periodic scam-low to test the credibility filter. Tagged source_id "mock_*"
so it can be kept out of (or swapped in for) real ranking data, like the rest of
the engine's source tagging.
"""
import datetime as dt
import hashlib
import math
import random

from .base import make_obs, now_utc
from ..config import active_events, parse_date

# Per-SKU terminal-decay shape, tuned so the seed SKUs land on the spec's
# as-of-2026-06 anchors (83" ~$4,950 new, 77" ~$2,998 new). floor = fire-sale
# asymptote, lam = decay time-constant in days.
_PARAMS = {
    "samsung_s95f_83": {"floor": 3000.0, "lam": 820.0},
    "samsung_s95f_77": {"floor": 2000.0, "lam": 500.0},
}


def _street(cfg, sku, on_date):
    """Expected typical authorized-new street price M(tau) on `on_date`."""
    p = _PARAMS.get(sku["sku_key"], {"floor": sku["launch_msrp"] * 0.46, "lam": 760.0})
    tau = (parse_date(on_date) - parse_date(sku["launch_date"])).days
    base = p["floor"] + (sku["launch_msrp"] - p["floor"]) * math.exp(-tau / p["lam"])
    pull = sum(e.get("pull_pct", 0) for e in active_events(cfg, on_date, sku["sku_key"]))
    return base * (1 + pull / 100.0)


def _rng(sku_key, on_date):
    h = hashlib.md5(f"{sku_key}|{on_date}".encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def fetch(cfg, sku, on_date):
    """Return raw-observation rows for one SKU on one date."""
    rng = _rng(sku["sku_key"], on_date)
    m = _street(cfg, sku, on_date) * (1 + rng.uniform(-0.02, 0.02))
    fetched = now_utc()

    # Rising stockout pressure after the successor ships (spec §6.4): listings
    # increasingly read out-of-stock as the SKU approaches EOL.
    ship = next((parse_date(e["start_date"]) for e in cfg["events"]
                 if e["type"] == "successor_ship"), None)
    days_since_ship = (parse_date(on_date) - ship).days if ship else -9999
    p_out = min(0.55, 0.02 + 0.0016 * max(0, days_since_ship))

    def stock():
        return rng.random() > p_out

    rows = []
    # Authorized-new listings (corroborate each other around M).
    for sid, mult in (("mock_bestbuy", 1.00), ("mock_samsung", 1.012),
                      ("mock_crutchfield", 1.028)):
        rows.append(make_obs(
            sku["sku_key"], sid, fetched, fetch_tier="mock",
            source_url=f"https://example.test/{sid}/{sku['sku_key']}",
            http_status=200, raw_price=round(m * mult, 2), in_stock=stock(),
            availability_text="InStock", condition_text="new", seller_text=None,
            bundle_text=("$300 gift card with purchase" if sid == "mock_bestbuy"
                         and rng.random() < 0.4 else None)))
    # Open-box (authorized), return-safe — eligible for the §4.4 gates.
    rows.append(make_obs(
        sku["sku_key"], "mock_bestbuy_openbox", fetched, fetch_tier="mock",
        source_url=f"https://example.test/openbox/{sku['sku_key']}",
        http_status=200, raw_price=round(m * rng.uniform(0.82, 0.88), 2),
        in_stock=stock(), availability_text="InStock", condition_text="open_box",
        seller_text="Best Buy", returns_ok=True))
    # Marketplace third-party used — gated on seller trust + returns (spec §4.4).
    rows.append(make_obs(
        sku["sku_key"], "mock_marketplace", fetched, fetch_tier="mock",
        source_url=f"https://example.test/mkt/{sku['sku_key']}",
        http_status=200, raw_price=round(m * rng.uniform(0.74, 0.82), 2),
        in_stock=True, availability_text="InStock", condition_text="used",
        seller_text="thirdparty_seller", seller_rating=round(rng.uniform(0.95, 0.999), 3),
        seller_volume=rng.randint(20, 400), returns_ok=rng.random() < 0.7))
    # Periodic scam-low / parser-trap from an unknown seller, to prove the
    # credibility filter rejects it instead of mis-firing a BUY (spec §6.1).
    if parse_date(on_date).toordinal() % 11 == 0:
        rows.append(make_obs(
            sku["sku_key"], "mock_unknown", fetched, fetch_tier="mock",
            source_url=f"https://example.test/sketch/{sku['sku_key']}",
            http_status=200, raw_price=round(m * 0.42, 2), in_stock=True,
            availability_text="InStock", condition_text="new",
            seller_text="too_good_deals", seller_rating=0.71, seller_volume=3,
            returns_ok=False))
    return rows

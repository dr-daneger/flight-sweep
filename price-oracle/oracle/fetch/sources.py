"""Source registry + tiered fetch runner (spec §4.1).

Per source we attempt tiers in order — official API > JSON-LD > CSS > headless —
and record which tier produced the data, for provenance and health monitoring.
Coverage is intentionally partial: a source failing on a given day is logged,
not fatal, and the models forecast a trend from whatever subset responded.

Live sources are declared in config under `live_sources` (absent by default —
see README for how to add real product URLs). The `mock` mode needs no network
and keeps the pipeline green offline.
"""
from . import jsonld, mock


def _proxies(cfg):
    import os
    if cfg.get("proxy", {}).get("enabled") and os.environ.get("PROXY_URL"):
        url = os.environ["PROXY_URL"]
        return {"http": url, "https": url}
    return None


def _fetch_live_source(cfg, sku, src, proxies):
    """One live source through its tier ladder; [] (logged) on total failure."""
    tier = src.get("tier", "jsonld")
    sk, sid = sku["sku_key"], src["source_id"]
    if tier in ("jsonld", "css"):     # css falls through to the jsonld parser today
        return jsonld.fetch(sk, sid, src["url"], proxies=proxies)
    if tier == "slickdeals":          # deal-event capture (RSS); WATCH-only
        from . import slickdeals
        return slickdeals.fetch(sk, sid, src.get("query", ""),
                                size=src.get("size"), proxies=proxies)
    if tier == "ebay":                # secondary market (Browse API); needs creds
        from . import ebay
        return ebay.fetch(sk, sid, src.get("query", ""), cfg=cfg, proxies=proxies)
    # "api" and "headless" are declared seams (spec §4.1); not wired by default.
    print(f"  ~ {sid}: tier '{tier}' not implemented, skipping")
    return []


def run_fetch(cfg, on_date, mode="mock"):
    """Fetch raw observations for every configured SKU on `on_date`.

    mode: "mock" (offline synthetic), "live" (configured real sources only), or
    "auto" (live, with a loud notice if a SKU got zero live coverage that day).
    """
    rows = []
    if mode == "mock":
        for sku in cfg["skus"]:
            got = mock.fetch(cfg, sku, on_date)
            print(f"  {sku['sku_key']}: {len(got)} mock observations")
            rows.extend(got)
        return rows

    proxies = _proxies(cfg)
    live = cfg.get("live_sources", [])
    for sku in cfg["skus"]:
        sku_rows = []
        for src in [s for s in live if s.get("sku_key") == sku["sku_key"]]:
            got = _fetch_live_source(cfg, sku, src, proxies)
            if got:
                print(f"  {sku['sku_key']} <- {src['source_id']}: {len(got)} offers")
            sku_rows.extend(got)
        if not sku_rows and mode == "auto":
            print(f"  ::notice:: no live coverage for {sku['sku_key']} on {on_date}")
        rows.extend(sku_rows)
    return rows

"""Normalize raw observations into comparable offers (spec §2.2, §5.2, §6.1).

A raw scraped price is not a usable observation until it is tagged by condition
and seller authorization and reduced to a single net-effective landed price:

    net = list_price - bundle_value - conditional_discount + shipping + tax_adj

Conditions are different goods and are never pooled; gift cards are treated as
near-cash; EPP/EDU/member prices are flagged conditional, not universal.
"""
import re

OFFER_FIELDS = (
    "observation_id", "sku_key", "fetched_at_utc", "source_id", "source_url",
    "condition", "authorization", "list_price", "bundle_value",
    "conditional_discount", "shipping", "tax_rate_applied", "net_effective_price",
    "in_stock", "credibility_flag", "dedup_group_id",
    "seller_rating", "seller_volume", "returns_ok",
)

# Source roots we treat as authorized full-line retailers (spec §4.2).
_AUTHORIZED = {
    "bestbuy", "samsung", "costco", "bjs", "sams", "walmart", "crutchfield",
    "abt", "bh", "value_electronics", "amazon",
}
_CONDITIONS = {"new", "open_box", "openbox", "refurb", "refurbished",
               "used", "scratch_dent", "scratchdent"}
# "used" stays its own tag (distinct good from open-box); the owner accepts
# returnable used in scope (spec §11.4), but it is never pooled with open-box.
_COND_CANON = {"openbox": "open_box", "refurbished": "refurb",
               "scratchdent": "scratch_dent"}

_GIFTCARD_RE = re.compile(r"\$\s*([\d,]+)\s*(?:gift\s*card|gc)", re.I)
_CONDITIONAL_RE = re.compile(r"\b(epp|edu|member|student|financing)\b", re.I)


def _condition(text):
    t = (text or "").strip().lower().replace("-", "_").replace(" ", "_")
    if t in _CONDITIONS:
        return _COND_CANON.get(t, t)
    return "unknown"


def _authorization(source_id, seller_text):
    root = (source_id or "").split("_")[0] if source_id else ""
    # mock_bestbuy -> bestbuy; mock_marketplace -> marketplace
    parts = (source_id or "").split("_")
    root = parts[1] if parts[:1] == ["mock"] and len(parts) > 1 else parts[0]
    if root in _AUTHORIZED:
        return "authorized"
    if root in ("marketplace", "ebay", "backmarket", "woot") or seller_text:
        return "marketplace_3p"
    return "unknown"


def _bundle_value(text):
    m = _GIFTCARD_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else 0.0


def _dedup_group(sku_key, condition, net):
    # Collapse the same underlying offer surfaced via multiple aggregators:
    # same SKU + condition within a $10 bucket. Coarse but right at this volume.
    return f"{sku_key}|{condition}|{round(net / 10) * 10:.0f}"


def normalize(raw_rows):
    """raw_observations rows (dicts) -> normalized offer rows (dicts)."""
    offers = []
    for r in raw_rows:
        if r.get("raw_price") is None:
            continue
        condition = _condition(r.get("condition_text"))
        authorization = _authorization(r.get("source_id"), r.get("seller_text"))
        list_price = float(r["raw_price"])
        bundle = _bundle_value(r.get("bundle_text"))
        conditional = 0.0  # EPP/EDU/member are flagged, not subtracted by default
        if _CONDITIONAL_RE.search(r.get("bundle_text") or ""):
            conditional = 0.0  # kept separate; present only as a flag for now
        shipping = 0.0
        net = list_price - bundle - conditional + shipping
        offers.append({
            "observation_id": r["observation_id"],
            "sku_key": r["sku_key"],
            "fetched_at_utc": r["fetched_at_utc"],
            "source_id": r["source_id"],
            "source_url": r.get("source_url"),
            "condition": condition,
            "authorization": authorization,
            "list_price": list_price,
            "bundle_value": bundle,
            "conditional_discount": conditional,
            "shipping": shipping,
            "tax_rate_applied": None,        # not determinable from these sources
            "net_effective_price": round(net, 2),
            "in_stock": r.get("in_stock"),
            "credibility_flag": "ok",        # finalized by value.annotate_credibility
            "dedup_group_id": _dedup_group(r["sku_key"], condition, net),
            "seller_rating": r.get("seller_rating"),
            "seller_volume": r.get("seller_volume"),
            "returns_ok": r.get("returns_ok"),
        })
    return offers

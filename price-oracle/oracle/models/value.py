"""V1 — current value estimator (spec §6.1).

Three reported statistics over today's offers for one SKU:
  - L_t : lowest legitimate available net price (the actual best deal now),
          guarded by a credibility filter so a scrape error / typo'd listing
          cannot mis-fire a BUY.
  - M_t : robust street price (Huber location), characterizing "typical", so a
          single doorbuster is distinguishable from the whole market stepping
          down.
  - deal depth : M_t - L_t.

The acquirable-new-legit set is S_t = {new, authorized, in_stock, credible}.
Open-box / returnable used are tracked in parallel and become BUY-eligible only
through the secondary-market gates (spec §4.4).
"""
import numpy as np


def _huber_location(x, c=1.345, iters=25):
    """Huber M-estimator of location (robust 'typical' price). Falls back
    sensibly for tiny samples."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return float("nan")
    if len(x) <= 2:
        return float(np.median(x))
    mu = float(np.median(x))
    mad = float(np.median(np.abs(x - mu))) * 1.4826 or 1.0
    for _ in range(iters):
        r = (x - mu) / mad
        w = np.where(np.abs(r) <= c, 1.0, c / np.maximum(np.abs(r), 1e-9))
        new = float(np.sum(w * x) / np.sum(w))
        if abs(new - mu) < 1e-6:
            break
        mu = new
    return mu


def _mad(x):
    x = np.asarray(x, dtype=float)
    med = float(np.median(x))
    return med, float(np.median(np.abs(x - med))) * 1.4826


def secondary_eligible(offer, cfg):
    """Does a non-new (open-box/used/refurb) listing clear the §4.4 BUY gates?"""
    g = cfg["secondary_gates"]
    if g.get("require_returns") and not offer.get("returns_ok"):
        return False
    rating = offer.get("seller_rating")
    volume = offer.get("seller_volume")
    # Authorized open-box (e.g. retailer's own) carries the retailer's return
    # policy and needs no third-party seller rating.
    if offer.get("authorization") == "authorized":
        return True
    if rating is None or rating < g["min_seller_rating"]:
        return False
    if volume is None or volume < g["min_seller_volume"]:
        return False
    return True


def annotate_credibility(offers, cfg):
    """Flag implausible lows (spec §6.1). A net price below median - k*MAD over
    the authorized-new in-stock cohort is rejected as suspect_outlier unless it
    is corroborated by >= min_corroboration sources within a tolerance band."""
    v = cfg["value"]
    cohort = [o["net_effective_price"] for o in offers
              if o["condition"] == "new" and o["authorization"] == "authorized"
              and o.get("in_stock")]
    if len(cohort) >= 3:
        med, mad = _mad(cohort)
        floor = med - v["mad_k"] * (mad or 1.0)
    else:
        floor = -np.inf  # too few points to judge; do not reject
    for o in offers:
        net = o["net_effective_price"]
        if net >= floor:
            o["credibility_flag"] = "ok"
            continue
        # Below the plausibility band: trust only if corroborated.
        near = [x for x in offers
                if abs(x["net_effective_price"] - net) <= net * v["corroboration_tol_pct"] / 100
                and x["observation_id"] != o["observation_id"]]
        o["credibility_flag"] = "ok" if len(near) + 1 >= v["min_corroboration"] \
            else "suspect_outlier"
    return offers


def acquirable_new(offers):
    """S_t — the legit acquirable-new set."""
    return [o for o in offers if o["condition"] == "new"
            and o["authorization"] == "authorized" and o.get("in_stock")
            and o["credibility_flag"] == "ok"]


def buyable(offers, sku, cfg):
    """Every listing eligible to satisfy a BUY: legit new, plus in-scope
    secondary listings (in stock, credible) that clear the §4.4 gates."""
    scope = set(sku.get("condition_scope", ["new"]))
    out = list(acquirable_new(offers))
    for o in offers:
        if o["condition"] in ("new",) or o["condition"] not in scope:
            continue
        if o.get("in_stock") and o["credibility_flag"] == "ok" \
                and secondary_eligible(o, cfg):
            out.append(o)
    return out


def summarize(offers, sku, cfg):
    """Compute the V1 market state for one SKU's offers today."""
    s_t = acquirable_new(offers)
    new_nets = [o["net_effective_price"] for o in s_t]
    buy_set = buyable(offers, sku, cfg)
    best_buy = min(buy_set, key=lambda o: o["net_effective_price"]) if buy_set else None
    med = float(np.median(new_nets)) if new_nets else None
    return {
        "sku_key": sku["sku_key"],
        "n_offers": len(offers),
        "n_legit_new": len(s_t),
        "lowest_legit_new": float(min(new_nets)) if new_nets else None,   # L_t
        "robust_street": _huber_location(new_nets) if new_nets else None,  # M_t
        "median_new": med,
        "deal_depth": (float(_huber_location(new_nets) - min(new_nets))
                       if new_nets else None),
        "best_buyable_net": best_buy["net_effective_price"] if best_buy else None,
        "best_buyable_source": best_buy["source_id"] if best_buy else None,
        "best_buyable_condition": best_buy["condition"] if best_buy else None,
        "in_stock_any": any(o.get("in_stock") for o in offers),
        "n_suspect": sum(o["credibility_flag"] != "ok" for o in offers),
    }

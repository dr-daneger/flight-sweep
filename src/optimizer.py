"""Combine the three legs into ranked full itineraries.

Because the van covers all intra-Europe travel and ends in Bordeaux, a full
itinerary is fully determined by: outbound date choice + entry city + return
date choice. Total cost is separable per leg, so the global optimum is just
min(outbound) + min(DFW->X) + min(home) per entry city X — no combinatorial
search needed.
"""


def _best_for_leg(best, leg, origin, dest, dates):
    cands = [best[(leg, origin, dest, d)] for d in dates if (leg, origin, dest, d) in best]
    return min(cands, key=lambda o: o["price"]) if cands else None


def build_itineraries(cfg, best_now, best_prev=None):
    legs = cfg["legs"]
    out = _best_for_leg(best_now, "outbound", legs["outbound"]["origin"],
                        legs["outbound"]["dest"], legs["outbound"]["dates"])
    home = _best_for_leg(best_now, "home", legs["home"]["origin"],
                         legs["home"]["dest"], legs["home"]["dates"])
    eur_usd = cfg.get("eur_usd", 1.10)

    itins = []
    for city in cfg["entry_cities"]:
        ta = _best_for_leg(best_now, "transatlantic", legs["transatlantic"]["origin"],
                           city["code"], legs["transatlantic"]["dates"])
        if not (out and ta and home):
            continue
        flights_total = out["price"] + ta["price"] + home["price"]
        van_fee_usd = round(city["van_fee_eur"] * eur_usd, 2)
        prev_total = None
        if best_prev:
            p_out = _best_for_leg(best_prev, "outbound", legs["outbound"]["origin"],
                                  legs["outbound"]["dest"], legs["outbound"]["dates"])
            p_ta = _best_for_leg(best_prev, "transatlantic", legs["transatlantic"]["origin"],
                                 city["code"], legs["transatlantic"]["dates"])
            p_home = _best_for_leg(best_prev, "home", legs["home"]["origin"],
                                   legs["home"]["dest"], legs["home"]["dates"])
            if p_out and p_ta and p_home:
                prev_total = p_out["price"] + p_ta["price"] + p_home["price"]
        itins.append({
            **city,
            "outbound": out,
            "transatlantic": ta,
            "home": home,
            "flights_total": round(flights_total, 2),
            "van_fee_usd": van_fee_usd,
            "adjusted_total": round(flights_total + van_fee_usd, 2),
            "prev_flights_total": round(prev_total, 2) if prev_total else None,
        })
    itins.sort(key=lambda i: i["adjusted_total"])
    return itins


def find_alerts(cfg, best_now, best_prev, drop_pct):
    """Legs whose best price moved >= drop_pct % between the last two sweeps."""
    alerts = []
    if not best_prev:
        return alerts
    for key, now in best_now.items():
        prev = best_prev.get(key)
        if not prev or prev["price"] <= 0:
            continue
        change = 100 * (now["price"] - prev["price"]) / prev["price"]
        if abs(change) >= drop_pct:
            leg, origin, dest, dep_date = key
            alerts.append({
                "leg": leg, "origin": origin, "dest": dest, "dep_date": dep_date,
                "prev": prev["price"], "now": now["price"],
                "change_pct": round(change, 1),
            })
    alerts.sort(key=lambda a: a["change_pct"])
    return alerts

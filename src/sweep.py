"""Run one price sweep: fetch fares, persist, optimize, render the dashboard.

Usage:
  python -m src.sweep                       # auto: Amadeus if keys exist, else
                                            #   Google Flights scraper; Amadeus
                                            #   gaps are backfilled from GF
  python -m src.sweep --source gflights     # force scraper-only (no keys needed)
  python -m src.sweep --mock                # generated fares, for development
  python -m src.sweep --spot-check 3        # also GF-cross-check top 3 cities
"""
import argparse
import datetime
import os
from pathlib import Path

import yaml

from . import links, mock_fares, optimizer, render, store, timewin

ROOT = Path(__file__).resolve().parent.parent


def hkey(leg, o, d, dt):
    return "|".join([leg, o, d, dt])


def normalize_legs(cfg):
    """Date entries may be plain strings or {date, dep_after, dep_before}.
    Flatten to legs[x]['dates'] = [str] and legs[x]['windows'] = {date: window}."""
    for leg in cfg["legs"].values():
        dates, windows = [], {}
        for d in leg["dates"]:
            if isinstance(d, str):
                dates.append(d)
            else:
                dates.append(d["date"])
                w = {k: v for k, v in d.items() if k in ("dep_after", "dep_before")}
                if w:
                    windows[d["date"]] = w
        leg["dates"] = dates
        leg["windows"] = windows


def apply_window(offers, win):
    kept = timewin.filter_offers(offers, win)
    return kept, len(offers) - len(kept)


def fetch_all(cfg, sources, con, sweep_id):
    """sources = [(name, search_fn), ...] tried in order per query (backfill)."""
    trav = cfg["travelers"]
    kw = dict(adults=trav["adults"], children=trav["children"],
              currency=cfg["currency"], max_offers=cfg["amadeus"]["max_offers"])
    legs = cfg["legs"]
    queries = []
    for d in legs["outbound"]["dates"]:
        queries.append(("outbound", legs["outbound"]["origin"], legs["outbound"]["dest"], d))
    for d in legs["transatlantic"]["dates"]:
        for city in cfg["entry_cities"]:
            queries.append(("transatlantic", legs["transatlantic"]["origin"], city["code"], d))
    for d in legs["home"]["dates"]:
        queries.append(("home", legs["home"]["origin"], legs["home"]["dest"], d))

    for leg, origin, dest, dep_date in queries:
        win = cfg["legs"][leg].get("windows", {}).get(dep_date)
        offers, used, dropped = [], None, 0
        for name, search in sources:
            offers = search(origin, dest, dep_date, cabin=cfg["cabin"],
                            window=win, **kw)
            offers, dropped = apply_window(offers, win)  # safety net
            if offers:
                used = name
                break
            if len(sources) > 1:
                print(f"  {origin}->{dest} {dep_date}: {name} empty, trying backup...")
        tag = f" [{used}]" if used and used != sources[0][0] else ""
        wtag = f", {dropped} outside time window" if dropped else ""
        print(f"  {origin}->{dest} {dep_date}: "
              + (f"${offers[0]['price']:,.0f} ({len(offers)} offers{wtag}){tag}"
                 if offers else f"no offers within constraints{wtag}"))
        if offers:
            store.insert_offers(con, sweep_id, used, leg, origin, dest, dep_date, offers)


def run_spot_checks(cfg, con, sweep_id, itins, top_n):
    from . import gflights
    trav = cfg["travelers"]
    print(f"Google Flights spot-check (top {top_n} cities)...")
    for it in itins[:top_n]:
        r = it["transatlantic"]
        best = gflights.spot_check(r["origin"], r["dest"], r["dep_date"],
                                   trav["adults"], trav["children"])
        if best:
            print(f"  GF {r['origin']}->{r['dest']}: ${best['price']:,.0f}")
            store.insert_offers(con, sweep_id, "gflights-check", "transatlantic",
                                r["origin"], r["dest"], r["dep_date"], [best])


def pick_sources(mode, cfg):
    """Return [(name, search_fn), ...] tried in order per query."""
    from . import gflights
    have_keys = bool(os.environ.get("AMADEUS_CLIENT_ID")
                     and os.environ.get("AMADEUS_CLIENT_SECRET"))
    if mode == "mock":
        return [("mock", mock_fares.search)]
    if mode == "gflights":
        return [("gflights", gflights.search)]
    if mode == "amadeus" or (mode == "auto" and have_keys):
        from .amadeus_client import AmadeusClient
        client = AmadeusClient(cfg["amadeus"]["hostname"])
        sources = [("amadeus", client.search)]
        if mode == "auto":
            sources.append(("gflights", gflights.search))  # backfill Amadeus gaps
        return sources
    print("No Amadeus keys found — sweeping via Google Flights scraper.")
    return [("gflights", gflights.search)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "amadeus", "gflights", "mock"],
                    default="auto",
                    help="auto = Amadeus with Google Flights backfill if keys "
                         "exist, else Google Flights scraper only")
    ap.add_argument("--mock", action="store_true", help="shorthand for --source mock")
    ap.add_argument("--spot-check", type=int, default=0, metavar="N",
                    help="Google-Flights-check the top N entry cities")
    ap.add_argument("--render-only", action="store_true",
                    help="rebuild the dashboard from existing data, no fetching")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--db", default=str(ROOT / "data" / "prices.sqlite"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "index.html"))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    normalize_legs(cfg)
    con = store.connect(args.db)
    mode = "mock" if args.mock else args.source
    # mock history stays separate from real data sharing the same db
    rank_sources = ("mock",) if mode == "mock" else ("amadeus", "gflights")

    if args.render_only:
        ids = store.sweep_ids(con, rank_sources)
        if not ids:
            raise SystemExit("no sweeps in the database; run a sweep first")
        sweep_id = ids[-1]  # re-render the latest sweep as-is
        row = con.execute("SELECT source FROM fare_snapshots WHERE sweep_id = ? LIMIT 1",
                          (sweep_id,)).fetchone()
        source = row[0] if row else "unknown"
    else:
        sweep_id = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        sources = pick_sources(mode, cfg)
        source = "+".join(name for name, _ in sources)
        print(f"Sweep {sweep_id} ({source})")
        fetch_all(cfg, sources, con, sweep_id)
        ids = store.sweep_ids(con, rank_sources)
    # latest_offers falls back to the freshest prior price (marked stale) for
    # any query this sweep missed, so flaky sources don't drop cities
    best_now = store.latest_offers(con, sweep_id, rank_sources)
    prev_id = ids[-2] if len(ids) >= 2 else None
    best_prev = store.best_offers(con, prev_id, rank_sources) if prev_id else None

    trav = cfg["travelers"]
    for (leg, o, d, dt), offer in best_now.items():
        offer["gf_url"] = links.gf_link(o, d, dt, trav["adults"], trav["children"])

    itins = optimizer.build_itineraries(cfg, best_now, best_prev)
    alerts = optimizer.find_alerts(cfg, best_now, best_prev, cfg["alerts"]["drop_pct"])

    if args.spot_check and not args.render_only:
        if sources[0][0] == "gflights":
            print("Skipping spot-check: Google Flights is already the primary source.")
        else:
            run_spot_checks(cfg, con, sweep_id, itins, args.spot_check)

    legs = cfg["legs"]
    payload = {
        "meta": {"sweep_id": sweep_id, "source": source, "n_sweeps": len(ids),
                 "adults": cfg["travelers"]["adults"], "children": cfg["travelers"]["children"]},
        "itineraries": itins,
        "alerts": alerts,
        "legs": {
            "outbound": [best_now[k] for d in legs["outbound"]["dates"]
                         if (k := ("outbound", legs["outbound"]["origin"],
                                   legs["outbound"]["dest"], d)) in best_now],
            "home": [best_now[k] for d in legs["home"]["dates"]
                     if (k := ("home", legs["home"]["origin"],
                               legs["home"]["dest"], d)) in best_now],
        },
        "windows": {f"{leg}|{d}": w for leg, lc in cfg["legs"].items()
                    for d, w in lc.get("windows", {}).items()},
        "history": {hkey(*k): v for k, v in store.price_history(con, rank_sources).items()},
        "gflights": {hkey(*k): v for k, v in store.gflights_checks(con, sweep_id).items()},
    }
    out = render.render(payload, args.out)

    print(f"\nTop 3 itineraries (flights + est. van drop fee, {cfg['travelers']['adults'] + cfg['travelers']['children']} seats):")
    for i, it in enumerate(itins[:3], 1):
        print(f"  {i}. via {it['city']:<10} ${it['adjusted_total']:>8,.0f}"
              f"  (flights ${it['flights_total']:,.0f} + van ~${it['van_fee_usd']:,.0f})")
    if alerts:
        print(f"{len(alerts)} price move(s) >= {cfg['alerts']['drop_pct']}% since last sweep")
    print(f"Dashboard: {out}")


if __name__ == "__main__":
    main()

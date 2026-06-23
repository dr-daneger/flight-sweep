"""Daily run: fetch -> normalize -> store -> models -> validate -> report -> alert.

  python -m oracle.run                      # mock sources (offline, default)
  python -m oracle.run --source auto        # configured live sources, mock-free
  python -m oracle.run --seed-history       # backfill synthetic history first
  python -m oracle.run --render-only        # rebuild outputs from stored data

Coverage is intentionally partial and failures degrade gracefully (spec §4.1):
a source missing on a given day is logged, not fatal.
"""
import argparse
import datetime as dt

import pandas as pd

from . import alert, config, normalize, report, store, validate
from .fetch import sources
from .models import MODEL_VERSION, decision, forecast, hazard, lifecycle, value


def _run_id():
    # Colon-free, NTFS-safe: this string becomes the Parquet filename and ':' is
    # illegal on Windows (reserved for drive letters / alternate data streams).
    # Hyphens keep it lexicographically sortable, which is all the store relies on.
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")


def _market_state_row(ms, sku, on_date, run_id):
    return {**ms, "date": str(on_date), "run_id": run_id,
            "model_version": MODEL_VERSION, "role": sku.get("role")}


def seed_history(cfg, dd, end_date, step=7):
    """Backfill synthetic market_state_daily from launch to end_date so the
    forecaster/hazard have a trajectory on first run (one-time, spec §4.3). Uses
    the mock generator; replace with a Keepa backfill when KEEPA_API_KEY is set.
    Tagged run_id 'seed' so it is distinguishable from live runs."""
    from .fetch import mock
    rows_by_date = {}
    for sku in cfg["skus"]:
        start = config.parse_date(sku["launch_date"]) + dt.timedelta(days=30)
        d = start
        while d < config.parse_date(end_date):
            raw = mock.fetch(cfg, sku, str(d))
            offers = value.annotate_credibility(normalize.normalize(raw), cfg)
            ms = value.summarize(offers, sku, cfg)
            rows_by_date.setdefault(str(d), []).append(
                _market_state_row(ms, sku, d, "seed"))
            d += dt.timedelta(days=step)
    for d, rows in rows_by_date.items():
        store.write_table(dd, "market_state_daily", rows, "seed", d)
    print(f"Seeded {sum(len(r) for r in rows_by_date.values())} market-state rows "
          f"({len(rows_by_date)} dates).")


def _load_history(dd, sku_key):
    """Per-date history for one SKU, latest run per date, oldest first, with a
    censoring flag for stockout days (spec §2.1).

    L is the best BUYABLE net (the decision variable — lowest transactable price
    across in-scope conditions passing the §4.4 gates), so the forecast that
    feeds the stopping threshold predicts the same quantity the decision acts on.
    M is the robust NEW street price, the typical-price anchor for the decay
    shape (conditions are never pooled into it)."""
    df = store.read_table(dd, "market_state_daily", where=f"sku_key = '{sku_key}'")
    if df.empty:
        return []
    df = df.sort_values(["date", "run_id"]).groupby("date", as_index=False).last()
    hist = []
    for _, r in df.iterrows():
        hist.append({
            "date": str(r["date"]),
            "M": (float(r["robust_street"]) if pd.notna(r.get("robust_street")) else None),
            "L": (float(r["best_buyable_net"]) if pd.notna(r.get("best_buyable_net")) else None),
            "censored": not bool(r.get("in_stock_any")),
        })
    return hist


def _load_stored_state(dd, cfg):
    """Reconstruct the most recent run's offers + market state from the store,
    for --render-only (rebuild outputs without fetching)."""
    ms_df = store.read_table(dd, "market_state_daily",
                             where="run_id <> 'seed'")
    if ms_df.empty:
        raise SystemExit("no live runs in the store; run a full sweep first")
    on_date = sorted(ms_df["date"].astype(str).unique())[-1]
    run_id = sorted(ms_df[ms_df["date"].astype(str) == on_date]["run_id"])[-1]
    sku_state = {r["sku_key"]: {k: r[k] for k in r.index if k not in
                                ("date", "run_id", "model_version")}
                 for _, r in ms_df[(ms_df["date"].astype(str) == on_date)
                                   & (ms_df["run_id"] == run_id)].iterrows()}
    off_df = store.read_table(dd, "offers", where=f"observation_id is not null")
    sku_offers = {}
    if not off_df.empty:
        off_df = off_df[off_df["fetched_at_utc"].astype(str).str.startswith(on_date)]
        for sk, grp in off_df.groupby("sku_key"):
            sku_offers[sk] = grp.to_dict("records")
    return on_date, sku_offers, sku_state


def _prev_decision(dd, sku_key, before_date):
    df = store.read_table(dd, "decisions", where=f"sku_key = '{sku_key}'")
    if df.empty:
        return None
    df = df[df["date"] < str(before_date)].sort_values("date")
    return df.iloc[-1].to_dict() if not df.empty else None


def run(mode="mock", on_date=None, do_seed=False, render_only=False):
    cfg = config.load()
    dd = config.data_dir(cfg)
    on_date = on_date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    run_id = _run_id()
    primary_key = cfg["decision"]["primary_sku"]

    if do_seed:
        seed_history(cfg, dd, on_date)

    sku_offers = {}        # sku_key -> today's annotated offers
    sku_state = {}         # sku_key -> today's market_state
    if render_only:
        on_date, sku_offers, sku_state = _load_stored_state(dd, cfg)
        print(f"Render-only: rebuilding from stored data for {on_date}")
    else:
        print(f"Run {run_id} ({mode}) for {on_date}")
        raw = sources.run_fetch(cfg, on_date, mode)
        store.write_table(dd, "raw_observations", raw, run_id, on_date)
        offers_all = value.annotate_credibility(normalize.normalize(raw), cfg)
        store.write_table(dd, "offers", offers_all, run_id, on_date)
        ms_rows = []
        for sku in cfg["skus"]:
            offs = [o for o in offers_all if o["sku_key"] == sku["sku_key"]]
            ms = value.summarize(offs, sku, cfg)
            sku_offers[sku["sku_key"]] = offs
            sku_state[sku["sku_key"]] = ms
            ms_rows.append(_market_state_row(ms, sku, on_date, run_id))
        # One write per run: each run's rows share a {run_id}.parquet filename,
        # so all SKUs must go in a single call or they would clobber each other.
        store.write_table(dd, "market_state_daily", ms_rows, run_id, on_date)

    # ---- models for the primary SKU ---------------------------------------
    primary = cfg["skus_by_key"][primary_key]
    fallback_key = next((s["sku_key"] for s in cfg["skus"]
                         if s.get("role") == "fallback"), None)
    history = _load_history(dd, primary_key)
    prior = lifecycle.build_prior(cfg, primary)
    fc = forecast.build(cfg, primary, prior, history, on_date)
    hz = hazard.HazardModel(cfg, primary, history, on_date)
    ms = sku_state.get(primary_key) or value.summarize([], primary, cfg)
    sub_state = sku_state.get(fallback_key) if fallback_key else None
    dec = decision.decide(cfg, primary, ms, fc, hz, on_date, substitute_state=sub_state)
    store.write_table(dd, "decisions", [dec], run_id, on_date)

    # ---- forecast / hazard / prediction tables (weekly horizon) -----------
    horizon = cfg["forecast"]["horizon_days"]
    plot_dates = [str(d) for d in forecast.daterange(
        on_date, (config.parse_date(on_date) + dt.timedelta(days=horizon)).isoformat(), 7)]
    fc_rows = fc.quantiles(plot_dates)
    store.write_table(dd, "forecasts",
                      [{**r, "run_id": run_id, "sku_key": primary_key,
                        "model_version": MODEL_VERSION} for r in fc_rows],
                      run_id, on_date)
    surv = hz.survival(plot_dates)
    store.write_table(dd, "hazard_curve",
                      [{"run_id": run_id, "sku_key": primary_key, "date": d,
                        "S": s} for d, s in zip(plot_dates, surv)],
                      run_id, on_date)
    if not render_only:
        store.write_table(dd, "predictions_log",
                          validate.prediction_rows(fc, primary, run_id, on_date, plot_dates),
                          run_id, on_date)

    # ---- validation -------------------------------------------------------
    anomalies = validate.detect_anomalies(sku_offers.get(primary_key, []), ms, cfg)
    calib = validate.score_calibration(cfg, primary_key)
    health = validate.data_health(cfg, ms, history, fc, on_date)

    # ---- payload + outputs ------------------------------------------------
    by_source = sorted(
        ({"source_id": o["source_id"], "condition": o["condition"],
          "authorization": o["authorization"],
          "net_effective_price": o["net_effective_price"],
          "in_stock": bool(o.get("in_stock")), "credibility_flag": o["credibility_flag"]}
         for o in sku_offers.get(primary_key, [])),
        key=lambda o: o["net_effective_price"])
    watchlist = [{"sku_key": k, "label": cfg["skus_by_key"][k]["label"],
                  "role": cfg["skus_by_key"][k].get("role"), **sku_state[k]}
                 for k in sku_state if k != primary_key]
    payload = {
        "meta": {"run_id": run_id, "date": on_date, "source": mode,
                 "n_hist": len(history), "model_version": MODEL_VERSION},
        "primary": {
            "sku_key": primary_key, "label": primary["label"],
            "decision": dec, "market_state": ms, "by_source": by_source,
            "history": [{"date": h["date"], "L": h["L"], "M": h["M"]} for h in history],
            "forecast": [{"date": r["date"], "q05": r["low_q05"], "q25": r["low_q25"],
                          "q50": r["low_q50"], "q75": r["low_q75"], "q95": r["low_q95"],
                          "mean_street": r["mean_street"]} for r in fc_rows],
            "survival": [{"date": d, "S": s} for d, s in zip(plot_dates, surv)],
            "health": health,
        },
        "watchlist": watchlist,
        "anomalies": anomalies,
        "calibration": calib,
    }
    docs = cfg["_root"] / "docs"
    report.render_html(payload, docs / "index.html", title=primary["label"])
    report.render_markdown(payload, docs / "report.md")

    # ---- alerts -----------------------------------------------------------
    prev = _prev_decision(dd, primary_key, on_date)
    alerts = alert.build_alerts(cfg, dec, anomalies, prev)
    alert.emit(alerts, docs / "alert.json")

    bln = f"${dec['best_legit_now']:,.0f}" if dec["best_legit_now"] else "n/a"
    sub = (f"live 77\" ${dec['substitute_price']:,.0f}" if dec["substitute_is_live"]
           else f"77\" anchor ${dec['substitute_price']:,.0f}")
    hzn = ("deadline " if dec["horizon_is_deadline"] else "stockout-bounded horizon ")
    print(f"\nVERDICT {dec['verdict']} — best legit {bln} "
          f"vs threshold ${dec['threshold']:,.0f} (fallback {sub} + K)")
    print(f"  P(buyable at {hzn}{dec['horizon_end']}) = {dec['p_available_horizon']:.0%}"
          f" · prior weight {dec['prior_weight']:.0%} · driver: {dec['dominant_driver']}")
    print(f"  DATA HEALTH: {health['confidence']} — {health['summary']}")
    if anomalies:
        print(f"  {len(anomalies)} data anomaly(ies) flagged")
    print(f"Dashboard: {docs / 'index.html'}")
    return dec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["mock", "live", "auto"], default="mock")
    ap.add_argument("--date", default=None, help="run date (YYYY-MM-DD), UTC today by default")
    ap.add_argument("--seed-history", action="store_true",
                    help="one-time synthetic backfill of market-state history")
    ap.add_argument("--render-only", action="store_true",
                    help="rebuild outputs from stored data, no fetching")
    args = ap.parse_args()
    run(mode=args.source, on_date=args.date, do_seed=args.seed_history,
        render_only=args.render_only)


if __name__ == "__main__":
    main()

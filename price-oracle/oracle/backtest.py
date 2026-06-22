"""Backtesting harness (spec §7, §10 Phase 3): replay history to a cutoff,
forecast forward, score against held-out actuals. Surfaces the accuracy trend
and whether the reference-class prior's weight demonstrably shrinks as live data
accrues. Run weekly by backtest.yml.

  python -m oracle.backtest
"""
import datetime as dt

import numpy as np

from . import config, store
from .models import lifecycle, forecast
from .validate import _pinball


def _truth_series(dd, sku_key):
    df = store.read_table(dd, "market_state_daily", where=f"sku_key = '{sku_key}'")
    if df.empty:
        return {}
    df = df.sort_values(["date", "run_id"]).groupby("date", as_index=False).last()
    return {str(r["date"]): float(r["best_buyable_net"])
            for _, r in df.iterrows() if r.get("best_buyable_net") is not None}


def run():
    cfg = config.load()
    dd = config.data_dir(cfg)
    sku_key = cfg["decision"]["primary_sku"]
    sku = cfg["skus_by_key"][sku_key]
    prior = lifecycle.build_prior(cfg, sku)
    truth = _truth_series(dd, sku_key)
    dates = sorted(truth)
    if len(dates) < 8:
        print("Not enough history to backtest (need >= 8 observed dates).")
        return

    # Walk-forward: cut at successive points, forecast the next ~28 days, score.
    horizons = [7, 14, 28]
    results = []  # (cutoff, n_obs, horizon, pinball, in_interval)
    for i in range(4, len(dates) - 1):
        cutoff = dates[i]
        hist = [{"date": d, "M": truth[d], "L": truth[d], "censored": False}
                for d in dates[:i + 1]]
        fc = forecast.build(cfg, sku, prior, hist, cutoff)
        for h in horizons:
            target = (config.parse_date(cutoff) + dt.timedelta(days=h)).isoformat()
            future = [t for t in dates if cutoff < t <= target]
            if not future:
                continue
            tgt = future[-1]
            row = fc.quantiles([tgt])[0]
            y = truth[tgt]
            results.append((cutoff, len(hist), h,
                            float(np.mean(_pinball(y, row["low_q50"], 0.5))),
                            int(row["low_q05"] <= y <= row["low_q95"]),
                            round(fc.w_data, 3)))

    if not results:
        print("No scorable (cutoff, horizon) pairs.")
        return
    pin = np.mean([r[3] for r in results])
    cov = np.mean([r[4] for r in results])
    w_first, w_last = results[0][5], results[-1][5]

    lines = [
        "# price-oracle backtest scorecard", "",
        f"_Walk-forward over {len(results)} (cutoff, horizon) pairs · "
        f"{config.parse_date(dt.datetime.now(dt.timezone.utc).date().isoformat())}_", "",
        "| Metric | Value |", "|---|---|",
        f"| Mean pinball loss (q50) | {pin:,.1f} |",
        f"| 90% interval coverage | {cov:.0%} (target 90%) |",
        f"| Data weight, first→last cutoff | {w_first:.0%} → {w_last:.0%} "
        f"(prior weight shrinks as live data accrues) |",
        "",
        "## By horizon", "", "| Horizon (days) | n | Mean pinball | Coverage |",
        "|---:|---:|---:|---:|",
    ]
    for h in horizons:
        sub = [r for r in results if r[2] == h]
        if sub:
            lines.append(f"| {h} | {len(sub)} | {np.mean([r[3] for r in sub]):,.1f} "
                         f"| {np.mean([r[4] for r in sub]):.0%} |")
    docs = cfg["_root"] / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "backtest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {docs / 'backtest.md'}")


if __name__ == "__main__":
    run()

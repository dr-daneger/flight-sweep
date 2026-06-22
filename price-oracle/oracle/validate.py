"""Self-improvement & validation (spec §7) — makes "improves over time" auditable.

  * Prediction logging: every forecast is persisted with its made-on date,
    target date, horizon, model version, and quantiles (predictions_log), so
    calibration can be measured later.
  * Calibration scoring: once truth is observed, score pinball loss and check
    empirical coverage (did the 80% interval contain truth ~80% of the time?).
  * Drift / parser-break detection: per-source sanity checks. A silently broken
    parser corrupts V1 invisibly and can mis-fire a BUY — the most dangerous
    failure — so anomalies raise an alert rather than a silent NaN.
"""
import numpy as np

from . import store
from .config import parse_date
from .models import MODEL_VERSION


def prediction_rows(forecaster, sku, run_id, made_on, target_dates):
    """Quantile forecasts to persist for later calibration scoring."""
    rows = []
    for r in forecaster.quantiles(target_dates):
        rows.append({
            "run_id": run_id, "sku_key": sku["sku_key"],
            "made_on": str(made_on), "target_date": r["date"],
            "horizon_days": r["horizon_days"], "model_version": MODEL_VERSION,
            "pred_low_q05": r.get("low_q05"), "pred_low_q25": r.get("low_q25"),
            "pred_low_q50": r.get("low_q50"), "pred_low_q75": r.get("low_q75"),
            "pred_low_q95": r.get("low_q95"), "pred_mean_low": r["mean_low"],
        })
    return rows


def _pinball(y, q, tau):
    d = y - q
    return np.where(d >= 0, tau * d, (tau - 1) * d)


def score_calibration(cfg, sku_key=None):
    """Join matured predictions to realized lows and report pinball + coverage."""
    dd = cfg["_root"] / cfg["storage"]["data_dir"]
    preds = store.read_table(dd, "predictions_log")
    actuals = store.read_table(dd, "market_state_daily")
    if preds.empty or actuals.empty:
        return {"n_scored": 0, "note": "not enough history yet"}
    if sku_key:
        preds = preds[preds["sku_key"] == sku_key]
        actuals = actuals[actuals["sku_key"] == sku_key]

    # realized achievable buyable low per (sku, date) — the quantity forecast
    truth = {(r["sku_key"], str(r["date"])): r["best_buyable_net"]
             for _, r in actuals.iterrows() if r.get("best_buyable_net") is not None}

    n, pin50, cov80, cov_lo, cov_hi = 0, [], 0, [], []
    for _, p in preds.iterrows():
        key = (p["sku_key"], str(p["target_date"]))
        if key not in truth or parse_date(p["target_date"]) > parse_date(
                max(str(d) for _, d in actuals["date"].items())):
            continue
        y = float(truth[key])
        if p.get("pred_low_q50") is None:
            continue
        n += 1
        pin50.append(float(np.mean(_pinball(y, float(p["pred_low_q50"]), 0.5))))
        lo, hi = p.get("pred_low_q05"), p.get("pred_low_q95")  # ~90% interval
        if lo is not None and hi is not None and lo <= y <= hi:
            cov80 += 1
    return {
        "n_scored": n,
        "pinball_q50": round(float(np.mean(pin50)), 2) if pin50 else None,
        "coverage_90": round(cov80 / n, 3) if n else None,
        "model_version": MODEL_VERSION,
    }


def detect_anomalies(offers, market_state, cfg):
    """Per-source sanity checks (spec §7). Returns a list of anomaly dicts."""
    anomalies = []
    if not offers:
        anomalies.append({"kind": "no_rows", "detail": "fetch returned zero offers"})
        return anomalies
    med = market_state.get("median_new")
    by_source = {}
    for o in offers:
        by_source.setdefault(o["source_id"], []).append(o)
    for sid, rows in by_source.items():
        nets = [r["net_effective_price"] for r in rows]
        if med and min(nets) < 0.4 * med:
            anomalies.append({"kind": "source_out_of_band", "source": sid,
                              "detail": f"min net ${min(nets):,.0f} << median ${med:,.0f}"})
        if any(r["condition"] == "unknown" and r["authorization"] == "unknown"
               for r in rows):
            anomalies.append({"kind": "unparsed_fields", "source": sid,
                              "detail": "condition+authorization both unresolved"})
    return anomalies

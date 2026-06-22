"""Decision layer — finite-horizon optimal stopping (spec §6.5).

Combines V1 (best legit price now), V2 (posterior-predictive future lows), and
V3 (survival) with the owner's utility into a daily threshold policy:

  * Hard override: any credible, return-safe listing at net <= hard_buy_trigger
    emits BUY immediately, bypassing the model (owner rule).
  * Otherwise BUY iff L_t <= threshold*(t), where threshold*(t) is the
    continuation certainty-equivalent — the expected cost of waiting optimally,
    integrating future price AND the probability the chance to buy is lost.

threshold*(t) is computed by backward induction over the days to the deadline
(spec's "cleaner" option; the horizon to a mid-July deadline is short). Failing
to buy before the SKU is gone costs substitute_price + K (the resolved 77"
fallback plus a regret penalty). The verdict is explainable: it carries the
numeric threshold, the expected savings from waiting, the stockout probability
before the next forecast trough, and the single dominant uncertainty driver.
"""
import numpy as np

from ..config import parse_date
from .forecast import daterange


def _backward_threshold(cfg, sku, forecaster, hazard, today):
    """Continuation certainty-equivalent threshold*(today) and the predicted
    trough, via backward induction on the posterior predictive + survival."""
    fallback = cfg["decision"]["substitute_price"] + cfg["decision"]["stockout_penalty"]
    deadline = cfg["decision"]["deadline"]
    today, deadline = parse_date(today), parse_date(deadline)
    if deadline <= today:
        return fallback, None, None, fallback  # no time left: only the fallback

    future = list(daterange(today.fromordinal(today.toordinal() + 1), deadline))
    draws = forecaster.sample_low_paths(future, cfg["decision"]["mc_paths"])

    # W_s = expected optimal cost-to-go given available at the start of day s.
    W = float(np.mean(np.minimum(draws[:, -1], fallback)))   # deadline = last shot
    for j in range(len(future) - 2, -1, -1):
        h = hazard.daily_hazard(future[j])               # overnight stockout prob
        cont = (1 - h) * W + h * fallback
        W = float(np.mean(np.minimum(draws[:, j], cont)))

    h_today = hazard.daily_hazard(today)
    threshold = (1 - h_today) * W + h_today * fallback

    means = np.array([forecaster.mean_low(d) for d in future])
    k = int(np.argmin(means))
    return threshold, future[k], float(means[k]), fallback


def decide(cfg, sku, market_state, forecaster, hazard, today):
    dc = cfg["decision"]
    L_t = market_state.get("best_buyable_net")           # best buyable now
    threshold, trough_date, trough_price, fallback = _backward_threshold(
        cfg, sku, forecaster, hazard, today)

    # Hard override (owner rule): bypasses the model entirely.
    if L_t is not None and L_t <= dc["hard_buy_trigger"]:
        verdict, reason = "BUY", (
            f"Hard trigger: a credible return-safe listing is at "
            f"${L_t:,.0f} <= ${dc['hard_buy_trigger']:,.0f}. Buy now.")
        return _result(cfg, sku, today, verdict, L_t, threshold, fallback,
                       trough_date, trough_price, forecaster, hazard,
                       reason, hard=True)

    if L_t is None:
        verdict, reason = "WAIT", (
            "Nothing buyable today (no in-stock, credible, gate-passing listing). "
            "Holding for the next quote.")
        return _result(cfg, sku, today, verdict, L_t, threshold, fallback,
                       trough_date, trough_price, forecaster, hazard, reason)

    band = threshold * dc["watch_band_pct"] / 100.0
    if L_t <= threshold:
        verdict = "BUY"
        reason = (f"Best legit price ${L_t:,.0f} is at/below the continuation "
                  f"threshold ${threshold:,.0f}: waiting is not expected to beat "
                  f"buying now once stockout risk is priced in.")
    elif L_t <= threshold + band:
        verdict = "WATCH"
        reason = (f"Best legit price ${L_t:,.0f} is just above the threshold "
                  f"${threshold:,.0f} (within {dc['watch_band_pct']:.0f}%). On the "
                  f"edge — watch closely.")
    else:
        verdict = "WAIT"
        reason = (f"Best legit price ${L_t:,.0f} is above the threshold "
                  f"${threshold:,.0f}: the model expects a better achievable price "
                  f"before stockout risk erodes the gain.")
    return _result(cfg, sku, today, verdict, L_t, threshold, fallback,
                   trough_date, trough_price, forecaster, hazard, reason)


def _result(cfg, sku, today, verdict, L_t, threshold, fallback, trough_date,
            trough_price, forecaster, hazard, reason, hard=False):
    deadline = cfg["decision"]["deadline"]
    p_avail_deadline = hazard.p_available(deadline)
    p_stockout_trough = (1 - hazard.p_available(trough_date)) if trough_date else None

    # Dominant uncertainty driver: dollar impact of price spread vs. of losing
    # the buying option before the deadline.
    price_impact = forecaster.sigma_frac * (trough_price or forecaster.mean_low(today))
    stockout_impact = (1 - p_avail_deadline) * max(0.0, fallback - threshold)
    driver = ("stockout risk" if stockout_impact >= price_impact else "price uncertainty")

    savings = None if L_t is None else round(L_t - threshold, 2)  # >0 => waiting cheaper
    return {
        "sku_key": sku["sku_key"],
        "date": str(today),
        "verdict": verdict,
        "hard_override": hard,
        "best_legit_now": L_t,
        "threshold": round(threshold, 2),
        "expected_savings_wait": savings,
        "fallback_cost": round(fallback, 2),
        "deadline": deadline,
        "p_available_deadline": round(float(p_avail_deadline), 4),
        "trough_date": str(trough_date) if trough_date else None,
        "trough_price": round(trough_price, 2) if trough_price else None,
        "p_stockout_before_trough": (round(float(p_stockout_trough), 4)
                                     if p_stockout_trough is not None else None),
        "prior_weight": round(1 - forecaster.w_data, 4),
        "data_weight": round(forecaster.w_data, 4),
        "dominant_driver": driver,
        "rationale": reason,
    }

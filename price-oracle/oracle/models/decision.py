"""Decision layer — finite-horizon optimal stopping (spec §6.5).

Combines V1 (best legit price now), V2 (posterior-predictive future lows), and
V3 (survival) with the owner's utility into a daily threshold policy:

  * Hard override: any credible, return-safe listing at net <= hard_buy_trigger
    emits BUY immediately, bypassing the model (owner rule).
  * Otherwise BUY iff L_t <= threshold*(t), where threshold*(t) is the
    continuation certainty-equivalent — the expected cost of waiting optimally,
    integrating future price AND the probability the chance to buy is lost.

threshold*(t) is computed by backward induction over the decision horizon. With
no calendar deadline (the owner's stance: "getting a deal is top priority, stick
to the 83\" until it goes out of stock"), the horizon is bounded by the rising
stockout hazard rather than a date — we integrate to the end of the forecast
horizon and let survival S(t) discount the far tail to ~0.

Failing to secure the 83\" before it is gone forces the 77\" fallback, costing
substitute_price + K. The substitute price tracks the 77\"'s live best price when
available (it is the real consolation cost), falling back to the configured
anchor otherwise. The verdict is explainable: it carries the numeric threshold,
the expected savings from waiting, the stockout probability before the next
forecast trough, and the single dominant uncertainty driver.
"""
import datetime as dt

import numpy as np

from ..config import parse_date
from .forecast import daterange


def _horizon_end(cfg, today):
    """The decision horizon: an explicit deadline if configured, else a
    stockout-bounded window (forecast horizon) the survival curve discounts."""
    deadline = cfg["decision"].get("deadline")
    if deadline:
        return parse_date(deadline), True
    return parse_date(today) + dt.timedelta(days=cfg["forecast"]["horizon_days"]), False


def _backward_threshold(cfg, forecaster, hazard, today, fallback, end):
    """Continuation certainty-equivalent threshold*(today) and the predicted
    trough, via backward induction on the posterior predictive + survival."""
    today = parse_date(today)
    if end <= today:
        return fallback, None, None

    future = list(daterange(today + dt.timedelta(days=1), end))
    draws = forecaster.sample_low_paths(future, cfg["decision"]["mc_paths"])

    # W_s = expected optimal cost-to-go given available at the start of day s.
    W = float(np.mean(np.minimum(draws[:, -1], fallback)))   # horizon end = last shot
    for j in range(len(future) - 2, -1, -1):
        h = hazard.daily_hazard(future[j])               # overnight stockout prob
        cont = (1 - h) * W + h * fallback
        W = float(np.mean(np.minimum(draws[:, j], cont)))

    h_today = hazard.daily_hazard(today)
    threshold = (1 - h_today) * W + h_today * fallback

    means = np.array([forecaster.mean_low(d) for d in future])
    k = int(np.argmin(means))
    return threshold, future[k], float(means[k])


def _substitute_price(cfg, substitute_state):
    """Cost of the 77\" consolation. The resolved fallback (spec §11.2) is a new
    77\" at ~$2,998, so track its live lowest legit *new* price when available
    (more stable than a one-off open-box listing), else the configured anchor."""
    anchor = cfg["decision"]["substitute_price"]
    if substitute_state:
        live = substitute_state.get("lowest_legit_new")
        if live:
            return float(live), True
    return anchor, False


def decide(cfg, sku, market_state, forecaster, hazard, today, substitute_state=None):
    dc = cfg["decision"]
    L_t = market_state.get("best_buyable_net")           # best buyable now
    sub_price, sub_live = _substitute_price(cfg, substitute_state)
    fallback = sub_price + dc["stockout_penalty"]
    end, is_deadline = _horizon_end(cfg, today)
    threshold, trough_date, trough_price = _backward_threshold(
        cfg, forecaster, hazard, today, fallback, end)

    ctx = dict(forecaster=forecaster, hazard=hazard, fallback=fallback,
               trough_date=trough_date, trough_price=trough_price, end=end,
               is_deadline=is_deadline, sub_price=sub_price, sub_live=sub_live)

    # Hard override (owner rule): bypasses the model entirely.
    if L_t is not None and L_t <= dc["hard_buy_trigger"]:
        reason = (f"Hard trigger: a credible return-safe listing is at "
                  f"${L_t:,.0f} <= ${dc['hard_buy_trigger']:,.0f}. Buy now.")
        return _result(cfg, sku, today, "BUY", L_t, threshold, reason,
                       hard=True, **ctx)

    if L_t is None:
        reason = ("Nothing buyable today (no in-stock, credible, gate-passing "
                  "listing). Holding for the next quote.")
        return _result(cfg, sku, today, "WAIT", L_t, threshold, reason, **ctx)

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
    return _result(cfg, sku, today, verdict, L_t, threshold, reason, **ctx)


def _result(cfg, sku, today, verdict, L_t, threshold, reason, *, forecaster,
            hazard, fallback, trough_date, trough_price, end, is_deadline,
            sub_price, sub_live, hard=False):
    p_avail_end = hazard.p_available(end)
    p_stockout_trough = (1 - hazard.p_available(trough_date)) if trough_date else None

    # Dominant uncertainty driver: dollar impact of price spread vs. of losing
    # the buying option before the horizon end.
    price_impact = forecaster.sigma_frac * (trough_price or forecaster.mean_low(today))
    stockout_impact = (1 - p_avail_end) * max(0.0, fallback - threshold)
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
        "substitute_price": round(sub_price, 2),
        "substitute_is_live": sub_live,
        "deadline": cfg["decision"].get("deadline"),
        "horizon_end": str(end),
        "horizon_is_deadline": is_deadline,
        "p_available_horizon": round(float(p_avail_end), 4),
        "trough_date": str(trough_date) if trough_date else None,
        "trough_price": round(trough_price, 2) if trough_price else None,
        "p_stockout_before_trough": (round(float(p_stockout_trough), 4)
                                     if p_stockout_trough is not None else None),
        "prior_weight": round(1 - forecaster.w_data, 4),
        "data_weight": round(forecaster.w_data, 4),
        "dominant_driver": driver,
        "rationale": reason,
    }

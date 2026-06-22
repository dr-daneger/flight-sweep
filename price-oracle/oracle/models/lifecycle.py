"""V2a — reference-class lifecycle prior (spec §6.2, §2.3).

A single SKU's terminal phase is short and noisy, so we borrow strength from the
terminal-decay curves of prior-generation analogs aligned on lifecycle time. For
each analog j we fit

    M(tau) = floor + premium * exp(-tau / lambda)        (tau = days since launch)

then pool the analogs (as fractions of launch MSRP, so they transfer to the
target SKU) into a population-level prior with between-analog uncertainty. Live
data later shifts the posterior (forecast.py); this module only builds the prior.

The analog observation points come from reference_class.yaml seed parameters
today; point `_analog_points` at real Keepa / archived series to upgrade it
(spec §4.3, §11.5) without touching the fit or the pooling.
"""
import hashlib

import numpy as np
from scipy.optimize import curve_fit


def _seed(name):
    """Stable per-analog seed (builtin hash() is randomized per process, which
    would make the prior — and the whole forecast — non-reproducible)."""
    return int(hashlib.md5(name.encode()).hexdigest()[:8], 16)


def _decay(tau, floor, premium, lam):
    return floor + premium * np.exp(-tau / lam)


def _analog_points(analog, horizon_days=1000, step=21, seed=0):
    """Seed monthly street-price observations for one analog from its encoded
    decay parameters (stand-in for a real price history). The window spans the
    full decay so the asymptotic floor is identifiable — prior-gen analogs are
    old enough to have reached it, which is precisely why they can supply the
    floor the still-decaying target SKU has not yet revealed."""
    rng = np.random.default_rng(seed)
    tau = np.arange(0, horizon_days, step, dtype=float)
    truth = _decay(tau, analog["floor"], analog["premium"], analog["lambda_days"])
    noise = rng.normal(0, analog.get("noise_pct", 5) / 100.0, size=len(tau))
    return tau, truth * (1 + noise)


def _fit_one(analog):
    tau, y = _analog_points(analog, seed=_seed(analog["name"]))
    p0 = [analog["floor"], analog["premium"], analog["lambda_days"]]
    try:
        popt, _ = curve_fit(_decay, tau, y, p0=p0, maxfev=20000,
                            bounds=([0, 0, 30], [np.inf, np.inf, 3000]))
    except (RuntimeError, ValueError):
        popt = p0
    floor, premium, lam = popt
    return {"floor": floor, "premium": premium, "lam": lam,
            "msrp": analog["launch_msrp"]}


def build_prior(cfg, sku):
    """Pooled reference-class prior for `sku`, scaled to its launch MSRP."""
    group = cfg["reference_class"][sku["reference_class"]]
    fits = [_fit_one(a) for a in group["analogs"]]

    # Pool as fractions of each analog's MSRP so they transfer across price tiers.
    floor_frac = np.array([f["floor"] / f["msrp"] for f in fits])
    prem_frac = np.array([f["premium"] / f["msrp"] for f in fits])
    lams = np.array([f["lam"] for f in fits])

    msrp = sku["launch_msrp"]
    pop = {
        "floor": float(np.mean(floor_frac) * msrp),
        "premium": float(np.mean(prem_frac) * msrp),
        "lam": float(np.mean(lams)),
        # between-analog spread -> a relative uncertainty band on the prior curve
        "floor_sd": float(np.std(floor_frac) * msrp),
        "lam_sd": float(np.std(lams)),
    }
    # Prior uncertainty as a % of price: combine floor spread + a noise floor.
    band_pct = max(6.0, 100.0 * pop["floor_sd"] / max(pop["floor"], 1.0))

    def curve(tau):
        return _decay(np.asarray(tau, float), pop["floor"], pop["premium"], pop["lam"])

    return {
        "group": sku["reference_class"],
        "n_analogs": len(fits),
        "pop": pop,
        "band_pct": band_pct,
        "curve": curve,
        "analog_fits": fits,
    }

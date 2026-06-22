"""V2b — live forecaster, fallback path (spec §6.3).

This is the pragmatic forecaster that unblocks the decision layer (Phase 2) while
still honoring the parts that are the whole point of "improves over time / based
on prior cycles":

  * reference-class prior gives the terminal-decay SHAPE (lifecycle.py);
  * live S95F observations correct the LEVEL (and, once dense, the shape);
  * the prior's weight shrinks as live data accrue — w_data = n / (n + halflife)
    — so early forecasts are non-useless and later ones are data-driven;
  * future sale/successor events pull the mean down on their dates, yielding
    event-conditional troughs;
  * Student-t noise makes the posterior predictive robust to scrape outliers;
  * stockout-adjacent lows are treated as left-censored (the true clearing price
    was <= last seen), widening the achievable-low tail (spec §2.1).

The interface (a `Forecaster` exposing posterior-predictive draws + quantiles of
the achievable low L_t) is exactly what the optimal-stopping policy consumes, so
swapping in the PyMC structural model (Phase 2.5) is a drop-in replacement.
"""
import datetime as dt

import numpy as np
from scipy.optimize import curve_fit

from ..config import parse_date, active_events


def _decay(tau, floor, premium, lam):
    return floor + premium * np.exp(-tau / lam)


def _tau(launch_date, d):
    return (parse_date(d) - parse_date(launch_date)).days


class Forecaster:
    def __init__(self, cfg, sku, prior, history, today):
        self.cfg = cfg
        self.sku = sku
        self.prior = prior
        self.today = parse_date(today)
        self.launch = sku["launch_date"]
        self.q = cfg["forecast"]["quantiles"]
        self.df = cfg["forecast"]["student_t_df"]

        # Live observations: list of dicts {date, M, L, censored}
        self.hist = [h for h in history if h.get("M") is not None]
        self.n = len(self.hist)
        hl = cfg["forecast"]["prior_halflife_obs"]
        self.w_data = self.n / (self.n + hl) if self.n else 0.0

        self._fit_level()
        self._fit_depth()

    # -- mean curve -----------------------------------------------------------
    def _data_curve(self):
        """A curve from live data alone: a fitted decay when dense, else the
        prior shape shifted by the mean live residual (a level correction)."""
        prior = self.prior["curve"]
        if self.n >= 8:
            taus = np.array([_tau(self.launch, h["date"]) for h in self.hist], float)
            ys = np.array([h["M"] for h in self.hist], float)
            p = self.prior["pop"]
            try:
                popt, _ = curve_fit(
                    _decay, taus, ys, p0=[p["floor"], p["premium"], p["lam"]],
                    maxfev=20000, bounds=([0, 0, 30], [np.inf, np.inf, 3000]))
                return lambda tau: _decay(np.asarray(tau, float), *popt)
            except (RuntimeError, ValueError):
                pass
        if self.n:
            resid = np.mean([h["M"] - float(prior(_tau(self.launch, h["date"])))
                             for h in self.hist])
        else:
            resid = 0.0
        return lambda tau: prior(tau) + resid

    def _fit_level(self):
        prior = self.prior["curve"]
        data_curve = self._data_curve()
        w = self.w_data

        def mean_M(d):
            tau = _tau(self.launch, d)
            return float((1 - w) * prior(tau) + w * data_curve(tau))
        self.mean_M = mean_M

        # Relative noise: blend prior band with live residual scatter.
        prior_frac = self.prior["band_pct"] / 100.0
        if self.n >= 3:
            res = np.array([(h["M"] - mean_M(h["date"])) / max(h["M"], 1.0)
                            for h in self.hist])
            data_frac = float(np.std(res)) or prior_frac
        else:
            data_frac = prior_frac
        # Per-step (today) relative scale, floored so a smooth seed series does
        # not collapse the bands and capped so a misspecified prior cannot blow
        # them up. Horizon diffusion is applied at sample time.
        self.sigma_frac = float(np.clip(
            np.sqrt((1 - w) * prior_frac**2 + w * data_frac**2), 0.04, 0.20))

    # -- deal depth (M - L), with censoring widening the low tail -------------
    def _fit_depth(self):
        depths, infl = [], 1.0
        for h in self.hist:
            if h.get("L") is None:
                continue
            d = max(0.0, h["M"] - h["L"])
            depths.append(d)
            if h.get("censored"):
                infl = 1.4   # stockout-adjacent low => true clearing was lower
        if depths:
            self.depth_mean = float(np.mean(depths))
            self.depth_sd = float(np.std(depths)) * infl + 0.01 * self.sku["launch_msrp"]
        else:
            # No paired L yet: assume a modest typical discount off street.
            self.depth_mean = 0.04 * self.sku["launch_msrp"]
            self.depth_sd = 0.03 * self.sku["launch_msrp"]

    # -- event-conditional mean ----------------------------------------------
    def _event_mult(self, d):
        pull = sum(e.get("pull_pct", 0)
                   for e in active_events(self.cfg, d, self.sku["sku_key"]))
        return 1 + pull / 100.0

    def mean_low(self, d):
        """Expected achievable low on date d (event-conditional)."""
        return self.mean_M(d) * self._event_mult(d) - self.depth_mean

    # -- posterior-predictive draws of the achievable low ---------------------
    def sample_low_paths(self, dates, n, rng=None):
        """Posterior-predictive draws of the achievable low per date. Forecast
        uncertainty grows with the horizon (random-walk-style diffusion): the
        relative scale widens as sqrt(1 + days_ahead / 30)."""
        rng = rng or np.random.default_rng(12345)
        floor_clamp = 0.65 * self.prior["pop"]["floor"]
        hi_clamp = 1.4 * self.sku["launch_msrp"]       # tame heavy Student-t tails
        unit_t = np.sqrt(self.df / (self.df - 2))      # standard_t -> unit variance
        out = np.empty((n, len(dates)), float)
        for j, d in enumerate(dates):
            ahead = max(0, (parse_date(d) - self.today).days)
            scale = self.sigma_frac * np.sqrt(1 + ahead / 30.0)
            m = self.mean_M(d) * self._event_mult(d)
            m_draw = m * (1 + scale * rng.standard_t(self.df, size=n) / unit_t)
            depth = np.clip(rng.normal(self.depth_mean, self.depth_sd * (1 + ahead / 90.0),
                                       size=n), 0, None)
            out[:, j] = np.clip(m_draw - depth, floor_clamp, hi_clamp)
        return out

    # -- quantile table for storage / reporting -------------------------------
    def quantiles(self, dates, n=4000):
        paths = self.sample_low_paths(dates, n)
        rows = []
        for j, d in enumerate(dates):
            row = {"date": str(d), "horizon_days": (parse_date(d) - self.today).days,
                   "mean_low": float(np.mean(paths[:, j])),
                   "mean_street": self.mean_M(d) * self._event_mult(d)}
            for ql in self.q:
                row[f"low_q{int(ql*100):02d}"] = float(np.quantile(paths[:, j], ql))
            rows.append(row)
        return rows


def daterange(start, end, step_days=1):
    start, end = parse_date(start), parse_date(end)
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=step_days)


def build(cfg, sku, prior, history, today):
    return Forecaster(cfg, sku, prior, history, today)

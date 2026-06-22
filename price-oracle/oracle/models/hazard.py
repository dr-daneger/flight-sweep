"""V3 — stockout / EOL hazard (spec §6.4).

SKU availability is a survival process. The dominant covariate is proximity to
the successor milestone: a terminal low-volume SKU's permanent-EOL hazard rises
sharply once the successor ships. We model a discrete-time daily hazard h(t) with
a baseline that jumps and then ramps after the successor-ship event, nudged up by
the live rate of full-market stockouts, and integrate it into a survival curve
S(t) = P(still buyable at t). This is the term that stops the policy from naively
"waiting for Black Friday" when the SKU will likely vanish first.
"""
import numpy as np

from ..config import parse_date


class HazardModel:
    def __init__(self, cfg, sku, history, today):
        h = cfg["hazard"]
        self.h0 = h["baseline_daily_hazard"]
        self.mult = h["successor_hazard_mult"]
        self.today = parse_date(today)
        self.ship = next((parse_date(e["start_date"]) for e in cfg["events"]
                          if e["type"] == "successor_ship"), None)
        # Live signal: recent share of days with no source in stock at all.
        recent = [bool(x.get("in_stock_any")) for x in history[-30:]
                  if x.get("in_stock_any") is not None]
        oos_rate = (1 - np.mean(recent)) if recent else 0.0
        self.live_mult = 1.0 + 3.0 * float(oos_rate)        # capped softly below

    def daily_hazard(self, d):
        d = parse_date(d)
        mult = 1.0
        if self.ship and d >= self.ship:
            # Ramp to the full successor multiplier over ~6 months post-ship.
            frac = min(1.0, (d - self.ship).days / 180.0)
            mult = 1.0 + (self.mult - 1.0) * frac
        return min(0.5, self.h0 * mult * self.live_mult)

    def survival(self, dates):
        """S(t) for each date, P(available), starting from today=1.0."""
        s, prev, out = 1.0, self.today, []
        for d in dates:
            d = parse_date(d)
            for _ in range((d - prev).days):
                s *= (1 - self.daily_hazard(d))
            prev = d
            out.append(s)
        return out

    def p_available(self, date):
        return self.survival([date])[-1]

"""Shared fetch-time hard-constraint filters from config.yaml: departure-time
windows and per-leg stop limits. Both follow the same epistemic rule: drop
offers that verifiably violate the constraint, keep unknowns (sources differ
in coverage; the dashboard marks unverified rows)."""


def in_window(dep_time, win):
    """True = verified inside window; False = violates; None = time unknown."""
    if not win:
        return True
    t = (dep_time or "")[11:16]
    if not t:
        return None
    if win.get("dep_after") and t < win["dep_after"]:
        return False
    if win.get("dep_before") and t > win["dep_before"]:
        return False
    return True


def filter_offers(offers, win):
    """Drop offers that verifiably violate the window. Unknown-time offers are
    kept (dashboard marks them) — sources differ in time coverage."""
    return [o for o in offers if in_window(o.get("dep_time"), win) is not False]


def stops_ok(stops, max_stops):
    """True = verified within limit; False = violates; None = stops unknown."""
    if max_stops is None:
        return True
    if stops is None:
        return None
    return stops <= max_stops


def filter_stops(offers, max_stops):
    """Drop offers with verifiably too many stops; keep unknown-stops offers."""
    return [o for o in offers if stops_ok(o.get("stops"), max_stops) is not False]

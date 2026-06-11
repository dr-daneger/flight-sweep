"""Shared departure-time window logic (hard constraints from config.yaml)."""


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

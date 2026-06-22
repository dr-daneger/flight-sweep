"""Smoke + correctness tests for the price-oracle pipeline.

Run from price-oracle/:  python -m tests.test_pipeline
No pytest dependency — plain asserts so it runs anywhere the engine runs.
"""
from pathlib import Path

from oracle import config, normalize
from oracle.fetch import mock
from oracle.fetch.base import make_obs, now_utc
from oracle.models import decision, forecast, hazard, lifecycle, value


def _cfg():
    return config.load()


def test_net_price_and_bundle():
    """Gift cards are treated as near-cash off the list price (spec §6.1)."""
    t = now_utc()
    raw = [make_obs("s", "mock_bestbuy", t, raw_price=5000, condition_text="new",
                    in_stock=True, bundle_text="$300 gift card with purchase")]
    o = normalize.normalize(raw)[0]
    assert o["bundle_value"] == 300.0
    assert o["net_effective_price"] == 4700.0
    assert o["authorization"] == "authorized"


def test_credibility_rejects_uncorroborated_low(c):
    t = now_utc()
    raw = [make_obs("s", s, t, raw_price=p, condition_text="new", in_stock=True)
           for s, p in [("mock_bestbuy", 4950), ("mock_samsung", 5010),
                        ("mock_crutchfield", 5080), ("mock_unknown", 2100)]]
    offers = value.annotate_credibility(normalize.normalize(raw), c)
    flags = {o["source_id"]: o["credibility_flag"] for o in offers}
    assert flags["mock_unknown"] == "suspect_outlier"
    buy = value.buyable(offers, c["skus_by_key"]["samsung_s95f_83"], c)
    assert min(b["net_effective_price"] for b in buy) == 4950.0   # scam excluded


def test_secondary_gates(c):
    """Used listing without returns is logged but not BUY-eligible (spec §4.4)."""
    sku = c["skus_by_key"]["samsung_s95f_83"]
    base = {"condition": "used", "authorization": "marketplace_3p",
            "in_stock": True, "credibility_flag": "ok", "net_effective_price": 3500,
            "seller_rating": 0.99, "seller_volume": 200, "returns_ok": True}
    assert value.secondary_eligible(base, c) is True
    assert value.secondary_eligible({**base, "returns_ok": False}, c) is False
    assert value.secondary_eligible({**base, "seller_rating": 0.90}, c) is False


def test_prior_floor_identified(c):
    """Reference-class prior recovers a sane floor/lambda (the analog-fit bug)."""
    p = lifecycle.build_prior(c, c["skus_by_key"]["samsung_s95f_83"])
    assert 2300 < p["pop"]["floor"] < 4200
    assert 400 < p["pop"]["lam"] < 1300
    # reproducible across calls (no process-randomized seeding)
    assert lifecycle.build_prior(c, c["skus_by_key"]["samsung_s95f_83"])["pop"]["floor"] \
        == p["pop"]["floor"]


def test_prior_weight_shrinks(c):
    """Data weight rises (prior weight shrinks) as observations accrue (spec §6.3)."""
    sku = c["skus_by_key"]["samsung_s95f_83"]
    prior = lifecycle.build_prior(c, sku)
    few = forecast.build(c, sku, prior,
                         [{"date": "2025-06-01", "M": 5800, "L": 5800, "censored": False}],
                         "2026-06-22")
    many = forecast.build(c, sku, prior,
                          [{"date": f"2025-{m:02d}-01", "M": 5800 - m * 60,
                            "L": 5700 - m * 60, "censored": False}
                           for m in range(1, 12)] * 6, "2026-06-22")
    assert many.w_data > few.w_data


def test_forecast_quantiles_ordered(c):
    sku = c["skus_by_key"]["samsung_s95f_83"]
    prior = lifecycle.build_prior(c, sku)
    fc = forecast.build(c, sku, prior, [], "2026-06-22")
    row = fc.quantiles(["2026-08-01"])[0]
    assert row["low_q05"] <= row["low_q50"] <= row["low_q95"]


def test_hazard_rises_after_successor(c):
    sku = c["skus_by_key"]["samsung_s95f_83"]
    hz = hazard.HazardModel(c, sku, [], "2026-06-22")
    assert hz.daily_hazard("2026-03-01") < hz.daily_hazard("2026-09-01")
    s_now = hz.p_available("2026-07-01")
    s_later = hz.p_available("2026-12-01")
    assert 0 <= s_later <= s_now <= 1


def test_hard_override(c):
    sku = c["skus_by_key"]["samsung_s95f_83"]
    prior = lifecycle.build_prior(c, sku)
    fc = forecast.build(c, sku, prior, [], "2026-06-22")
    hz = hazard.HazardModel(c, sku, [], "2026-06-22")
    ms = {"best_buyable_net": 2950.0, "best_buyable_condition": "open_box",
          "best_buyable_source": "bb", "robust_street": 4950.0,
          "lowest_legit_new": 4960.0, "deal_depth": 10.0, "in_stock_any": True}
    d = decision.decide(c, sku, ms, fc, hz, "2026-06-22")
    assert d["verdict"] == "BUY" and d["hard_override"]


def test_decision_threshold_bounded(c):
    """Threshold sits at or below the fallback and at/above the price floor."""
    sku = c["skus_by_key"]["samsung_s95f_83"]
    prior = lifecycle.build_prior(c, sku)
    fc = forecast.build(c, sku, prior, [], "2026-06-22")
    hz = hazard.HazardModel(c, sku, [], "2026-06-22")
    ms = {"best_buyable_net": 4500.0, "robust_street": 4950.0,
          "lowest_legit_new": 4960.0, "deal_depth": 50.0, "in_stock_any": True}
    d = decision.decide(c, sku, ms, fc, hz, "2026-06-22")
    fallback = c["decision"]["substitute_price"] + c["decision"]["stockout_penalty"]
    assert 0.6 * prior["pop"]["floor"] <= d["threshold"] <= fallback + 1


def test_end_to_end_run():
    """Full fetch->...->report run on mock sources produces a verdict + dashboard."""
    from oracle import run as run_mod
    d = run_mod.run(mode="mock", on_date="2026-06-22", do_seed=False)
    assert d["verdict"] in ("BUY", "WAIT", "WATCH")
    assert d["threshold"] > 0
    assert (Path(__file__).resolve().parent.parent / "docs" / "index.html").exists()


def main():
    c = _cfg()
    passed = 0
    tests = [
        ("net price + bundle", lambda: test_net_price_and_bundle()),
        ("credibility filter", lambda: test_credibility_rejects_uncorroborated_low(c)),
        ("secondary gates", lambda: test_secondary_gates(c)),
        ("prior floor identified", lambda: test_prior_floor_identified(c)),
        ("prior weight shrinks", lambda: test_prior_weight_shrinks(c)),
        ("forecast quantiles ordered", lambda: test_forecast_quantiles_ordered(c)),
        ("hazard rises post-successor", lambda: test_hazard_rises_after_successor(c)),
        ("hard override", lambda: test_hard_override(c)),
        ("threshold bounded", lambda: test_decision_threshold_bounded(c)),
        ("end-to-end run", lambda: test_end_to_end_run()),
    ]
    for name, fn in tests:
        fn()
        print(f"  ok  {name}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")


if __name__ == "__main__":
    main()

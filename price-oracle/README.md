# price-oracle

> **STATUS: FROZEN (2026-06-29).** The daily job is disabled and this subproject is
> parked. The optimal-stopping engine works; the *data acquisition* is what didn't.
> The buyable, decision-relevant signal (sub-$3k open-box/used) sits behind bot
> walls and API gates, while the easily-scraped first-party "new" price is redundant
> (every dealer clusters within a few dollars). `flight-sweep` (repo root) is
> unaffected.
>
> **Lessons for any price-scraping / tracking project — the durable takeaway:**
> - Bot walls block on the **client TLS/JS fingerprint, not the IP** — a residential
>   IP doesn't help. Escalation ladder: `requests` → `curl_cffi` (Chrome
>   impersonation; beats fingerprint-only blocks, e.g. Abt) → headless + stealth
>   (for Akamai JS-sensor gates, e.g. Best Buy) → official API → scraping-as-a-service.
> - **Official APIs carry their own friction:** eBay's production keyset is gated
>   behind an account-deletion exemption and won't grant sold-price history; Best
>   Buy's API needs a company-domain email; Keepa's API is paywalled.
> - **Survivorship bias is fundamental:** the cheapest prices sell out before a daily
>   poll sees them. Prefer **event/alert streams** (Slickdeals search RSS, Keepa
>   drop-alerts, eBay saved-search) over polling for trough detection.
> - For a single SKU, free alerts + the occasional manual check usually beat a
>   custom engine on ROI. Build it for the learning, with a thin spine of
>   *cooperative* sources rather than "scrape everything."
>
> To revive: `schtasks /Change /TN buy-signal-daily /ENABLE`.

A terminal-clearance **price tracker and stopping-policy engine**. It scrapes a
SKU's price on a schedule and publishes a daily, defensible **BUY / WAIT / WATCH**
verdict to a self-contained dashboard — framed not as "a price chart" but as a
*finite-horizon optimal-stopping problem with a deadline and an absorbing
stockout state.*

The seed target is the **Samsung 83" S95F** (`QN83S95FAEXZA`), a Tandem WOLED
panel in active end-of-life clearance after the S95H successor shipped. The
engine is generic, though: add SKUs in `config.yaml`. It is the second project in
this repo alongside `flight-sweep`, sharing its ethos — runs entirely in GitHub
Actions, no server, a single self-contained HTML dashboard, and graceful
degradation when a source fails.

## The decision, formally

Given the best legitimate net price available now (**V₁**), the forecast
distribution of future achievable prices (**V₂**), and the probability the SKU
disappears before any future trough (**V₃**), should I buy today? The decision
layer combines these with the owner's utility into a daily threshold policy.

| Estimand | What it is | Module |
|---|---|---|
| **V₁** current value | net-of-bundle/condition/auth best legit price + robust street price, with a credibility filter | `models/value.py` |
| **V₂a** lifecycle prior | hierarchical terminal-decay curve fit over prior-gen analogs (S95D, …) | `models/lifecycle.py` |
| **V₂b** forecaster | prior shape + live level, event regression, Student-t noise, censoring; posterior-predictive of the achievable low | `models/forecast.py` |
| **V₃** stockout hazard | discrete-time survival S(t), rising sharply after the successor ships | `models/hazard.py` |
| decision | hard ≤$3k override, else BUY iff price ≤ continuation certainty-equivalent (backward induction) | `models/decision.py` |

**Why V₃ dominates and latency is the binding constraint.** A genuinely good
price sells out — the cheapest transactable prices are deleted before a daily
sampler sees them (survivorship / left-censoring). So every day the scraper is
not running is unrecoverable trajectory data near the trough. The build
prioritizes getting clean observations flowing over modeling polish, and the
forecaster treats a stockout right after a low as a left-censored signal that the
true clearing price was ≤ the last seen price.

## Usage

```bash
cd price-oracle
pip install -r requirements.txt

# Offline, no keys: synthetic-but-realistic observations through the full pipeline.
python -m oracle.run --source mock

# First run with no history? Backfill a synthetic trajectory so the forecaster
# and hazard have something to fit (one-time; replace with Keepa for real data).
python -m oracle.run --source mock --seed-history

# Real sources (declare product URLs under `live_sources` in config.yaml first):
python -m oracle.run --source live

# Rebuild the dashboard from stored data without fetching:
python -m oracle.run --render-only

# Weekly walk-forward calibration:
python -m oracle.backtest

# Tests (no pytest needed):
python -m tests.test_pipeline
```

Each run writes the dashboard to `docs/index.html` (open it directly — no
server, no CDN), a Markdown summary to `docs/report.md`, and append-only Parquet
to `data/`.

## Outputs

- **`docs/index.html`** — verdict + rationale on top; best legit net price by
  source; deal depth vs typical; the forecast fan chart with credible bands and
  the next event-conditional trough; the survival / stockout-risk curve; the
  calibration scorecard. All charts are inline SVG.
- **`docs/report.md`** — the same, committed in Markdown.
- **Alerting** — `docs/alert.json` is turned into a GitHub Issue by the workflow
  (the in-repo, free default channel); set `ALERT_WEBHOOK_URL` to also POST to
  Slack/Discord/ntfy. Fires on a BUY verdict, a threshold crossing, or a
  data-quality anomaly.

## Data sources (tiered, graceful degradation)

Per source the fetcher attempts, in order: official API → JSON-LD
`schema.org/Product`+`Offer` → CSS → headless (Playwright). It records which
tier succeeded. Coverage is **intentionally partial** — a source failing on a day
is logged, not fatal; the models forecast a trend from whatever subset responded.
The bundled `mock` source keeps the pipeline green offline and is what the demo
and tests use. To wire real retailers, add to `config.yaml`:

```yaml
live_sources:
  - {sku_key: samsung_s95f_83, source_id: bestbuy, tier: jsonld, url: "https://www.bestbuy.com/site/.../<id>.p"}
```

Secondary-market listings (open-box, returnable used) are tracked under hard
**return-eligibility + seller-trust gates** (`secondary_gates` in config); they
feed the ≤$3k hard trigger and the dashboard but never auto-qualify for a BUY
without clearing the gates. Scraping is fragile and ToS-gray — prefer APIs,
respect `robots.txt`, rate-limit, identify politely.

## Storage

DuckDB over date-partitioned Parquet under `data/` (`store.py` is the adapter
seam — swapping to an R2/S3 bucket is a config change). Three append-only,
immutable layers — `raw_observations` (verbatim), `offers` (normalized net
price), and the model-output tables (`market_state_daily`, `forecasts`,
`hazard_curve`, `decisions`, `predictions_log`) — so the whole pipeline is
reproducible and backtestable. The daily workflow commits `data/` and `docs/` to
this branch.

## Configuration & the decisions baked in

Everything tunable lives in `config.yaml`, `events.yaml`, and
`reference_class.yaml` — no policy is hard-coded. The spec's open decisions were
resolved with these defaults (change freely):

| Decision (spec §11) | Default here | Notes |
|---|---|---|
| Purchase deadline / horizon | **2026-07-15** | the mid-July house move; the single most policy-dominant input |
| Substitute + utility | **77" S95F @ $2,998** | resolved in spec §11.2; the regret anchor |
| Stockout penalty `K` | **$400** | regret of the downgrade; higher ⇒ buy sooner |
| Hard buy trigger | **net ≤ $3,000** | any credible return-safe listing ⇒ immediate BUY |
| Condition scope | new, open-box, **used**, refurb, scratch-dent | returnable used in scope per §11.4 |
| Paid data (Keepa) | **off** | set `KEEPA_API_KEY`, flip `paid_data.keepa` |
| Residential proxy | **off** | set `PROXY_URL`, flip `proxy.enabled` |
| Persistence target | **commit Parquet to branch** | adapter seam left for R2/S3 |
| Alert channel | **GitHub Issue** | + optional webhook |

## "Improves over time", concretely

The reference-class prior supplies the terminal-decay *shape*; live observations
correct the *level* and (once dense) the shape; the prior's weight shrinks as
`w_data = n / (n + halflife)`. The backtest reports this shrinkage alongside
pinball loss and interval coverage, so the claim is auditable rather than
rhetorical. Calibration accrues as logged forecasts mature.

## Layout

```
config.yaml            SKUs, decision policy, gates, storage, alerts
events.yaml            holiday / sale / successor-milestone calendar
reference_class.yaml   prior-gen analog decay params (seed; swap for Keepa)
oracle/run.py          orchestrator: fetch → normalize → store → models → report → alert
oracle/fetch/          tiered sources (jsonld, mock) + registry
oracle/normalize.py    raw → net-effective offers (condition / auth / bundle / dedup)
oracle/models/         value, lifecycle, forecast, hazard, decision
oracle/validate.py     prediction logging, calibration, parser-drift detection
oracle/report.py       self-contained HTML dashboard + Markdown report
oracle/alert.py        GitHub-Issue / webhook alerting
oracle/backtest.py     weekly walk-forward calibration
data/                  append-only Parquet (committed by the workflow)
docs/                  generated dashboard + reports
```

## Limitations (honest)

Scraping is fragile and coverage partial; some sources need paid APIs or get
their Actions IPs blocked. Survivorship bias is fundamental — even 6-hourly
sampling can miss a flash dip; the true floor may only ever be seen as "≤ last
seen, then gone." A single low-volume SKU is sparse and noisy, so the
reference-class prior carries real weight and its quality bounds forecast
quality. This fallback forecaster gives weaker uncertainty quantification than
the planned PyMC structural model (spec Phase 2.5), for which `models/forecast.py`
exposes the drop-in interface (posterior-predictive draws + quantiles). The
verdict is a decision aid, not booking advice — always verify the live listing.

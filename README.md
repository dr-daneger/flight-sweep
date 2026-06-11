# flight-sweep

Automated price tracking + itinerary optimization for the October 2026 Bordeaux
wedding trip, so nobody has to brute-force Google Flights by hand.

## The trip

| Leg | Route | Date(s) | Hard constraint |
|---|---|---|---|
| Outbound | PDX → DFW | Oct 8 or 9 | Oct 8 departs ≥17:00; Oct 9 departs ≤18:00 (Dallas wedding Oct 10) |
| Transatlantic | DFW → **entry city X** | Oct 11 or 12 | the optimizer's variables |
| Road trip | X → ... → Bordeaux | Oct 11/12–24 | one-way rental van, dropped at BOD |
| Home | BOD → PDX | Oct 25 or 26 | Oct 25 departs ≥17:00 (Bordeaux wedding Oct 24–25) |

Departure-time windows are **enforced when fetching** (`dep_after`/`dep_before`
per date in `config.yaml`); out-of-window fares never enter the rankings.
Offers whose source lacks departure-time data are kept but marked "?" on the
dashboard.

5 seats: 2 adults + 3 children (set real ages in `config.yaml` — kids 12+ price
as adults).

## Why this isn't NP-hard

It looks like a traveling-salesman mess, but the van rental collapses it:
flights are only needed at three fixed cut points, and the only free choice is
the Europe entry city (plus ±1 day on two legs). Total cost is *separable* —
`best(PDX→DFW) + best(DFW→X) + best(BOD→PDX) + vanDropFee(X)` — so the global
optimum is a simple minimum over ~12 candidate cities × a few dates ≈ 16 fare
queries per sweep. The hard part isn't the optimization, it's having fresh
fares every day, which is what this tool automates.

Entry cities are pre-filtered for late-October weather (Mediterranean-leaning)
and drivability to Bordeaux in ~12 days; each carries a rough one-way van
drop-fee estimate (France ≈ free, cross-border often €500–900) that folds into
the ranking. Edit `entry_cities` in `config.yaml` to add/remove candidates.

## Usage

```bash
pip install -r requirements.txt

# real prices immediately, no API keys: full sweep via Google Flights scraper
python -m src.sweep

# with free Amadeus keys (developers.amadeus.com), Amadeus becomes primary and
# the scraper automatically backfills any query Amadeus returns empty
export AMADEUS_CLIENT_ID=... AMADEUS_CLIENT_SECRET=...
python -m src.sweep --spot-check 3   # + cross-check top 3 cities against GF

python -m src.sweep --mock           # generated fares, for development
```

Each sweep appends to `data/prices.sqlite` and regenerates
`docs/index.html` — a single self-contained file (open it directly, no
server needed) showing:

- ranked itineraries per entry city (flights + van fee, weather, drive time, route idea)
- **every price is a deep link** opening that exact Google Flights search
  (airports, date, all 5 travelers) for one-click verification/booking
- price-move alerts ≥5% since the previous sweep
- per-leg detail with history sparklines and time-window verification flags
- Google Flights cross-check column where available

## Data sources

Hybrid, in order of trust:

- **Amadeus Self-Service API** (primary when keys exist): free tier ~2k
  calls/month; a daily sweep uses ~16. Test-environment fares can differ
  slightly from retail — treat them as ranking/trend signal.
- **fast-flights** (backup + spot-check): unofficial Google Flights scraper.
  In `--source auto` it backfills any query Amadeus returns empty, runs the
  whole sweep when no keys are set, and powers `--spot-check N`
  (stored separately as `gflights-check` so it never pollutes the ranking
  data). Per query it tries a plain HTTP fetch first, then its own headless
  Chromium (`playwright install chromium`) for routes the lite page won't
  render — smaller airports like BOD/LIS/NCE need this. Every failure
  degrades to "no data for that query" instead of a failed sweep, and the
  optimizer reuses the freshest prior price (flagged *stale* on the
  dashboard) for anything a sweep missed. Google Flights shows the **party
  total**, recorded as-is. Behind a TLS-intercepting proxy, set
  `GFLIGHTS_INSECURE_TLS=1` to let the local browser accept the proxy cert.
- **Always verify the winning fare manually before booking.**

## Daily automation

`.github/workflows/flight-sweep.yml` (repo root) runs a sweep every day at
14:00 UTC and commits the updated database + dashboard. Setup:

1. Repo → Settings → Secrets and variables → Actions: add
   `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`.
2. Scheduled workflows only fire from the **default branch** — merge this to
   `main` to start the daily cadence. Until then, trigger manually via
   Actions → "flight price sweep" → Run workflow.
3. History accrues in git; sparklines get useful after ~5 sweeps.

## Layout

```
config.yaml          trip dates, travelers, entry-city candidates + van fees
src/sweep.py         CLI orchestrator (fetch → store → optimize → render)
src/amadeus_client.py  Amadeus Flight Offers Search wrapper
src/gflights.py      Google Flights search source (backup + spot-check)
src/mock_fares.py    keyless plausible fares for development
src/store.py         SQLite fare-snapshot history
src/optimizer.py     itinerary assembly, ranking, price-move alerts
src/render.py        static dashboard generator
data/prices.sqlite   accumulated price history (committed on purpose)
docs/index.html      generated dashboard
```

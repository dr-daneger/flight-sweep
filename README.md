# flight-sweep

A daily fare tracker and multi-leg itinerary optimizer for one family trip in October 2026.
The trip has passed and tracking has stopped. This repository is kept as a retrospective.

Live dashboard (frozen on the last sweep before the trip, 2026-10-07):
https://dr-daneger.github.io/flight-sweep/

## What it did

The trip had 3 priced flight legs and a road trip in the middle:

1. Home airport to Dallas (DFW), 2 candidate dates.
2. Dallas to a European entry city, 2 candidate dates. The entry city was the free choice, out of 12 candidates.
3. Bordeaux (BOD) back home, 2 candidate dates.

A one-way rental van covered everything between the entry city and Bordeaux.

Each day a GitHub Actions job fetched fares for every leg, date and entry city (28 queries per sweep).
It stored every offer in a SQLite database, ranked the full itineraries, and published a static dashboard to GitHub Pages.
The dashboard showed ranked itineraries, price moves of 5% or more since the previous sweep, per-leg price history, and a deep link from every price to the matching Google Flights search.
It recorded 122 sweeps before the trip.

## Engineering points

**The itinerary problem is separable, not a traveling-salesman search.**
The van removes all routing inside Europe, so flights are needed only at 3 fixed cut points.
Total cost is `best(leg 1) + best(Dallas to X) + best(leg 3) + van drop fee(X)`.
Each term is minimized on its own, so the global optimum is a minimum over 12 cities.
No combinatorial search is needed (`src/optimizer.py`).

**Multi-source fallback with stale-price flagging.**
Each query tries its sources in order and stops at the first that returns offers (`fetch_all` in `src/sweep.py`).
With API keys present, the Amadeus flight-offers API is primary and a Google Flights scraper backfills empty results.
Without keys, the scraper runs the whole sweep.
The scraper itself tries a plain page fetch first, then a headless Chromium browser for routes the lite page will not render (`src/gflights.py`).
Any failure degrades to "no data for that query" instead of a failed sweep.
When a sweep misses a query, the ranking reuses the freshest earlier compliant price and marks it stale on the dashboard (`latest_offers` in `src/store.py`).

**Constraints applied at fetch time.**
Departure-time windows and per-leg stop limits are read from `config.yaml` and applied inside each source, before offers are truncated to the cheapest 5 (`src/timewin.py`, `src/gflights.py`, `src/amadeus_client.py`).
Out-of-window fares never enter the database.
Offers whose time or stop count is unknown are kept and marked on the dashboard.
Stop limits are checked again at ranking time, where an unknown stop count does not rank.

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m src.sweep                  # Amadeus if keys are set, else the Google Flights scraper
python -m src.sweep --mock           # generated fares, for development
python -m src.sweep --render-only    # rebuild the dashboard from the stored database
```

Amadeus keys go in the `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET` environment variables.
The workflow in `.github/workflows/flight-sweep.yml` now runs only when triggered by hand.
A manual run overwrites the frozen dashboard with a new sweep.

## Layout

```
config.yaml            legs, dates, time windows, stop limits, entry-city candidates
src/sweep.py           command-line entry point: fetch, store, optimize, render
src/amadeus_client.py  Amadeus flight-offers source
src/gflights.py        Google Flights scraper source
src/timewin.py         shared time-window and stop-limit filters
src/store.py           SQLite fare history and stale fallback
src/optimizer.py       itinerary assembly, ranking, price-move alerts
src/render.py          static dashboard generator
data/prices.sqlite     full fare history
docs/index.html        dashboard served by GitHub Pages
```

## Limits

- No automated tests.
- The Google Flights source is an unofficial scraper and breaks when the page changes.
- Van drop fees and drive times are rough hand estimates.
- Prices are from 2026 and are no longer bookable.

## Authorship

Designed and specified by the owner; built by directing AI coding agents.

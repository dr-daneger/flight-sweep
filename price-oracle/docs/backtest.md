# price-oracle backtest scorecard

_Walk-forward over 180 (cutoff, horizon) pairs · 2026-06-22_

| Metric | Value |
|---|---|
| Mean pinball loss (q50) | 123.9 |
| 90% interval coverage | 88% (target 90%) |
| Data weight, first→last cutoff | 14% → 68% (prior weight shrinks as live data accrues) |

## By horizon

| Horizon (days) | n | Mean pinball | Coverage |
|---:|---:|---:|---:|
| 7 | 60 | 121.7 | 88% |
| 14 | 60 | 123.0 | 88% |
| 28 | 60 | 127.0 | 88% |

"""Storage adapter: DuckDB query engine over date-partitioned Parquet.

The three logical layers (spec §5) are append-only and immutable upstream so the
whole pipeline is reproducible and backtestable. Each run writes its own Parquet
file into a `dt=YYYY-MM-DD` partition; files are never rewritten. DuckDB reads
the union of all partitions (schema-tolerant via union_by_name), so adding a
column to a later run does not break reads of earlier ones.

This module is the persistence SEAM (spec §5.5): the rest of the engine speaks
only `read_table` / `write_table`, so swapping the local Parquet tree for an R2/S3
bucket later is a change here, not a rewrite across the models.
"""
from pathlib import Path

import duckdb
import pandas as pd

# Append-only layers. Everything is partitioned by date; nothing is mutated.
TABLES = (
    "raw_observations",   # immutable, one row per fetched offer per run (§5.1)
    "offers",             # normalized, derived from raw (§5.2)
    "market_state_daily", # V1 summary per sku per day (§5.4)
    "forecasts",          # posterior-predictive quantiles by horizon (§5.4)
    "hazard_curve",        # survival S(t) (§5.4)
    "decisions",          # daily verdict + threshold + rationale (§5.4)
    "predictions_log",    # every forecast made, for calibration scoring (§7)
)


def _table_dir(data_dir, name):
    return Path(data_dir) / name


def write_table(data_dir, name, rows, run_id, on_date):
    """Append `rows` (list of dicts or DataFrame) as one immutable Parquet file
    in the date partition for `on_date`. `run_id` keys the file so repeated runs
    on the same day each persist their own snapshot (versioning, spec §5.4)."""
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        return None
    part = _table_dir(data_dir, name) / f"dt={on_date}"
    part.mkdir(parents=True, exist_ok=True)
    path = part / f"{run_id}.parquet"
    df.to_parquet(path, index=False)
    return path


def read_table(data_dir, name, where=None):
    """Return the union of every partition for `name` as a DataFrame (empty if
    the table has no data yet). `where` is an optional SQL predicate."""
    glob = _table_dir(data_dir, name) / "**" / "*.parquet"
    if not list(_table_dir(data_dir, name).glob("**/*.parquet")):
        return pd.DataFrame()
    con = duckdb.connect()
    try:
        sql = (f"SELECT * FROM read_parquet('{glob}', union_by_name=true, "
               f"hive_partitioning=true)")
        if where:
            sql += f" WHERE {where}"
        return con.execute(sql).fetch_df()
    finally:
        con.close()


def latest_run(data_dir, name):
    """run_id of the most recent run that wrote to `name`, or None."""
    df = read_table(data_dir, name)
    if df.empty or "run_id" not in df.columns:
        return None
    return sorted(df["run_id"].unique())[-1]

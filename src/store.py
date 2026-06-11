"""SQLite persistence for fare snapshots across sweeps."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS fare_snapshots (
    id INTEGER PRIMARY KEY,
    sweep_id TEXT NOT NULL,        -- ISO timestamp shared by every row in a sweep
    source TEXT NOT NULL,          -- amadeus | gflights | mock
    leg TEXT NOT NULL,             -- outbound | transatlantic | home
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    dep_date TEXT NOT NULL,
    rank INTEGER NOT NULL,         -- 0 = cheapest offer for this query
    price REAL NOT NULL,           -- total for the whole party
    currency TEXT NOT NULL,
    carrier TEXT,
    stops INTEGER,
    duration_min INTEGER,
    dep_time TEXT,
    arr_time TEXT,
    stops_detail TEXT              -- JSON [{"airport": ..., "minutes": ...}] per layover
);
CREATE INDEX IF NOT EXISTS idx_snap_query
    ON fare_snapshots (leg, origin, dest, dep_date, sweep_id);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # Idempotent migration for databases created before stops_detail existed
    # (CREATE TABLE IF NOT EXISTS does not add columns to an existing table).
    cols = {r[1] for r in con.execute("PRAGMA table_info(fare_snapshots)")}
    if "stops_detail" not in cols:
        con.execute("ALTER TABLE fare_snapshots ADD COLUMN stops_detail TEXT")
        con.commit()
    return con


def insert_offers(con, sweep_id, source, leg, origin, dest, dep_date, offers):
    rows = [
        (sweep_id, source, leg, origin, dest, dep_date, rank,
         o["price"], o["currency"], o.get("carrier"), o.get("stops"),
         o.get("duration_min"), o.get("dep_time"), o.get("arr_time"),
         o.get("stops_detail"))
        for rank, o in enumerate(offers)
    ]
    con.executemany(
        "INSERT INTO fare_snapshots (sweep_id, source, leg, origin, dest, dep_date,"
        " rank, price, currency, carrier, stops, duration_min, dep_time, arr_time,"
        " stops_detail)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()


def sweep_ids(con, sources=("amadeus", "gflights")):
    """Sweep ids that contain ranking data from the given sources, oldest first."""
    ph = ",".join("?" * len(sources))
    return [r[0] for r in con.execute(
        f"""SELECT DISTINCT sweep_id FROM fare_snapshots
            WHERE source IN ({ph}) ORDER BY sweep_id""", sources)]


def best_offers(con, sweep_id, sources=("amadeus", "gflights")):
    """Cheapest offer per (leg, origin, dest, dep_date) within one sweep."""
    ph = ",".join("?" * len(sources))
    rows = con.execute(
        f"""SELECT * FROM fare_snapshots
            WHERE sweep_id = ? AND source IN ({ph}) AND rank = 0""",
        (sweep_id, *sources)).fetchall()
    return {(r["leg"], r["origin"], r["dest"], r["dep_date"]): dict(r) for r in rows}


def all_offers(con, sweep_id, sources=("amadeus", "gflights")):
    ph = ",".join("?" * len(sources))
    rows = con.execute(
        f"""SELECT * FROM fare_snapshots
            WHERE sweep_id = ? AND source IN ({ph})
            ORDER BY leg, origin, dest, dep_date, rank""",
        (sweep_id, *sources)).fetchall()
    return [dict(r) for r in rows]


def latest_offers(con, current_sweep_id, sources=("amadeus", "gflights")):
    """Cheapest offer per query from the most recent sweep that has one.

    Brittle sources (the scraper) can miss queries in any given sweep; rather
    than dropping those cities from the ranking, fall back to the freshest
    prior price and mark it stale.
    """
    ph = ",".join("?" * len(sources))
    rows = con.execute(
        f"""SELECT f.* FROM fare_snapshots f
            JOIN (SELECT leg, origin, dest, dep_date, MAX(sweep_id) AS ms
                  FROM fare_snapshots WHERE source IN ({ph})
                  GROUP BY leg, origin, dest, dep_date) m
              ON f.leg = m.leg AND f.origin = m.origin AND f.dest = m.dest
             AND f.dep_date = m.dep_date AND f.sweep_id = m.ms
            WHERE f.rank = 0 AND f.source IN ({ph})""",
        (*sources, *sources)).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["stale"] = d["sweep_id"] if d["sweep_id"] != current_sweep_id else None
        out[(r["leg"], r["origin"], r["dest"], r["dep_date"])] = d
    return out


def price_history(con, sources=("amadeus", "gflights")):
    """Best price per query per sweep: {(leg,origin,dest,dep_date): [(sweep_id, price), ...]}"""
    ph = ",".join("?" * len(sources))
    rows = con.execute(
        f"""SELECT leg, origin, dest, dep_date, sweep_id, MIN(price) AS price
            FROM fare_snapshots WHERE source IN ({ph})
            GROUP BY leg, origin, dest, dep_date, sweep_id
            ORDER BY sweep_id""", sources).fetchall()
    hist = {}
    for r in rows:
        hist.setdefault((r["leg"], r["origin"], r["dest"], r["dep_date"]), []) \
            .append((r["sweep_id"], r["price"]))
    return hist


def gflights_checks(con, sweep_id):
    rows = con.execute(
        """SELECT * FROM fare_snapshots
           WHERE sweep_id = ? AND source = 'gflights-check' AND rank = 0""",
        (sweep_id,)).fetchall()
    return {(r["leg"], r["origin"], r["dest"], r["dep_date"]): dict(r) for r in rows}

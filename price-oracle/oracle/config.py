"""Configuration loading: the YAML files are the single source of truth for
the SKUs, the event calendar, the reference-class priors, and every policy knob
(spec §11 decisions live in config.yaml, not in code)."""
import datetime as dt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent          # price-oracle/


def load(config_path=None, events_path=None, refclass_path=None):
    config_path = Path(config_path or ROOT / "config.yaml")
    events_path = Path(events_path or ROOT / "events.yaml")
    refclass_path = Path(refclass_path or ROOT / "reference_class.yaml")

    cfg = yaml.safe_load(config_path.read_text())
    cfg["events"] = yaml.safe_load(events_path.read_text())["events"]
    cfg["reference_class"] = yaml.safe_load(refclass_path.read_text())["groups"]
    cfg["_root"] = ROOT
    cfg["skus_by_key"] = {s["sku_key"]: s for s in cfg["skus"]}
    return cfg


def data_dir(cfg):
    d = ROOT / cfg["storage"]["data_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_date(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def active_events(cfg, on_date, sku_key):
    """Events active on `on_date` whose scope covers `sku_key`."""
    on_date = parse_date(on_date)
    out = []
    for e in cfg["events"]:
        if e.get("sku_scope", "all") not in ("all", sku_key):
            continue
        if parse_date(e["start_date"]) <= on_date <= parse_date(e["end_date"]):
            out.append(e)
    return out

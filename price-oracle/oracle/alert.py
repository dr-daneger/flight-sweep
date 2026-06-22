"""Alerting (spec §9), zero-infra. Fires on: a BUY verdict, a threshold
crossing, or a data-quality anomaly. The default channel is a GitHub Issue —
this module writes `docs/alert.json`, and the workflow opens/updates one issue
from it; if ALERT_WEBHOOK_URL is set it also POSTs (Slack/Discord/ntfy/email).
"""
import json
import os
from pathlib import Path

import requests


def build_alerts(cfg, decision, anomalies, prev_decision):
    fire_on = set(cfg["alerts"]["fire_on"])
    out = []
    if "buy_verdict" in fire_on and decision["verdict"] == "BUY":
        out.append({
            "level": "strong", "kind": "buy_verdict",
            "title": f"BUY — {decision['sku_key']} @ ${decision['best_legit_now']:,.0f}",
            "body": decision["rationale"]})
    if "threshold_cross" in fire_on and prev_decision and \
            decision.get("best_legit_now") is not None:
        was_above = (prev_decision.get("best_legit_now") or 1e12) > \
            (prev_decision.get("threshold") or 0)
        now_below = decision["best_legit_now"] <= decision["threshold"]
        if was_above and now_below and decision["verdict"] != "BUY":
            out.append({
                "level": "info", "kind": "threshold_cross",
                "title": f"Threshold crossed — {decision['sku_key']}",
                "body": (f"Best legit ${decision['best_legit_now']:,.0f} crossed "
                         f"below threshold ${decision['threshold']:,.0f}.")})
    if "data_anomaly" in fire_on and anomalies:
        out.append({
            "level": "warn", "kind": "data_anomaly",
            "title": f"Data anomaly — {decision['sku_key']} ({len(anomalies)})",
            "body": "; ".join(f"{a['kind']}: {a.get('detail','')}" for a in anomalies)})
    return out


def emit(alerts, out_path):
    """Persist alerts for the workflow's issue step and optionally POST a webhook."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    for a in alerts:
        print(f"  ::notice title={a['title']}::{a['body']}")
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if url and alerts:
        try:
            requests.post(url, json={"text": "\n".join(
                f"*{a['title']}*\n{a['body']}" for a in alerts)}, timeout=15)
        except requests.RequestException as exc:
            print(f"  ! webhook post failed: {type(exc).__name__}: {str(exc)[:80]}")
    return out_path

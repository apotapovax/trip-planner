#!/usr/bin/env python3
"""Send daily digest email for pending digest-tier alerts."""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.alerts import AlertCandidate, AlertEngine
from radar.config import load_config
from radar.store import PriceStore


def main():
    parser = argparse.ArgumentParser(description="Send flight radar digest email")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args()

    cfg = load_config(args.config)
    store = PriceStore(cfg.sqlite_path())
    engine = AlertEngine(cfg.alerts, store)

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with store.connection() as conn:
        rows = conn.execute(
            """SELECT route_key, origin, destination, depart_date, return_date,
                      price, currency, airline, departs_at, observed_at
               FROM price_observations
               WHERE is_comfortable = 1 AND observed_at >= ?
               ORDER BY price ASC""",
            (since,),
        ).fetchall()

    seen = set()
    candidates: list[AlertCandidate] = []
    for row in rows:
        rk = row["route_key"]
        if rk in seen:
            continue
        seen.add(rk)
        percentile = store.percentile_rank(rk, row["price"])
        if percentile is None or percentile > cfg.alerts.percentile_threshold:
            continue
        candidates.append(AlertCandidate(
            route_key=rk,
            origin=row["origin"],
            destination=row["destination"],
            depart_date=row["depart_date"],
            return_date=row["return_date"],
            price=row["price"],
            currency=row["currency"],
            previous_price=None,
            drop_usd=0,
            drop_pct=0,
            percentile=percentile,
            airline=row["airline"],
            departs_at=row["departs_at"],
            alert_type="digest",
            reason=f"daily digest — {percentile:.0f}th percentile",
        ))

    if not candidates:
        print("No digest-worthy deals in the last 24h.")
        return

    result = engine.dispatch(candidates, force_digest=True)
    print(f"Digest sent: {result}")


if __name__ == "__main__":
    main()

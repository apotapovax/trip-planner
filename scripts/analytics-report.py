#!/usr/bin/env python3
"""Print price analytics and insights from local SQLite history."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.config import load_config
from radar.metrics import ScanMetrics, format_metrics_report, print_storage_projection
from radar.store import PriceStore


def main():
    parser = argparse.ArgumentParser(description="Flight price analytics report")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--route", help="Route key prefix filter e.g. RDU-SFO")
    parser.add_argument("--group", help="Route group name from config")
    parser.add_argument("--top", type=int, default=15, help="Top deals to show")
    parser.add_argument("--project", action="store_true", help="Show 1-year storage projection")
    args = parser.parse_args()

    cfg = load_config(args.config)
    store = PriceStore(cfg.sqlite_path())

    print("=== Trip Planner Radar — Analytics ===\n")
    print(f"Database: {store.db_path} ({store.db_size_bytes() / 1024 / 1024:.2f} MB)")
    print(f"Observations: {store.observation_count():,}\n")

    deals = store.top_deals(route_group=args.group, limit=args.top)
    if deals:
        print(f"Best comfortable prices (top {args.top}):")
        for d in deals:
            print(
                f"  {d['route_key']:40}  ${d['best_price']:>7.0f} {d['currency']}  "
                f"last {d['last_seen'][:10]}"
            )
    else:
        print("No comfortable price data yet. Run: python scripts/scan-radar.py")

    print("\n--- Route statistics (comfortable flights, 365d) ---")
    with store.connection() as conn:
        q = """
            SELECT DISTINCT route_key, origin, destination
            FROM price_observations WHERE is_comfortable = 1
        """
        params = []
        if args.route:
            q += " AND route_key LIKE ?"
            params.append(f"{args.route}%")
        if args.group:
            q += " AND route_group = ?"
            params.append(args.group)
        routes = conn.execute(q, params).fetchall()

    for r in routes:
        stats = store.route_stats(r["route_key"])
        if not stats:
            continue
        print(
            f"  {r['route_key']}: "
            f"${stats['min_price']:.0f}–${stats['max_price']:.0f} "
            f"(avg ${stats['avg_price']:.0f}, n={stats['n']})"
        )

    m = ScanMetrics(db_bytes=store.db_size_bytes())
    print("\n" + format_metrics_report(m, store))

    if args.project:
        print_storage_projection(store, scans_per_day=24)


if __name__ == "__main__":
    main()

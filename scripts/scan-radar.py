#!/usr/bin/env python3
"""Run the flight radar scan — use in cron every hour."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.scanner import run_scan


def main():
    parser = argparse.ArgumentParser(description="Scan configured routes and store price analytics")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"), help="Path to config.yaml")
    parser.add_argument("--mode", default="watch", choices=["watch", "discovery", "oneway"])
    parser.add_argument("--groups", nargs="*", help="Route group names (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Scan without alerts or tracked.json sync")
    args = parser.parse_args()

    run_scan(args.config, mode=args.mode, groups=args.groups, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

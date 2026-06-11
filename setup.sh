#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Installing trip-planner radar dependencies..."
pip install -r requirements.txt
mkdir -p data
echo ""
echo "Done. Quick start:"
echo "  python scripts/scan-radar.py --groups us_leisure --dry-run"
echo "  python scripts/analytics-report.py"
echo ""
echo "Optional email alerts — set env vars:"
echo "  SMTP_HOST SMTP_USER SMTP_PASS ALERT_EMAIL_TO"

# Fork notes

This repository is a fork of [jackculpan/flightclaw](https://github.com/jackculpan/flightclaw).

## Renovations added

- **`radar/`** — SQLite analytics store, comfort filters, smart alerts, resource metrics
- **`config.yaml`** — RDU-centric route groups (US leisure, Europe nonstop, Asia-Pacific hubs)
- **`scripts/scan-radar.py`** — hourly cron scanner
- **`scripts/analytics-report.py`** — price range insights from local history
- **`scripts/send-digest.py`** — daily email digest

## Syncing with upstream

```bash
git fetch upstream
git merge upstream/master   # or rebase, your choice
```

Upstream MCP server, Duffel integration, and CLI scripts are preserved.

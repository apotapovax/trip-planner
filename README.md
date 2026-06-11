# Trip Planner Radar

Personal flight price radar built on [FlightClaw](https://github.com/jackculpan/flightclaw) + [fli](https://github.com/punitarani/fli) (Google Flights). Tracks **real prices** locally in SQLite for long-term analytics, filters out brutal schedules (late-night departures, red-eyes), and sends **anti-spam email alerts**.

Default origin: **RDU** (Raleigh-Durham). Monitors US leisure cities (Chicago, Seattle, SLC, Vegas, LA, SF…) and international routes (Europe nonstop, Asia-Pacific via CLT/IAD/ATL).

## What's new vs upstream FlightClaw

| Feature | FlightClaw | This fork |
|---------|------------|-----------|
| Price history | `data/tracked.json` | SQLite + JSON sync |
| Long-term analytics | Basic per-route history | Min/max/avg, percentiles, 400-day retention |
| Comfort filters | Manual time params | Automatic — rejects 23:40 takeoffs, red-eyes, long layovers |
| Notifications | Console alerts | SMTP instant + daily digest, cooldown, daily cap |
| Resource metrics | — | Wall/CPU time, memory, DB size, energy estimate (Wh) |
| Config-driven routes | Manual track commands | `config.yaml` route groups |

## Setup

```bash
./setup.sh
# or: pip install -r requirements.txt && mkdir -p data
```

Copy and edit `config.yaml` for your routes and comfort preferences.

### Email alerts (optional)

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASS=your-app-password
export ALERT_EMAIL_TO=you@gmail.com
```

## Usage

```bash
# Scan all route groups (hourly cron)
python scripts/scan-radar.py

# Scan specific groups only
python scripts/scan-radar.py --groups us_leisure europe_nonstop

# Dry run — no emails, no tracked.json writes
python scripts/scan-radar.py --groups us_leisure --dry-run

# Analytics report (price ranges after data accumulates)
python scripts/analytics-report.py
python scripts/analytics-report.py --group us_leisure --project

# Daily digest (7am cron)
python scripts/send-digest.py
```

### Cron example

```cron
# Hourly watch scan
0 * * * * cd /path/to/trip-planner && python scripts/scan-radar.py >> data/scan.log 2>&1

# Daily discovery scan (broader date window)
0 6 * * * cd /path/to/trip-planner && python scripts/scan-radar.py --mode discovery >> data/scan.log 2>&1

# Morning digest
0 7 * * * cd /path/to/trip-planner && python scripts/send-digest.py >> data/scan.log 2>&1
```

## Comfort filters

Configured in `config.yaml` under `comfort:`:

- **Depart 06:30–20:30** — no brutal late-night takeoffs
- **Return 07:00–20:00**
- **Latest arrival 23:30** — avoids forced hotel nights
- **Layover 45–180 min**
- Flights below `min_comfort_score` are **logged** for analytics but **excluded from alerts**

## Data storage

| File | Purpose |
|------|---------|
| `data/radar.db` | SQLite — all price observations, scan metrics, alert history |
| `data/tracked.json` | FlightClaw-compatible tracking (MCP tools still work) |

After ~1 year of hourly scans, expect roughly **50–150 MB** depending on route count (run `analytics-report.py --project` for your projection).

## MCP server (FlightClaw)

Still works unchanged:

```bash
pip install flights "mcp[cli]" fastmcp
claude mcp add flightclaw -- python3 /path/to/trip-planner/server.py
```

## Metrics

Each scan records:

- Wall time & CPU time
- Peak memory (via psutil)
- Estimated laptop energy (Wh) — ballpark using CPU fraction × 15W TDP
- SQLite size & observations per scan

Printed at end of every `scan-radar.py` run.

## Upstream

Forked from [jackculpan/flightclaw](https://github.com/jackculpan/flightclaw). Flight data via Google Flights through the `fli` library.

## License

MIT (same as upstream FlightClaw)

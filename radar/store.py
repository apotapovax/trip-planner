"""SQLite store for long-term flight price analytics."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    routes_checked INTEGER DEFAULT 0,
    observations_stored INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    wall_seconds REAL,
    cpu_seconds REAL,
    memory_peak_mb REAL,
    db_bytes INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    route_key TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    airline TEXT,
    flight_number TEXT,
    departs_at TEXT,
    arrives_at TEXT,
    return_departs_at TEXT,
    return_arrives_at TEXT,
    duration_min INTEGER,
    stops INTEGER,
    cabin TEXT,
    stops_filter TEXT,
    comfort_score REAL,
    is_comfortable INTEGER NOT NULL DEFAULT 0,
    comfort_reasons TEXT,
    route_group TEXT,
    FOREIGN KEY (scan_id) REFERENCES scan_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_obs_route ON price_observations(route_key, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_comfortable ON price_observations(is_comfortable, route_key);
CREATE INDEX IF NOT EXISTS idx_obs_depart ON price_observations(depart_date);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_key TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    price REAL,
    currency TEXT,
    subject TEXT,
    body TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_route ON alerts_sent(route_key, sent_at);

CREATE TABLE IF NOT EXISTS daily_metrics (
    day TEXT PRIMARY KEY,
    scan_count INTEGER DEFAULT 0,
    observations_added INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    wall_seconds REAL DEFAULT 0,
    cpu_seconds REAL DEFAULT 0,
    db_bytes INTEGER,
    instant_alerts_sent INTEGER DEFAULT 0,
    digest_sent INTEGER DEFAULT 0
);
"""


class PriceStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def new_scan_id(self) -> str:
        return str(uuid.uuid4())

    def start_scan(self, scan_id: str, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO scan_runs (id, started_at, mode) VALUES (?, ?, ?)",
                (scan_id, now, mode),
            )

    def finish_scan(
        self,
        scan_id: str,
        *,
        routes_checked: int,
        observations_stored: int,
        api_calls: int,
        wall_seconds: float,
        cpu_seconds: float,
        memory_peak_mb: float | None = None,
        notes: str = "",
    ) -> None:
        db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        now = datetime.now(timezone.utc).isoformat()
        day = now[:10]
        with self.connection() as conn:
            conn.execute(
                """UPDATE scan_runs SET
                    finished_at=?, routes_checked=?, observations_stored=?,
                    api_calls=?, wall_seconds=?, cpu_seconds=?, memory_peak_mb=?,
                    db_bytes=?, notes=?
                WHERE id=?""",
                (
                    now, routes_checked, observations_stored, api_calls,
                    wall_seconds, cpu_seconds, memory_peak_mb, db_bytes, notes, scan_id,
                ),
            )
            conn.execute(
                """INSERT INTO daily_metrics (day, scan_count, observations_added, api_calls, wall_seconds, cpu_seconds, db_bytes)
                   VALUES (?, 1, ?, ?, ?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET
                     scan_count = scan_count + 1,
                     observations_added = observations_added + excluded.observations_added,
                     api_calls = api_calls + excluded.api_calls,
                     wall_seconds = wall_seconds + excluded.wall_seconds,
                     cpu_seconds = cpu_seconds + excluded.cpu_seconds,
                     db_bytes = excluded.db_bytes
                """,
                (day, observations_stored, api_calls, wall_seconds, cpu_seconds, db_bytes),
            )

    @staticmethod
    def route_key(origin: str, destination: str, depart_date: str, return_date: str | None = None) -> str:
        base = f"{origin}-{destination}-{depart_date}"
        return f"{base}-RT-{return_date}" if return_date else base

    def insert_observation(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str,
        origin: str,
        destination: str,
        depart_date: str,
        return_date: str | None,
        price: float,
        currency: str,
        airline: str | None,
        flight_number: str | None,
        departs_at: str | None,
        arrives_at: str | None,
        return_departs_at: str | None,
        return_arrives_at: str | None,
        duration_min: int | None,
        stops: int,
        cabin: str,
        stops_filter: str,
        comfort_score: float,
        is_comfortable: bool,
        comfort_reasons: list[str],
        route_group: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rk = self.route_key(origin, destination, depart_date, return_date)
        conn.execute(
            """INSERT INTO price_observations (
                scan_id, observed_at, route_key, origin, destination, depart_date, return_date,
                price, currency, airline, flight_number, departs_at, arrives_at,
                return_departs_at, return_arrives_at, duration_min, stops, cabin, stops_filter,
                comfort_score, is_comfortable, comfort_reasons, route_group
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                scan_id, now, rk, origin, destination, depart_date, return_date,
                price, currency, airline, flight_number, departs_at, arrives_at,
                return_departs_at, return_arrives_at, duration_min, stops, cabin, stops_filter,
                comfort_score, 1 if is_comfortable else 0, json.dumps(comfort_reasons), route_group,
            ),
        )

    def price_history(
        self,
        route_key: str,
        *,
        comfortable_only: bool = True,
        days: int = 365,
    ) -> list[float]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = """
            SELECT price FROM price_observations
            WHERE route_key = ? AND observed_at >= ?
        """
        params: list[Any] = [route_key, since]
        if comfortable_only:
            query += " AND is_comfortable = 1"
        query += " ORDER BY observed_at"
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [r["price"] for r in rows]

    def route_stats(self, route_key: str, comfortable_only: bool = True) -> dict[str, Any] | None:
        since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        filt = " AND is_comfortable = 1" if comfortable_only else ""
        with self.connection() as conn:
            row = conn.execute(
                f"""SELECT
                    COUNT(*) as n,
                    MIN(price) as min_price,
                    MAX(price) as max_price,
                    AVG(price) as avg_price,
                    MIN(observed_at) as first_seen,
                    MAX(observed_at) as last_seen
                FROM price_observations
                WHERE route_key = ? AND observed_at >= ? {filt}""",
                (route_key, since),
            ).fetchone()
        if not row or row["n"] == 0:
            return None
        return dict(row)

    def latest_comfortable_price(self, route_key: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT * FROM price_observations
                   WHERE route_key = ? AND is_comfortable = 1
                   ORDER BY observed_at DESC LIMIT 1""",
                (route_key,),
            ).fetchone()

    def percentile_rank(self, route_key: str, price: float, comfortable_only: bool = True) -> float | None:
        prices = self.price_history(route_key, comfortable_only=comfortable_only)
        if len(prices) < 5:
            return None
        below = sum(1 for p in prices if p <= price)
        return (below / len(prices)) * 100

    def record_alert(
        self,
        route_key: str,
        alert_type: str,
        price: float,
        currency: str,
        subject: str,
        body: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        day = now[:10]
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO alerts_sent (route_key, alert_type, sent_at, price, currency, subject, body)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (route_key, alert_type, now, price, currency, subject, body),
            )
            if alert_type == "instant":
                conn.execute(
                    """INSERT INTO daily_metrics (day, instant_alerts_sent) VALUES (?, 1)
                       ON CONFLICT(day) DO UPDATE SET instant_alerts_sent = instant_alerts_sent + 1""",
                    (day,),
                )
            elif alert_type == "digest":
                conn.execute(
                    """INSERT INTO daily_metrics (day, digest_sent) VALUES (?, 1)
                       ON CONFLICT(day) DO UPDATE SET digest_sent = digest_sent + 1""",
                    (day,),
                )

    def alerts_in_cooldown(self, route_key: str, hours: int) -> bool:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM alerts_sent WHERE route_key = ? AND sent_at >= ? LIMIT 1",
                (route_key, since),
            ).fetchone()
        return row is not None

    def instant_alerts_today(self, tz_day: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT instant_alerts_sent FROM daily_metrics WHERE day = ?",
                (tz_day,),
            ).fetchone()
        return int(row["instant_alerts_sent"]) if row else 0

    def purge_old(self, retain_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM price_observations WHERE observed_at < ?", (cutoff,))
            deleted = cur.rowcount
            conn.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (cutoff,))
            conn.execute("DELETE FROM scan_runs WHERE started_at < ?", (cutoff,))
        return deleted

    def vacuum_if_needed(self, interval_days: int) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(started_at) as last FROM scan_runs WHERE notes LIKE '%vacuum%'"
            ).fetchone()
        if row and row["last"]:
            last = datetime.fromisoformat(row["last"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last < timedelta(days=interval_days):
                return False
        with self.connection() as conn:
            conn.execute("VACUUM")
        scan_id = self.new_scan_id()
        self.start_scan(scan_id, "maintenance")
        self.finish_scan(scan_id, routes_checked=0, observations_stored=0, api_calls=0,
                         wall_seconds=0, cpu_seconds=0, notes="vacuum")
        return True

    def db_size_bytes(self) -> int:
        if not self.db_path.exists():
            return 0
        total = self.db_path.stat().st_size
        wal = Path(str(self.db_path) + "-wal")
        if wal.exists():
            total += wal.stat().st_size
        return total

    def observation_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM price_observations").fetchone()
        return int(row["c"])

    def top_deals(self, route_group: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        query = """
            SELECT route_key, origin, destination, depart_date, MIN(price) as best_price,
                   currency, MAX(observed_at) as last_seen
            FROM price_observations
            WHERE is_comfortable = 1
        """
        params: list[Any] = []
        if route_group:
            query += " AND route_group = ?"
            params.append(route_group)
        query += " GROUP BY route_key ORDER BY best_price ASC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            return conn.execute(query, params).fetchall()

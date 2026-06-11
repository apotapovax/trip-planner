"""Storage and energy consumption metrics for laptop-friendly monitoring."""

from __future__ import annotations

import os
import resource
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

from radar.store import PriceStore


# Rough laptop TDP estimate for energy ballpark (adjust in config if needed)
DEFAULT_TDP_WATTS = 15.0


@dataclass
class ScanMetrics:
    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0
    memory_peak_mb: float | None = None
    api_calls: int = 0
    routes_checked: int = 0
    observations_stored: int = 0
    db_bytes: int = 0
    tracked_json_bytes: int = 0
    estimated_wh: float = 0.0
    notes: list[str] = field(default_factory=list)


class MetricsTracker:
    """Track wall time, CPU time, memory, and storage during a scan."""

    def __init__(self, tdp_watts: float = DEFAULT_TDP_WATTS):
        self.tdp_watts = tdp_watts
        self._start_wall = 0.0
        self._start_cpu = 0.0
        self._proc = psutil.Process(os.getpid()) if psutil else None
        self._mem_peak = 0.0

    def start(self) -> None:
        self._start_wall = perf_counter()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self._start_cpu = usage.ru_utime + usage.ru_stime
        if self._proc:
            self._mem_peak = self._proc.memory_info().rss / (1024 * 1024)

    def sample_memory(self) -> None:
        if self._proc:
            mb = self._proc.memory_info().rss / (1024 * 1024)
            self._mem_peak = max(self._mem_peak, mb)

    def finish(
        self,
        *,
        api_calls: int = 0,
        routes_checked: int = 0,
        observations_stored: int = 0,
        store: PriceStore | None = None,
        tracked_json: Path | None = None,
    ) -> ScanMetrics:
        wall = perf_counter() - self._start_wall
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (usage.ru_utime + usage.ru_stime) - self._start_cpu
        self.sample_memory()

        db_bytes = store.db_size_bytes() if store else 0
        tracked_bytes = tracked_json.stat().st_size if tracked_json and tracked_json.exists() else 0

        # Energy estimate: CPU-active fraction × TDP × wall time
        cpu_fraction = min(1.0, cpu / wall) if wall > 0 else 0
        estimated_wh = (self.tdp_watts * wall * cpu_fraction) / 3600

        return ScanMetrics(
            wall_seconds=round(wall, 2),
            cpu_seconds=round(cpu, 2),
            memory_peak_mb=round(self._mem_peak, 1) if self._mem_peak else None,
            api_calls=api_calls,
            routes_checked=routes_checked,
            observations_stored=observations_stored,
            db_bytes=db_bytes,
            tracked_json_bytes=tracked_bytes,
            estimated_wh=round(estimated_wh, 4),
        )


def format_metrics_report(m: ScanMetrics, store: PriceStore | None = None) -> str:
    lines = [
        "=== Radar metrics ===",
        f"Wall time:      {m.wall_seconds:.1f}s",
        f"CPU time:       {m.cpu_seconds:.1f}s",
        f"Est. energy:    {m.estimated_wh:.3f} Wh (~{m.estimated_wh * 1000:.1f} mWh)",
    ]
    if m.memory_peak_mb:
        lines.append(f"Peak memory:    {m.memory_peak_mb:.1f} MB")
    lines.extend([
        f"API calls:      {m.api_calls}",
        f"Routes checked: {m.routes_checked}",
        f"Observations:   {m.observations_stored}",
        f"SQLite size:    {_fmt_bytes(m.db_bytes)}",
        f"tracked.json:   {_fmt_bytes(m.tracked_json_bytes)}",
    ])
    if store:
        obs = store.observation_count()
        lines.append(f"Total rows:     {obs:,} price observations")
        if obs > 0 and m.db_bytes > 0:
            lines.append(f"Avg bytes/obs:  {m.db_bytes // obs:,}")
    return "\n".join(lines)


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def print_storage_projection(store: PriceStore, scans_per_day: int = 24) -> None:
    """Estimate 1-year storage if current observation rate continues."""
    with store.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c, MIN(observed_at) as first FROM price_observations"
        ).fetchone()
    if not row or row["c"] == 0:
        print("No observations yet — cannot project storage.")
        return

    db_bytes = store.db_size_bytes()
    per_obs = db_bytes / row["c"]
    # Assume ~50 obs per scan average for projection
    obs_per_scan = max(row["c"] / max(1, scans_per_day), 20)
    year_obs = obs_per_scan * scans_per_day * 365
    projected = per_obs * year_obs
    print(f"\nStorage projection (1 year @ {scans_per_day} scans/day):")
    print(f"  ~{year_obs:,.0f} observations → ~{_fmt_bytes(int(projected))}")
    print(f"  Current DB: {_fmt_bytes(db_bytes)} ({row['c']:,} rows)")

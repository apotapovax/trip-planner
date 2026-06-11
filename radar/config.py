"""Load and validate config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


@dataclass
class ComfortConfig:
    depart_after: str = "06:30"
    depart_before: str = "20:30"
    return_depart_after: str = "07:00"
    return_depart_before: str = "20:00"
    latest_arrival: str = "23:30"
    min_layover_minutes: int = 45
    max_layover_minutes: int = 180
    min_comfort_score: float = 0.6


@dataclass
class ScannerConfig:
    discovery_days_ahead: int = 90
    discovery_interval_hours: int = 24
    watch_interval_minutes: int = 60
    hot_interval_minutes: int = 10
    max_api_results_per_route: int = 15


@dataclass
class AlertsConfig:
    enabled: bool = True
    instant_max_per_day: int = 3
    cooldown_hours: int = 24
    min_drop_usd: float = 75
    min_drop_pct: float = 8
    percentile_threshold: float = 15
    digest_hour_local: int = 7
    digest_timezone: str = "America/New_York"


@dataclass
class StorageConfig:
    sqlite_path: str = "data/radar.db"
    retain_days: int = 400
    vacuum_interval_days: int = 30


@dataclass
class RouteGroup:
    name: str
    description: str = ""
    origins: list[str] = field(default_factory=lambda: ["RDU"])
    destinations: list[str] = field(default_factory=list)
    stops: str = "NON_STOP"
    trip_duration_days: int | None = None
    dates: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    defaults: dict[str, Any] = field(default_factory=dict)
    comfort: ComfortConfig = field(default_factory=ComfortConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    route_groups: dict[str, RouteGroup] = field(default_factory=dict)

    def sqlite_path(self) -> Path:
        p = Path(self.storage.sqlite_path)
        if not p.is_absolute():
            p = ROOT / p
        return p


def _build_dataclass(cls, data: dict | None):
    if not data:
        return cls()
    valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**valid)


def load_config(path: Path | str | None = None) -> AppConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    with open(cfg_path, "r") as f:
        raw = yaml.safe_load(f) or {}

    groups: dict[str, RouteGroup] = {}
    for name, g in (raw.get("route_groups") or {}).items():
        groups[name] = RouteGroup(
            name=name,
            description=g.get("description", ""),
            origins=g.get("origins") or raw.get("defaults", {}).get("origin", "RDU").split(","),
            destinations=g.get("destinations", []),
            stops=g.get("stops") or raw.get("defaults", {}).get("stops", "NON_STOP"),
            trip_duration_days=g.get("trip_duration_days"),
            dates=g.get("dates") or [],
        )

    return AppConfig(
        defaults=raw.get("defaults") or {},
        comfort=_build_dataclass(ComfortConfig, raw.get("comfort")),
        scanner=_build_dataclass(ScannerConfig, raw.get("scanner")),
        alerts=_build_dataclass(AlertsConfig, raw.get("alerts")),
        storage=_build_dataclass(StorageConfig, raw.get("storage")),
        route_groups=groups,
    )


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

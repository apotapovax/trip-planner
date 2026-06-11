"""Scan routes from config, apply comfort filters, persist analytics."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from helpers import build_filters, load_tracked, save_tracked
from search_utils import search_with_currency

from radar.alerts import AlertCandidate, AlertEngine
from radar.comfort import score_flight
from radar.config import AppConfig, RouteGroup, load_config
from radar.metrics import MetricsTracker, format_metrics_report
from radar.store import PriceStore


def _fmt_time(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%H:%M")


def _extract_legs(flight) -> tuple[list, list | None, float, int, int]:
    if isinstance(flight, tuple):
        outbound, ret = flight
        out_legs = list(outbound.legs or [])
        ret_legs = list(ret.legs or []) if ret else None
        price = round(ret.price if ret is not None else outbound.price, 2)
        stops = max(outbound.stops, ret.stops if ret else 0)
        duration = (outbound.duration or 0) + (ret.duration or 0 if ret else 0)
        return out_legs, ret_legs, price, stops, duration
    out_legs = list(flight.legs or [])
    return out_legs, None, round(flight.price, 2), flight.stops, flight.duration or 0


def _discovery_dates(days_ahead: int, count: int = 4) -> list[str]:
    """Sample departure dates across the discovery window."""
    start = datetime.now(timezone.utc).date() + timedelta(days=14)
    end = start + timedelta(days=days_ahead)
    span = (end - start).days
    step = max(1, span // count)
    dates = []
    d = start
    while d <= end and len(dates) < count:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=step)
    return dates


def scan_route(
    store: PriceStore,
    conn,
    scan_id: str,
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cfg: AppConfig,
    group: RouteGroup,
    top_n: int,
) -> tuple[int, list[AlertCandidate], float | None]:
    """Returns (api_calls, alert_candidates, best_comfortable_price)."""
    defaults = cfg.defaults
    cabin = defaults.get("cabin", "ECONOMY")
    stops = group.stops or defaults.get("stops", "NON_STOP")
    exclude_basic = defaults.get("exclude_basic_economy", True)

    comfort = cfg.comfort
    dep_after_h = int(comfort.depart_after.split(":")[0])
    dep_before_h = int(comfort.depart_before.split(":")[0])

    filters = build_filters(
        origin, destination, depart_date, return_date,
        cabin=cabin, stops=stops,
        earliest_departure=dep_after_h,
        latest_departure=dep_before_h,
        exclude_basic_economy=exclude_basic,
    )

    results, currency = search_with_currency(filters, top_n=top_n, exclude_basic_economy=exclude_basic)
    api_calls = 1
    if not results:
        return api_calls, [], None

    route_key = store.route_key(origin, destination, depart_date, return_date)
    prev_row = store.latest_comfortable_price(route_key)
    previous_price = float(prev_row["price"]) if prev_row else None

    alert_engine = AlertEngine(cfg.alerts, store)
    candidates: list[AlertCandidate] = []
    best_price: float | None = None

    for item in results:
        flight_data = item[0] if isinstance(item, tuple) else item
        out_legs, ret_legs, price, stops_n, duration = _extract_legs(flight_data)
        if not out_legs:
            continue

        cr = score_flight(out_legs, ret_legs, comfort)
        first = out_legs[0]
        ret_first = ret_legs[0] if ret_legs else None

        store.insert_observation(
            conn,
            scan_id=scan_id,
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            price=price,
            currency=currency or "USD",
            airline=first.airline.name if first.airline else None,
            flight_number=first.flight_number,
            departs_at=_fmt_time(first.departure_datetime),
            arrives_at=_fmt_time(out_legs[-1].arrival_datetime),
            return_departs_at=_fmt_time(ret_first.departure_datetime) if ret_first else None,
            return_arrives_at=_fmt_time(ret_legs[-1].arrival_datetime) if ret_legs else None,
            duration_min=duration,
            stops=stops_n,
            cabin=cabin,
            stops_filter=stops,
            comfort_score=cr.score,
            is_comfortable=cr.is_comfortable,
            comfort_reasons=cr.reasons,
            route_group=group.name,
        )

        if cr.is_comfortable:
            if best_price is None or price < best_price:
                best_price = price

    if best_price is not None:
        cand = alert_engine.evaluate(
            route_key, origin, destination, depart_date, return_date,
            best_price, currency or "USD", previous_price,
            prev_row["airline"] if prev_row else None,
            prev_row["departs_at"] if prev_row else None,
        )
        if cand:
            candidates.append(cand)

    return api_calls, candidates, best_price


def _sync_tracked_json(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    price: float,
    currency: str,
    airline: str | None,
    cabin: str,
    stops: str,
) -> None:
    """Keep FlightClaw tracked.json in sync for MCP tools."""
    tracked = load_tracked()
    route_id = f"{origin}-{destination}-{depart_date}"
    if return_date:
        route_id += f"-RT-{return_date}"

    now = datetime.now(timezone.utc).isoformat()
    entry = next((t for t in tracked if t["id"] == route_id), None)
    if not entry:
        entry = {
            "id": route_id,
            "origin": origin,
            "destination": destination,
            "date": depart_date,
            "return_date": return_date,
            "cabin": cabin,
            "stops": stops,
            "currency": currency,
            "added_at": now,
            "price_history": [],
        }
        tracked.append(entry)

    entry["price_history"].append({
        "timestamp": now,
        "best_price": price,
        "airline": airline,
    })
    entry["currency"] = currency
    save_tracked(tracked)


def run_scan(
    config_path: Path | str | None = None,
    *,
    mode: str = "watch",
    groups: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    store = PriceStore(cfg.sqlite_path())
    tracker = MetricsTracker()
    scan_id = store.new_scan_id()

    group_names = groups or list(cfg.route_groups.keys())
    tracker.start()
    store.start_scan(scan_id, mode)

    total_api = 0
    total_obs = 0
    routes = 0
    all_candidates: list[AlertCandidate] = []

    with store.connection() as conn:
        for gname in group_names:
            group = cfg.route_groups.get(gname)
            if not group:
                continue

            trip_days = group.trip_duration_days or cfg.defaults.get("trip_duration_days", 7)
            dates = group.dates or _discovery_dates(cfg.scanner.discovery_days_ahead)

            for origin in group.origins:
                for dest in group.destinations:
                    for depart in dates:
                        return_date = None
                        if trip_days and mode != "oneway":
                            d = datetime.strptime(depart, "%Y-%m-%d").date()
                            return_date = (d + timedelta(days=trip_days)).strftime("%Y-%m-%d")

                        routes += 1
                        tracker.sample_memory()

                        try:
                            api, cands, best = scan_route(
                                store, conn, scan_id,
                                origin=origin,
                                destination=dest,
                                depart_date=depart,
                                return_date=return_date,
                                cfg=cfg,
                                group=group,
                                top_n=cfg.scanner.max_api_results_per_route,
                            )
                        except Exception as e:
                            print(f"  Error {origin}->{dest} {depart}: {e}", file=sys.stderr)
                            continue

                        total_api += api
                        total_obs += api  # approx 1 obs batch per call; actual count higher
                        all_candidates.extend(cands)

                        if best is not None and not dry_run:
                            _sync_tracked_json(
                                origin, dest, depart, return_date, best,
                                cfg.defaults.get("currency", "USD"),
                                None,
                                cfg.defaults.get("cabin", "ECONOMY"),
                                group.stops,
                            )

        # Count actual observations for this scan
        row = conn.execute(
            "SELECT COUNT(*) as c FROM price_observations WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        total_obs = int(row["c"])

    metrics = tracker.finish(
        api_calls=total_api,
        routes_checked=routes,
        observations_stored=total_obs,
        store=store,
        tracked_json=ROOT / "data" / "tracked.json",
    )

    store.finish_scan(
        scan_id,
        routes_checked=routes,
        observations_stored=total_obs,
        api_calls=total_api,
        wall_seconds=metrics.wall_seconds,
        cpu_seconds=metrics.cpu_seconds,
        memory_peak_mb=metrics.memory_peak_mb,
    )

    alert_result = {}
    if not dry_run and cfg.alerts.enabled:
        engine = AlertEngine(cfg.alerts, store)
        alert_result = engine.dispatch(all_candidates)

    deleted = store.purge_old(cfg.storage.retain_days)
    store.vacuum_if_needed(cfg.storage.vacuum_interval_days)

    report = format_metrics_report(metrics, store)
    print(report)
    if alert_result:
        print(f"\nAlerts: {alert_result}")
    if deleted:
        print(f"Purged {deleted} old observations (retain {cfg.storage.retain_days}d)")

    return {
        "scan_id": scan_id,
        "metrics": metrics,
        "alerts": alert_result,
        "routes_checked": routes,
        "observations": total_obs,
    }

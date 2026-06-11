"""Comfort / schedule quality filters — reject brutal flight times."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from radar.config import ComfortConfig


def _parse_hm(value: str) -> int:
    """Parse HH:MM to minutes since midnight."""
    h, m = map(int, value.split(":"))
    return h * 60 + m


def _minutes(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return dt.hour * 60 + dt.minute


@dataclass
class ComfortResult:
    score: float
    is_comfortable: bool
    reasons: list[str]


def score_flight(
    outbound_legs: list[Any],
    return_legs: list[Any] | None,
    cfg: ComfortConfig,
) -> ComfortResult:
    """Score a flight 0–1. Penalize late-night departures, red-eyes, long layovers."""
    reasons: list[str] = []
    score = 1.0

    if not outbound_legs:
        return ComfortResult(0.0, False, ["no_legs"])

    dep_after = _parse_hm(cfg.depart_after)
    dep_before = _parse_hm(cfg.depart_before)
    ret_after = _parse_hm(cfg.return_depart_after)
    ret_before = _parse_hm(cfg.return_depart_before)
    latest_arr = _parse_hm(cfg.latest_arrival)

    first = outbound_legs[0]
    dep_min = _minutes(getattr(first, "departure_datetime", None))
    last_out = outbound_legs[-1]
    arr_min = _minutes(getattr(last_out, "arrival_datetime", None))

    if dep_min is not None:
        if dep_min < dep_after:
            score -= 0.35
            reasons.append(f"early_departure_before_{cfg.depart_after}")
        if dep_min > dep_before:
            score -= 0.45
            reasons.append(f"late_departure_after_{cfg.depart_before}")
        # Brutal red-eye band (22:00–05:59)
        if dep_min >= 22 * 60 or dep_min < 6 * 60:
            score -= 0.25
            reasons.append("red_eye_departure")

    if arr_min is not None and arr_min > latest_arr:
        score -= 0.2
        reasons.append(f"late_arrival_after_{cfg.latest_arrival}")

    # Layover checks on outbound
    for i in range(len(outbound_legs) - 1):
        a = outbound_legs[i]
        b = outbound_legs[i + 1]
        if a.arrival_datetime and b.departure_datetime:
            layover = int((b.departure_datetime - a.arrival_datetime).total_seconds() / 60)
            if layover < cfg.min_layover_minutes:
                score -= 0.3
                reasons.append("layover_too_short")
            if layover > cfg.max_layover_minutes:
                score -= 0.25
                reasons.append("layover_too_long")

    if return_legs:
        ret_first = return_legs[0]
        ret_dep = _minutes(getattr(ret_first, "departure_datetime", None))
        if ret_dep is not None:
            if ret_dep < ret_after:
                score -= 0.3
                reasons.append(f"early_return_before_{cfg.return_depart_after}")
            if ret_dep > ret_before:
                score -= 0.35
                reasons.append(f"late_return_after_{cfg.return_depart_before}")
            if ret_dep >= 22 * 60 or ret_dep < 6 * 60:
                score -= 0.2
                reasons.append("red_eye_return")

    score = max(0.0, min(1.0, score))
    comfortable = score >= cfg.min_comfort_score and not any(
        r.startswith("late_departure") or r.startswith("red_eye_departure") for r in reasons
    )
    return ComfortResult(round(score, 3), comfortable, reasons)


def flight_passes_comfort(
    outbound_legs: list[Any],
    return_legs: list[Any] | None,
    cfg: ComfortConfig,
) -> bool:
    return score_flight(outbound_legs, return_legs, cfg).is_comfortable

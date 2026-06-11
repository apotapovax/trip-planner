"""Smart email notifications with anti-spam rules."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

from radar.config import AlertsConfig, env
from radar.store import PriceStore


@dataclass
class AlertCandidate:
    route_key: str
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    price: float
    currency: str
    previous_price: float | None
    drop_usd: float
    drop_pct: float
    percentile: float | None
    airline: str | None
    departs_at: str | None
    alert_type: str  # instant | digest
    reason: str


class AlertEngine:
    def __init__(self, cfg: AlertsConfig, store: PriceStore):
        self.cfg = cfg
        self.store = store

    def _today_key(self) -> str:
        tz = ZoneInfo(self.cfg.digest_timezone)
        return datetime.now(tz).strftime("%Y-%m-%d")

    def evaluate(
        self,
        route_key: str,
        origin: str,
        destination: str,
        depart_date: str,
        return_date: str | None,
        price: float,
        currency: str,
        previous_price: float | None,
        airline: str | None,
        departs_at: str | None,
    ) -> AlertCandidate | None:
        if not self.cfg.enabled:
            return None

        drop_usd = (previous_price - price) if previous_price and previous_price > price else 0
        drop_pct = (drop_usd / previous_price * 100) if previous_price and previous_price > 0 else 0
        percentile = self.store.percentile_rank(route_key, price)

        reasons = []
        alert_type = "digest"

        if percentile is not None and percentile <= self.cfg.percentile_threshold:
            reasons.append(f"bottom {percentile:.0f}% of {self.cfg.percentile_threshold:.0f}% history")

        if drop_usd >= self.cfg.min_drop_usd and drop_pct >= self.cfg.min_drop_pct:
            reasons.append(f"drop ${drop_usd:.0f} ({drop_pct:.1f}%)")

        if not reasons:
            return None

        # Instant for big moves or new lows
        if drop_pct >= 15 or (percentile is not None and percentile <= 5):
            alert_type = "instant"
        elif drop_usd >= self.cfg.min_drop_usd * 2:
            alert_type = "instant"

        if self.store.alerts_in_cooldown(route_key, self.cfg.cooldown_hours):
            alert_type = "digest"

        if alert_type == "instant" and self.store.instant_alerts_today(self._today_key()) >= self.cfg.instant_max_per_day:
            alert_type = "digest"

        return AlertCandidate(
            route_key=route_key,
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            price=price,
            currency=currency,
            previous_price=previous_price,
            drop_usd=drop_usd,
            drop_pct=drop_pct,
            percentile=percentile,
            airline=airline,
            departs_at=departs_at,
            alert_type=alert_type,
            reason="; ".join(reasons),
        )

    def send_email(self, subject: str, body: str, html: str | None = None) -> bool:
        host = env("SMTP_HOST")
        user = env("SMTP_USER")
        password = env("SMTP_PASS")
        to_addr = env("ALERT_EMAIL_TO")
        from_addr = env("SMTP_FROM", user)

        if not all([host, user, password, to_addr]):
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))

        port = int(env("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True

    def dispatch(self, candidates: list[AlertCandidate], *, force_digest: bool = False) -> dict[str, Any]:
        instant = [c for c in candidates if c.alert_type == "instant" and not force_digest]
        digest = [c for c in candidates if c.alert_type == "digest" or force_digest]

        sent = {"instant": 0, "digest": 0, "skipped_no_smtp": 0}

        for c in instant:
            subject = f"✈ Deal: {c.origin}→{c.destination} {c.depart_date} ${c.price:.0f}"
            body = self._format_alert(c)
            if self.send_email(subject, body):
                self.store.record_alert(c.route_key, "instant", c.price, c.currency, subject, body)
                sent["instant"] += 1
            else:
                sent["skipped_no_smtp"] += 1

        if digest:
            subject = f"Flight radar digest — {len(digest)} update(s)"
            body = "\n\n---\n\n".join(self._format_alert(c) for c in digest)
            if self.send_email(subject, body):
                for c in digest:
                    self.store.record_alert(c.route_key, "digest", c.price, c.currency, subject, self._format_alert(c))
                sent["digest"] += 1
            else:
                sent["skipped_no_smtp"] += 1

        return sent

    @staticmethod
    def _format_alert(c: AlertCandidate) -> str:
        rt = f" return {c.return_date}" if c.return_date else ""
        prev = f" (was ${c.previous_price:.0f})" if c.previous_price else ""
        pct = f", {c.percentile:.0f}th percentile" if c.percentile is not None else ""
        dep = f" departs {c.departs_at}" if c.departs_at else ""
        return (
            f"{c.origin} → {c.destination} on {c.depart_date}{rt}\n"
            f"  ${c.price:.0f} {c.currency}{prev}{pct}\n"
            f"  {c.airline or '?'}{dep}\n"
            f"  {c.reason}"
        )

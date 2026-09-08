from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .model import CollectorResult, MetricSample


def utc_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@dataclass(slots=True)
class CollectorStatus:
    name: str
    enabled: bool = True
    healthy: bool = False
    attempts: int = 0
    errors: int = 0
    last_attempt: float | None = None
    last_success: float | None = None
    duration_seconds: float | None = None
    metric_count: int = 0
    error: str | None = None

    def ui_dict(self, now: float) -> dict:
        stale_seconds = None
        if self.last_success is not None:
            stale_seconds = max(0.0, now - self.last_success)
        return {
            "name": self.name,
            "enabled": self.enabled,
            "healthy": self.healthy,
            "attempts": self.attempts,
            "errors": self.errors,
            "last_attempt": utc_iso(self.last_attempt),
            "last_success": utc_iso(self.last_success),
            "duration_ms": (
                round(self.duration_seconds * 1000)
                if self.duration_seconds is not None
                else None
            ),
            "metric_count": self.metric_count,
            "error": self.error,
            "stale_seconds": stale_seconds,
        }


class MetricsState:
    """Thread-safe cache shared by background collection and HTTP scrapes."""

    def __init__(self, plugin_version: str):
        self.plugin_version = plugin_version
        self._lock = threading.RLock()
        self._metrics: dict[str, list[MetricSample]] = {}
        self._status: dict[str, CollectorStatus] = {}
        self._running = False
        self._started_at = time.time()
        self._last_collection_started: float | None = None
        self._last_collection_completed: float | None = None
        self._collection_duration_seconds: float | None = None
        self._scrapes_total = 0
        self._scrape_errors_total = 0
        self._last_fatal_error: str | None = None

    def set_running(self, value: bool) -> None:
        with self._lock:
            self._running = bool(value)

    def set_enabled_collectors(self, names: Iterable[str]) -> None:
        names = set(names)
        with self._lock:
            for name in names:
                status = self._status.setdefault(name, CollectorStatus(name=name))
                status.enabled = True
            for name, status in self._status.items():
                if name not in names:
                    status.enabled = False

    def mark_collection_started(self) -> float:
        now = time.time()
        with self._lock:
            self._last_collection_started = now
        return now

    def mark_collection_completed(self, started: float) -> None:
        completed = time.time()
        with self._lock:
            self._last_collection_completed = completed
            self._collection_duration_seconds = max(0.0, completed - started)
            self._last_fatal_error = None

    def mark_collection_fatal_error(self, error: str) -> None:
        with self._lock:
            self._last_fatal_error = error

    def update_collector(self, result: CollectorResult) -> None:
        now = time.time()
        with self._lock:
            status = self._status.setdefault(result.name, CollectorStatus(name=result.name))
            status.enabled = True
            status.attempts += 1
            status.last_attempt = now
            status.duration_seconds = result.duration_seconds

            if result.ok:
                self._metrics[result.name] = list(result.metrics)
                status.healthy = True
                status.last_success = now
                status.metric_count = len(result.metrics)
                status.error = None
                return

            status.healthy = False
            status.errors += 1
            status.error = result.error or "collector failed"

            # Preserve cached values after a transient failure, but replace any
            # explicit liveness/connectivity samples supplied by the collector.
            if result.failure_metrics:
                previous = list(self._metrics.get(result.name, []))
                replacement_keys = {
                    (sample.name, tuple(sorted(sample.labels.items())))
                    for sample in result.failure_metrics
                }
                previous = [
                    sample
                    for sample in previous
                    if (sample.name, tuple(sorted(sample.labels.items())))
                    not in replacement_keys
                ]
                previous.extend(result.failure_metrics)
                self._metrics[result.name] = previous
                status.metric_count = len(previous)

    def note_scrape(self) -> None:
        with self._lock:
            self._scrapes_total += 1

    def note_scrape_error(self) -> None:
        with self._lock:
            self._scrape_errors_total += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "started_at": self._started_at,
                "last_collection_started": self._last_collection_started,
                "last_collection_completed": self._last_collection_completed,
                "collection_duration_seconds": self._collection_duration_seconds,
                "scrapes_total": self._scrapes_total,
                "scrape_errors_total": self._scrape_errors_total,
                "last_fatal_error": self._last_fatal_error,
                "metrics": {
                    name: list(samples) for name, samples in self._metrics.items()
                },
                "status": {
                    name: CollectorStatus(
                        name=status.name,
                        enabled=status.enabled,
                        healthy=status.healthy,
                        attempts=status.attempts,
                        errors=status.errors,
                        last_attempt=status.last_attempt,
                        last_success=status.last_success,
                        duration_seconds=status.duration_seconds,
                        metric_count=status.metric_count,
                        error=status.error,
                    )
                    for name, status in self._status.items()
                },
            }

    def ui_snapshot(self, *, next_collection_at: float | None = None) -> dict:
        snap = self.snapshot()
        now = time.time()
        statuses = snap["status"]
        enabled = [status for status in statuses.values() if status.enabled]
        healthy = [status for status in enabled if status.healthy]
        metrics = [
            sample
            for collector_samples in snap["metrics"].values()
            for sample in collector_samples
        ]
        metrics.sort(key=lambda sample: (sample.name, sorted(sample.labels.items())))

        if not snap["running"]:
            health = "stopped"
        elif snap["last_collection_completed"] is None:
            health = "starting"
        elif enabled and len(healthy) < len(enabled):
            health = "degraded"
        else:
            health = "healthy"

        return {
            "status": health,
            "running": snap["running"],
            "started_at": utc_iso(snap["started_at"]),
            "uptime_seconds": max(0.0, now - snap["started_at"]),
            "last_collection_started": utc_iso(snap["last_collection_started"]),
            "last_collection_completed": utc_iso(snap["last_collection_completed"]),
            "next_collection_at": utc_iso(next_collection_at),
            "collection_duration_ms": (
                round(snap["collection_duration_seconds"] * 1000)
                if snap["collection_duration_seconds"] is not None
                else None
            ),
            "metric_count": len(metrics),
            "metric_family_count": len({sample.name for sample in metrics}),
            "scrapes_total": snap["scrapes_total"],
            "scrape_errors_total": snap["scrape_errors_total"],
            "last_error": snap["last_fatal_error"],
            "collector_count": len(enabled),
            "collectors_healthy": len(healthy),
            "collectors": [statuses[name].ui_dict(now) for name in sorted(statuses)],
            "metrics": [sample.ui_dict() for sample in metrics[:300]],
        }

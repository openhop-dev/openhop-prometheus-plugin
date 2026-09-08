from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..model import CollectorResult, MetricSample


class BaseCollector(ABC):
    name: str

    @abstractmethod
    def collect(self) -> list[MetricSample]:
        raise NotImplementedError

    def failure_metrics(self) -> list[MetricSample]:
        return []

    def collect_safe(self) -> CollectorResult:
        started = time.monotonic()
        try:
            metrics = self.collect()
            return CollectorResult(
                name=self.name,
                ok=True,
                metrics=metrics,
                duration_seconds=max(0.0, time.monotonic() - started),
            )
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                ok=False,
                metrics=[],
                duration_seconds=max(0.0, time.monotonic() - started),
                error=str(exc),
                failure_metrics=self.failure_metrics(),
            )

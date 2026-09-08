from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Mapping

_METRIC_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_ALLOWED_KINDS = {"gauge", "counter", "info"}


@dataclass(frozen=True, slots=True)
class MetricSample:
    name: str
    help: str
    kind: str
    value: float = 1.0
    labels: Mapping[str, str] = field(default_factory=dict)
    collector: str = ""

    def __post_init__(self) -> None:
        if not _METRIC_RE.fullmatch(self.name):
            raise ValueError(f"invalid Prometheus metric name: {self.name!r}")
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError(f"unsupported metric kind: {self.kind!r}")
        if self.kind == "counter" and not self.name.endswith("_total"):
            raise ValueError(f"counter metric must end in _total: {self.name}")
        if self.kind == "info" and not self.name.endswith("_info"):
            raise ValueError(f"info metric must end in _info: {self.name}")
        if not isinstance(self.value, (int, float)) or not math.isfinite(float(self.value)):
            raise ValueError(f"metric value must be finite: {self.name}")
        for key, value in self.labels.items():
            if not _LABEL_RE.fullmatch(str(key)):
                raise ValueError(f"invalid Prometheus label name: {key!r}")
            if not isinstance(value, str):
                raise ValueError(f"label values must be strings: {key!r}")

    def ui_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.kind,
            "value": float(self.value),
            "labels": dict(self.labels),
            "collector": self.collector,
        }


@dataclass(slots=True)
class CollectorResult:
    name: str
    ok: bool
    metrics: list[MetricSample]
    duration_seconds: float
    error: str | None = None
    failure_metrics: list[MetricSample] = field(default_factory=list)

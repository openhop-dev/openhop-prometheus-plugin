from __future__ import annotations

import os
import time

import psutil

from ..model import MetricSample
from .base import BaseCollector


class PluginCollector(BaseCollector):
    name = "plugin"

    def __init__(self):
        self.process = psutil.Process(os.getpid())

    def collect(self) -> list[MetricSample]:
        process = self.process
        memory = process.memory_info()
        cpu = process.cpu_times()
        metrics = [
            MetricSample(
                "openhop_prometheus_process_uptime_seconds",
                "Uptime of the Prometheus plugin process in seconds.",
                "gauge",
                max(0.0, time.time() - process.create_time()),
                collector=self.name,
            ),
            MetricSample(
                "openhop_prometheus_process_resident_memory_bytes",
                "Resident memory used by the Prometheus plugin process.",
                "gauge",
                memory.rss,
                collector=self.name,
            ),
            MetricSample(
                "openhop_prometheus_process_cpu_seconds_total",
                "CPU time consumed by the Prometheus plugin process.",
                "counter",
                cpu.user + cpu.system,
                collector=self.name,
            ),
            MetricSample(
                "openhop_prometheus_process_threads",
                "Number of threads in the Prometheus plugin process.",
                "gauge",
                process.num_threads(),
                collector=self.name,
            ),
        ]
        if hasattr(process, "num_fds"):
            try:
                metrics.append(
                    MetricSample(
                        "openhop_prometheus_process_open_fds",
                        "Number of open file descriptors in the Prometheus plugin process.",
                        "gauge",
                        process.num_fds(),
                        collector=self.name,
                    )
                )
            except (psutil.Error, OSError):
                pass
        return metrics

from __future__ import annotations

import time
from collections import defaultdict

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily

from .state import MetricsState


class SnapshotCollector:
    """Prometheus custom collector over the immutable state snapshots."""

    def __init__(self, state: MetricsState):
        self.state = state

    def collect(self):
        snap = self.state.snapshot()
        now = time.time()

        info = InfoMetricFamily(
            "openhop_prometheus_plugin",
            "openHop Prometheus plugin build information.",
        )
        info.add_metric([], {"version": self.state.plugin_version})
        yield info

        up = GaugeMetricFamily(
            "openhop_prometheus_up",
            "Whether the openHop Prometheus plugin runtime is running.",
        )
        up.add_metric([], 1.0 if snap["running"] else 0.0)
        yield up

        scrapes = CounterMetricFamily(
            "openhop_prometheus_scrapes",
            "Total Prometheus metrics scrapes served by this plugin.",
        )
        scrapes.add_metric([], snap["scrapes_total"])
        yield scrapes

        scrape_errors = CounterMetricFamily(
            "openhop_prometheus_scrape_errors",
            "Total errors while generating Prometheus scrape responses.",
        )
        scrape_errors.add_metric([], snap["scrape_errors_total"])
        yield scrape_errors

        last_collection = GaugeMetricFamily(
            "openhop_prometheus_last_collection_timestamp_seconds",
            "Unix timestamp of the most recently completed collection cycle.",
        )
        last_collection.add_metric([], snap["last_collection_completed"] or 0.0)
        yield last_collection

        duration = GaugeMetricFamily(
            "openhop_prometheus_collection_duration_seconds",
            "Duration in seconds of the most recently completed collection cycle.",
        )
        duration.add_metric([], snap["collection_duration_seconds"] or 0.0)
        yield duration

        exposed = GaugeMetricFamily(
            "openhop_prometheus_metrics_exposed",
            "Number of cached collector metric samples currently exposed.",
        )
        exposed.add_metric([], sum(len(items) for items in snap["metrics"].values()))
        yield exposed

        collector_up = GaugeMetricFamily(
            "openhop_prometheus_collector_up",
            "Whether the named collector succeeded on its most recent attempt.",
            labels=["collector"],
        )
        collector_last = GaugeMetricFamily(
            "openhop_prometheus_collector_last_success_timestamp_seconds",
            "Unix timestamp of the named collector's most recent successful collection.",
            labels=["collector"],
        )
        collector_duration = GaugeMetricFamily(
            "openhop_prometheus_collector_duration_seconds",
            "Duration of the named collector's most recent attempt.",
            labels=["collector"],
        )
        collector_errors = CounterMetricFamily(
            "openhop_prometheus_collector_errors",
            "Total collection errors for the named collector since plugin start.",
            labels=["collector"],
        )
        collector_stale = GaugeMetricFamily(
            "openhop_prometheus_collector_stale_seconds",
            "Seconds since the named collector last succeeded; zero before first success.",
            labels=["collector"],
        )
        collector_metrics = GaugeMetricFamily(
            "openhop_prometheus_collector_metrics",
            "Number of cached metric samples from the named collector.",
            labels=["collector"],
        )

        for name in sorted(snap["status"]):
            status = snap["status"][name]
            if not status.enabled:
                continue
            labels = [name]
            collector_up.add_metric(labels, 1.0 if status.healthy else 0.0)
            collector_last.add_metric(labels, status.last_success or 0.0)
            collector_duration.add_metric(labels, status.duration_seconds or 0.0)
            collector_errors.add_metric(labels, status.errors)
            collector_stale.add_metric(
                labels,
                max(0.0, now - status.last_success) if status.last_success else 0.0,
            )
            collector_metrics.add_metric(labels, status.metric_count)

        yield collector_up
        yield collector_last
        yield collector_duration
        yield collector_errors
        yield collector_stale
        yield collector_metrics

        # Group cached samples into metric families. Collector implementations
        # are validated to use a stable label schema per metric name.
        groups: dict[tuple, list] = defaultdict(list)
        for samples in snap["metrics"].values():
            for sample in samples:
                label_names = tuple(sorted(sample.labels))
                groups[(sample.name, sample.kind, sample.help, label_names)].append(sample)

        for (name, kind, help_text, label_names), samples in sorted(groups.items()):
            if kind == "info":
                base = name[:-5] if name.endswith("_info") else name
                family = InfoMetricFamily(base, help_text)
                # Information metrics in this plugin have one sample per family.
                for sample in samples:
                    family.add_metric([], dict(sample.labels))
                yield family
                continue

            if kind == "counter":
                base = name[:-6] if name.endswith("_total") else name
                family = CounterMetricFamily(base, help_text, labels=list(label_names))
            else:
                family = GaugeMetricFamily(name, help_text, labels=list(label_names))

            for sample in samples:
                family.add_metric(
                    [sample.labels[label] for label in label_names],
                    float(sample.value),
                )
            yield family

from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.state import MetricsState


def good_result(value=10):
    return CollectorResult(
        name="repeater",
        ok=True,
        metrics=[
            MetricSample("openhop_repeater_up", "up", "gauge", 1, collector="repeater"),
            MetricSample("openhop_repeater_rx_packets_total", "rx", "counter", value, collector="repeater"),
        ],
        duration_seconds=0.1,
    )


def test_failure_keeps_cached_metrics_and_marks_up_zero():
    state = MetricsState("0.1.0")
    state.update_collector(good_result())
    state.update_collector(CollectorResult(
        name="repeater",
        ok=False,
        metrics=[],
        duration_seconds=0.2,
        error="offline",
        failure_metrics=[MetricSample("openhop_repeater_up", "up", "gauge", 0, collector="repeater")],
    ))
    snap = state.snapshot()
    metrics = {metric.name: metric for metric in snap["metrics"]["repeater"]}
    assert metrics["openhop_repeater_up"].value == 0
    assert metrics["openhop_repeater_rx_packets_total"].value == 10
    assert snap["status"]["repeater"].healthy is False
    assert snap["status"]["repeater"].errors == 1


def test_ui_status_becomes_degraded_on_collector_failure():
    state = MetricsState("0.1.0")
    state.set_running(True)
    state.set_enabled_collectors(["repeater"])
    started = state.mark_collection_started()
    state.update_collector(good_result())
    state.mark_collection_completed(started)
    assert state.ui_snapshot()["status"] == "healthy"
    state.update_collector(CollectorResult("repeater", False, [], 0.1, error="fail"))
    assert state.ui_snapshot()["status"] == "degraded"

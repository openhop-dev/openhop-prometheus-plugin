import logging
from dataclasses import replace

from openhop_prometheus_plugin.collectors.base import BaseCollector
from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.diagnostics import FailureLog
from openhop_prometheus_plugin.manager import CollectionManager
from openhop_prometheus_plugin.state import MetricsState


def test_healthy_collections_are_quiet_at_default_log_level(tmp_path, caplog):
    config = replace(load_config(tmp_path), repeater_enabled=False)
    manager = CollectionManager(MetricsState("test"))
    with caplog.at_level(logging.INFO, logger="openhop.prometheus"):
        for _ in range(3):
            manager.collect_once(config)
    assert not caplog.records


def test_repeated_collector_failures_are_bounded_and_recovery_is_visible(
    tmp_path, caplog, monkeypatch
):
    class ControlledCollector(BaseCollector):
        name = "repeater"
        failing = True

        def collect(self):
            if self.failing:
                raise RuntimeError("offline")
            return []

    collector = ControlledCollector()
    config = load_config(tmp_path)
    manager = CollectionManager(MetricsState("test"))
    monkeypatch.setattr(manager, "build_collectors", lambda _: [collector])
    with caplog.at_level(logging.INFO, logger="openhop.prometheus"):
        for _ in range(20):
            manager.collect_once(config)
        assert len(caplog.records) == 1
        assert "offline" in caplog.records[0].getMessage()
        collector.failing = False
        manager.collect_once(config)
        assert "recovered" in caplog.records[-1].getMessage()
        count = len(caplog.records)
        manager.collect_once(config)
        assert len(caplog.records) == count
        collector.failing = True
        manager.collect_once(config)
        assert len(caplog.records) == count + 1


def test_failure_reminders_use_monotonic_clock_and_sanitize_secrets(caplog):
    now = [0.0]
    failures = FailureLog(clock=lambda: now[0])
    with caplog.at_level(logging.INFO, logger="openhop.prometheus"):
        for index in range(20):
            failures.report(
                "cycle",
                f"changing error {index} secret-value\n" + "x" * 2000,
                secret="secret-value",
            )
        assert len(caplog.records) == 1
        assert "secret-value" not in caplog.text
        assert "[redacted]" in caplog.text
        assert len(caplog.records[0].getMessage()) < 600
        now[0] = 299.9
        failures.report("cycle", "still failing")
        assert len(caplog.records) == 1
        now[0] = 300
        failures.report("cycle", "latest error")
        assert len(caplog.records) == 2
        assert "suppressed=20" in caplog.records[-1].getMessage()
        failures.recovered("cycle")
        assert "recovered" in caplog.records[-1].getMessage()
        failures.recovered("cycle")
        assert len(caplog.records) == 3


def test_failure_log_retained_sources_are_bounded(caplog):
    failures = FailureLog()
    with caplog.at_level(logging.CRITICAL, logger="openhop.prometheus"):
        for index in range(100):
            failures.report(str(index), "offline")
    assert len(failures._failures) == 32

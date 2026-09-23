from prometheus_client import CollectorRegistry, generate_latest

from openhop_prometheus_plugin.metrics import SnapshotCollector
from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.state import MetricsState


def test_prometheus_output_has_help_type_and_counter_suffix():
    state = MetricsState("1.0.0")
    state.set_running(True)
    state.update_collector(CollectorResult(
        "repeater",
        True,
        [
            MetricSample("openhop_repeater_rx_packets_total", "RX packets.", "counter", 42, collector="repeater"),
            MetricSample("openhop_repeater_noise_floor_dbm", "Noise floor.", "gauge", -102, collector="repeater"),
        ],
        0.1,
    ))
    registry = CollectorRegistry(auto_describe=False)
    registry.register(SnapshotCollector(state))
    text = generate_latest(registry).decode()
    assert "# HELP openhop_repeater_rx_packets_total RX packets." in text
    assert "# TYPE openhop_repeater_rx_packets_total counter" in text
    assert "openhop_repeater_rx_packets_total 42.0" in text
    assert "# TYPE openhop_repeater_noise_floor_dbm gauge" in text
    assert 'openhop_prometheus_plugin_info{version="1.0.0"} 1.0' in text


def test_dump_metrics_stdout_is_pure_exposition(tmp_path, monkeypatch, capsys):
    from prometheus_client.parser import text_string_to_metric_families
    from openhop_prometheus_plugin import main as main_module

    (tmp_path / "config.json").write_text(
        '{"repeater_enabled": false}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENHOP_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["openhop-prometheus", "--dump-metrics"])

    assert main_module.main() == 0
    captured = capsys.readouterr()
    families = list(text_string_to_metric_families(captured.out))
    assert families
    assert "openhop_prometheus_up" in captured.out

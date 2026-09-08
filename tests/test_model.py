import pytest

from openhop_prometheus_plugin.model import MetricSample


def test_counter_requires_total_suffix():
    with pytest.raises(ValueError):
        MetricSample("bad_counter", "help", "counter", 1)


def test_info_requires_info_suffix():
    with pytest.raises(ValueError):
        MetricSample("bad", "help", "info", labels={"version": "1"})


def test_valid_metric():
    metric = MetricSample("openhop_radio_rx_packets_total", "help", "counter", 12, labels={"interface": "radio"})
    assert metric.ui_dict()["value"] == 12

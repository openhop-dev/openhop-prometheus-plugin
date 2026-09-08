import json
from dataclasses import replace

from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.runtime_snapshot import publish_runtime_snapshot
from openhop_prometheus_plugin.state import MetricsState


def test_runtime_snapshot_preserves_token_but_never_exposes_it(tmp_path):
    secret = "super-secret-token"
    (tmp_path / "config.json").write_text(json.dumps({"repeater_api_token": secret}))
    config = load_config(tmp_path)
    state = MetricsState("0.1.0")
    state.set_running(True)
    state.update_collector(CollectorResult(
        "repeater", True,
        [MetricSample("openhop_repeater_up", "up", "gauge", 1, collector="repeater")],
        0.1,
    ))
    runtime = publish_runtime_snapshot(tmp_path, config, state, None)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["repeater_api_token"] == secret
    assert secret not in json.dumps(runtime)
    assert runtime["repeater"]["api_token_configured"] is True

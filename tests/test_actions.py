import json
from dataclasses import replace

from openhop_prometheus_plugin.actions import action_snapshot, process_actions
from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.http_server import MetricsHTTPServer
from openhop_prometheus_plugin.manager import CollectionManager
from openhop_prometheus_plugin.state import MetricsState


def test_endpoint_action_runs_once(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"test_endpoint_request_id": "test-1"}))
    config = replace(load_config(tmp_path), port=0)
    state = MetricsState("0.1.0")
    state.set_running(True)
    manager = CollectionManager(state)
    server = MetricsHTTPServer(config, state)
    server.start()
    try:
        assert process_actions(tmp_path, config, manager, server) is True
        snapshot = action_snapshot(tmp_path)
        assert snapshot["endpoint"]["status"] == "ok"
        assert process_actions(tmp_path, config, manager, server) is False
    finally:
        server.stop()

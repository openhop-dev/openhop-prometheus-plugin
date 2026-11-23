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


def test_completed_action_ids_survive_runtime_restart_without_periodic_writes(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"test_endpoint_request_id": "persisted-id"}))
    config = replace(load_config(tmp_path), port=0, repeater_enabled=False)
    state = MetricsState("test")
    state.set_running(True)
    with_manager = CollectionManager(state)
    server = MetricsHTTPServer(config, state)
    server.start()
    try:
        assert process_actions(tmp_path, config, with_manager, server)
        ledger = tmp_path / "action-state.json"
        original = ledger.read_bytes()
        before = ledger.stat()
        # Reconstruct the actual manager/server-facing path as after an upgrade.
        restarted_manager = CollectionManager(MetricsState("new-instance"))
        for _ in range(5):
            assert process_actions(tmp_path, config, restarted_manager, server) is False
        assert ledger.read_bytes() == original
        assert (ledger.stat().st_ino, ledger.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert state.snapshot()["scrapes_total"] == 1
        changed = replace(config, test_endpoint_request_id="new-user-request")
        assert process_actions(tmp_path, changed, restarted_manager, server)
        assert action_snapshot(tmp_path)["endpoint"]["request_id"] == "new-user-request"
        assert state.snapshot()["scrapes_total"] == 2
    finally:
        server.stop()

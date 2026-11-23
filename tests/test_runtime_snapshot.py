import json
from dataclasses import replace

from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.runtime_snapshot import publish_runtime_snapshot
from openhop_prometheus_plugin.runtime_store import MAX_SNAPSHOT_BYTES, RuntimePublisher
from openhop_prometheus_plugin.state import MetricsState


def test_publications_never_replace_or_edit_settings(tmp_path):
    path = tmp_path / "config.json"
    original = json.dumps(
        {
            "repeater_api_token": "keep-this-token",
            "port": 9112,
            "unknown_setting": {"keep": True},
            "_runtime": {"status": "legacy"},
        }
    ).encode()
    path.write_bytes(original)
    before = path.stat()
    config = load_config(tmp_path)
    state = MetricsState("1.0.0")
    state.set_running(True)
    for error in (None, "collection failed", None):
        publish_runtime_snapshot(tmp_path, config, state, None, last_error=error)
    state.set_running(False)
    publish_runtime_snapshot(tmp_path, config, state, None)
    assert path.read_bytes() == original
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_runtime_snapshot_preserves_token_but_never_exposes_it(tmp_path):
    secret = "super-secret-token"
    (tmp_path / "config.json").write_text(json.dumps({"repeater_api_token": secret}))
    config = load_config(tmp_path)
    state = MetricsState("0.1.0")
    state.set_running(True)
    state.update_collector(
        CollectorResult(
            "repeater",
            True,
            [MetricSample("openhop_repeater_up", "up", "gauge", 1, collector="repeater")],
            0.1,
        )
    )
    runtime = publish_runtime_snapshot(tmp_path, config, state, None)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["repeater_api_token"] == secret
    assert secret not in json.dumps(runtime)
    assert runtime["repeater"]["api_token_configured"] is True


def test_newer_and_malformed_settings_are_never_read_or_overwritten(tmp_path):
    config = load_config(tmp_path)
    state = MetricsState("test")
    path = tmp_path / "config.json"
    with RuntimePublisher(tmp_path) as publisher:
        for content in (b'{"port": 9200, "new_user_edit": true}', b"{malformed-on-purpose"):
            path.write_bytes(content)
            before = path.stat()
            publish_runtime_snapshot(tmp_path, config, state, None, publisher=publisher)
            assert path.read_bytes() == content
            after = path.stat()
            assert (after.st_ino, after.st_mtime_ns, after.st_size) == (
                before.st_ino,
                before.st_mtime_ns,
                before.st_size,
            )


def test_snapshot_bounds_and_redacts_collector_and_action_errors(tmp_path):
    secret = "synthetic-private-api-token"
    config = replace(load_config(tmp_path), repeater_api_token=secret)
    state = MetricsState("test")
    state.set_running(True)
    state.mark_collection_fatal_error("connection failed: " + secret)
    state.update_collector(CollectorResult("repeater", False, [], 0.1, error=secret))
    (tmp_path / "action-state.json").write_text(
        json.dumps(
            {
                "endpoint": {"status": "error", "message": secret + "x" * 2000},
            }
        )
    )
    runtime = publish_runtime_snapshot(tmp_path, config, state, None)
    assert secret not in json.dumps(runtime)
    assert len(runtime["actions"]["endpoint"]["message"]) <= 512
    assert runtime["status"] == "error"


def test_large_metric_preview_is_trimmed_but_counts_remain_accurate(tmp_path):
    config = load_config(tmp_path)
    state = MetricsState("test")
    state.update_collector(
        CollectorResult(
            "repeater",
            True,
            [
                MetricSample(
                    "large_metric",
                    "x" * 2000,
                    "gauge",
                    index,
                    labels={f"label_{label}": "y" * 2000 for label in range(32)},
                    collector="repeater",
                )
                for index in range(300)
            ],
            0.1,
        )
    )
    runtime = publish_runtime_snapshot(tmp_path, config, state, None)
    assert len((tmp_path / "runtime.json").read_bytes()) <= MAX_SNAPSHOT_BYTES
    assert runtime["metric_count"] == 300
    assert runtime["metrics_truncated"] is True
    assert 0 < len(runtime["metrics"]) < 300

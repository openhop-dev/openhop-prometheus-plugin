import json

import pytest

from openhop_prometheus_plugin.config import load_config


def test_defaults_are_safe(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.port == 9109
    assert cfg.metrics_path == "/metrics"
    assert cfg.repeater_enabled is True
    assert cfg.external_bind is False
    assert not hasattr(cfg, "meshcore_enabled")
    assert not hasattr(cfg, "system_enabled")


def test_external_bind_detected(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"bind_host": "0.0.0.0"}))
    assert load_config(tmp_path).external_bind is True


def test_rejects_unsafe_metrics_path(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"metrics_path": "/foo/../metrics"}))
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_rejects_health_path_collision(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"metrics_path": "/healthz"}))
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_timeout_must_be_less_than_interval(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"collection_interval_seconds": 10, "collection_timeout_seconds": 10}))
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_runtime_snapshot_is_ignored_by_config_parser(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"_runtime": {"port": 1}, "port": 9110}))
    assert load_config(tmp_path).port == 9110


def test_removed_v01_meshcore_and_system_keys_are_ignored_on_upgrade(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "meshcore_enabled": True,
        "meshcore_host": "10.0.0.2",
        "system_enabled": True,
        "port": 9111,
    }))
    cfg = load_config(tmp_path)
    assert cfg.port == 9111
    assert not hasattr(cfg, "meshcore_host")

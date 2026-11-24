"""Effective log-level transitions use real config parsing and logging state."""
import json
import logging

import pytest

from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.main import _apply_log_level


@pytest.fixture(autouse=True)
def restore_root_log_level():
    root = logging.getLogger()
    original = root.level
    yield
    root.setLevel(original)


@pytest.mark.parametrize(
    "inherit_settings",
    [{}, {"log_level": ""}, {"log_level": None}, {"log_level": "   "}],
    ids=["missing", "empty", "null", "whitespace"],
)
def test_live_reload_inherit_restores_environment(tmp_path, monkeypatch, inherit_settings):
    monkeypatch.setenv("OPENHOP_PROMETHEUS_LOG_LEVEL", "ERROR")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_level": "DEBUG"}))
    _apply_log_level(load_config(tmp_path))
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    # An intentional operator edit removes/clears the override. Applying it must
    # restore inheritance without mutating the newly saved file.
    path.write_text(json.dumps(inherit_settings))
    before_bytes = path.read_bytes()
    before = path.stat()
    config = load_config(tmp_path)
    assert config.log_level == ""
    _apply_log_level(config)
    after = path.stat()
    assert logging.getLogger().getEffectiveLevel() == logging.ERROR
    assert path.read_bytes() == before_bytes
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size) == (
        before.st_ino, before.st_mtime_ns, before.st_ctime_ns, before.st_size
    )


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_explicit_levels_override_environment_and_normalize(tmp_path, monkeypatch, level):
    monkeypatch.setenv("OPENHOP_PROMETHEUS_LOG_LEVEL", "CRITICAL" if level == "DEBUG" else "DEBUG")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_level": f"  {level.lower()}  "}))
    before = path.read_bytes()
    config = load_config(tmp_path)
    assert config.log_level == level
    _apply_log_level(config)
    assert logging.getLogger().getEffectiveLevel() == getattr(logging, level)
    assert path.read_bytes() == before


@pytest.mark.parametrize("environment", [None, "", "invalid-level"])
def test_inherit_falls_back_to_info_after_override(tmp_path, monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("OPENHOP_PROMETHEUS_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("OPENHOP_PROMETHEUS_LOG_LEVEL", environment)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_level": "DEBUG"}))
    _apply_log_level(load_config(tmp_path))
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    path.write_text("{}")
    _apply_log_level(load_config(tmp_path))
    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_inherited_environment_level_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENHOP_PROMETHEUS_LOG_LEVEL", "warning")
    _apply_log_level(load_config(tmp_path))
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize("value", ["VERBOSE", "NOTSET", "WARN", "debug,info", 10])
def test_invalid_config_level_is_rejected_without_writing(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_level": value}))
    before_bytes = path.read_bytes()
    before = path.stat()
    with pytest.raises(ValueError, match="log_level must be one of"):
        load_config(tmp_path)
    after = path.stat()
    assert path.read_bytes() == before_bytes
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

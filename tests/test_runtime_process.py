"""Real CLI process, HTTP and filesystem acceptance (no radio / Repeater calls)."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

import openhop_prometheus_plugin


@contextmanager
def _process(tmp_path, raw, code=None):
    path = tmp_path / "config.json"
    if not path.exists():
        path.write_text(json.dumps(raw))
    env = dict(os.environ, OPENHOP_PLUGIN_DATA=str(tmp_path))
    env["PYTHONPATH"] = str(Path(openhop_prometheus_plugin.__file__).resolve().parents[1])
    args = [sys.executable, "-u"]
    args += ["-c", code] if code else ["-m", "openhop_prometheus_plugin.main"]
    with (tmp_path / "process.log").open("w+") as output:
        process = subprocess.Popen(
            args, env=env, cwd=tmp_path, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            yield process, output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _persistent_identity(path):
    value = path.stat()
    return value.st_ino, value.st_mtime_ns, value.st_ctime_ns, value.st_size


def _wait_health(port, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return json.load(response)
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError("HTTP endpoint did not become ready")


def _wait_snapshot(path, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            last = json.loads(path.read_text())
            if predicate(last):
                return last
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"runtime did not reach expected state; last status={last.get('status')}")


def test_cli_heartbeat_scrapes_and_stop_never_rewrite_persistent_state(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "config.json"
    raw = json.dumps(
        {
            "bind_host": "127.0.0.1",
            "port": port,
            "collection_interval_seconds": 60,
            "collection_timeout_seconds": 0.5,
            "repeater_enabled": False,
            "repeater_api_token": "fixture-token",
            "_runtime": {"status": "old"},
            "unknown_setting": "preserve",
        }
    ).encode()
    config.write_bytes(raw)
    original = config.stat()
    env = dict(os.environ, OPENHOP_PLUGIN_DATA=str(tmp_path))
    env["PYTHONPATH"] = str(Path(openhop_prometheus_plugin.__file__).resolve().parents[1])
    runtime = tmp_path / "runtime.json"
    log = tmp_path / "process.log"
    with log.open("w+") as output:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "openhop_prometheus_plugin.main"],
            env=env,
            cwd=tmp_path,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            first = _wait_snapshot(runtime, lambda value: value.get("status") == "healthy")
            link = runtime.lstat()
            second = _wait_snapshot(
                runtime,
                lambda value: (
                    value.get("generated_at") != first["generated_at"]
                    and value.get("status") == "healthy"
                ),
            )
            assert second["collectors"][0]["attempts"] == first["collectors"][0]["attempts"] == 1
            assert second["expires_at"] > second["generated_at"]
            for _ in range(5):
                with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
                    assert b"openhop_prometheus_up 1.0" in response.read()
                with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    assert json.load(response)["runtime_status"] == "healthy"
                assert json.loads(runtime.read_text())["collectors"][0]["attempts"] == 1
            assert config.read_bytes() == raw
            assert (config.stat().st_ino, config.stat().st_mtime_ns) == (
                original.st_ino,
                original.st_mtime_ns,
            )
            assert (runtime.lstat().st_ino, runtime.lstat().st_mtime_ns) == (
                link.st_ino,
                link.st_mtime_ns,
            )
            assert not (tmp_path / "action-state.json").exists()
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.returncode == 0
        stopped = json.loads(runtime.read_text())
        assert stopped["status"] == "stopped" and stopped["running"] is False
        assert config.read_bytes() == raw
        output.seek(0)
        lines = output.read()
        assert "collector=plugin ok" not in lines
        assert "runtime_snapshot" not in lines


def test_unavailable_ram_keeps_metrics_working_without_disk_fallback(tmp_path):
    port = _port()
    raw = {
        "port": port,
        "repeater_enabled": False,
        "collection_interval_seconds": 5,
        "collection_timeout_seconds": 0.5,
    }
    code = (
        "from openhop_prometheus_plugin import runtime_store, main; "
        "runtime_store._is_tmpfs = lambda fd: False; raise SystemExit(main.main())"
    )
    with _process(tmp_path, raw, code) as (process, output):
        _wait_health(port)
        config = tmp_path / "config.json"
        before = _persistent_identity(config)
        time.sleep(5.2)
        assert _wait_health(port)["runtime_status"] == "healthy"
        with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
            assert b"openhop_prometheus_up 1.0" in response.read()
        assert _persistent_identity(config) == before
        assert not (tmp_path / "runtime.json").exists()
        assert not (tmp_path / "runtime.json").is_symlink()
        output.flush()
        output.seek(0)
        logs = output.read()
        assert logs.count("runtime_snapshot_unavailable failed") == 1
        assert "not tmpfs" in logs
        assert process.poll() is None


def test_crash_restart_reuses_link_and_resets_live_state_without_settings_changes(tmp_path):
    raw = {
        "port": _port(),
        "repeater_enabled": False,
        "collection_interval_seconds": 60,
        "collection_timeout_seconds": 0.5,
    }
    path = tmp_path / "runtime.json"
    with _process(tmp_path, raw) as (process, _):
        first = _wait_snapshot(path, lambda value: value.get("status") == "healthy")
        link = path.lstat()
        config_identity = _persistent_identity(tmp_path / "config.json")
        process.kill()
        process.wait(timeout=5)
        crashed = json.loads(path.read_text())
        assert crashed["running"] is True
        assert crashed["expires_at"] > crashed["generated_at"]
    with _process(tmp_path, raw) as (process, _):
        restarted = _wait_snapshot(
            path,
            lambda value: (
                value.get("status") == "healthy" and value.get("started_at") != first["started_at"]
            ),
        )
        assert restarted["collectors"][0]["attempts"] == 1
        assert (path.lstat().st_ino, path.lstat().st_mtime_ns) == (link.st_ino, link.st_mtime_ns)
        assert json.loads((tmp_path / "config.json").read_text()) == raw
        assert _persistent_identity(tmp_path / "config.json") == config_identity
        assert process.poll() is None


def test_failed_listener_rebind_recovers_without_settings_rewrite(tmp_path):
    port = _port()
    raw = {
        "port": port,
        "repeater_enabled": False,
        "collection_interval_seconds": 5,
        "collection_timeout_seconds": 0.5,
    }
    with _process(tmp_path, raw) as (process, _):
        path = tmp_path / "runtime.json"
        _wait_snapshot(path, lambda value: value.get("status") == "healthy")
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            replacement_port = occupied.getsockname()[1]
            raw["port"] = replacement_port
            config = tmp_path / "config.json"
            config.write_text(json.dumps(raw))
            saved = _persistent_identity(config)
            _wait_snapshot(path, lambda value: value.get("status") == "error")
        _wait_health(replacement_port)
        _wait_snapshot(path, lambda value: value.get("status") == "healthy")
        assert _persistent_identity(config) == saved
        assert process.poll() is None

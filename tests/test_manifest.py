import json
from pathlib import Path

import pytest

from openhop_prometheus_plugin import __version__
from openhop_prometheus_plugin.config import DEFAULT_CONFIG


def test_manifest_contract():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "openhop-plugin.json").read_text())
    assert manifest["schema"] == 1
    assert manifest["id"] == "openhop.prometheus"
    assert manifest["version"] == __version__
    assert manifest["runtime"] == {"type": "python", "entrypoint": "openhop-prometheus"}
    assert manifest["ui"] == {"type": "application", "entry": "ui/index.html"}
    defaults = manifest["config"]["defaults"]
    assert defaults["bind_host"] == "127.0.0.1"
    assert defaults["repeater_stats_path"] == "/api/stats"
    assert "meshcore_enabled" not in defaults
    assert "system_enabled" not in defaults


def test_release_version_references_agree():
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text()
    manifest = json.loads((root / "openhop-plugin.json").read_text())
    assert f'version = "{__version__}"' in project
    assert manifest["version"] == __version__
    assert f'v{__version__}</span>' in (root / "ui/index.html").read_text()
    assert f'openhop-prometheus/{__version__}' in (
        root / "src/openhop_prometheus_plugin/collectors/repeater.py"
    ).read_text()
    assert f'openHopPrometheus/{__version__}' in (
        root / "src/openhop_prometheus_plugin/http_server.py"
    ).read_text()
    assert f'openhop_prometheus_plugin-{__version__}-py3-none-any.whl' in (
        root / "README.md"
    ).read_text()


@pytest.mark.parametrize("filename", ["config.default.json", "openhop-plugin.json"])
def test_packaged_defaults_match_runtime_defaults(filename):
    root = Path(__file__).resolve().parents[1]
    defaults = json.loads((root / filename).read_text())
    if filename == "openhop-plugin.json":
        defaults = defaults["config"]["defaults"]
    assert defaults == DEFAULT_CONFIG

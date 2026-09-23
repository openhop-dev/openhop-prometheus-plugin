import json
from pathlib import Path

from openhop_prometheus_plugin import __version__


def test_manifest_contract():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "openhop-plugin.json").read_text())
    assert manifest["schema"] == 1
    assert manifest["id"] == "openhop.prometheus"
    assert manifest["version"] == "1.0.0"
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

import json
from pathlib import Path


def test_manifest_contract():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "openhop-plugin.json").read_text())
    assert manifest["schema"] == 1
    assert manifest["id"] == "openhop.prometheus"
    assert manifest["version"] == "0.2.0"
    assert manifest["runtime"] == {"type": "python", "entrypoint": "openhop-prometheus"}
    assert manifest["ui"] == {"type": "application", "entry": "ui/index.html"}
    defaults = manifest["config"]["defaults"]
    assert defaults["bind_host"] == "127.0.0.1"
    assert defaults["repeater_stats_path"] == "/api/stats"
    assert "meshcore_enabled" not in defaults
    assert "system_enabled" not in defaults

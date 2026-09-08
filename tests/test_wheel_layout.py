import json
import zipfile


def validate_wheel(path):
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        manifest_path = next(name for name in names if "share/openhop/plugins/openhop.prometheus/openhop-plugin.json" in name)
        next(name for name in names if "share/openhop/plugins/openhop.prometheus/config.default.json" in name)
        next(name for name in names if "share/openhop/plugins/openhop.prometheus/ui/index.html" in name)
        next(name for name in names if "share/openhop/plugins/openhop.prometheus/ui/app.js" in name)
        next(name for name in names if "share/openhop/plugins/openhop.prometheus/ui/styles.css" in name)
        manifest = json.loads(wheel.read(manifest_path))
        assert manifest["id"] == "openhop.prometheus"
        assert manifest["runtime"]["entrypoint"] == "openhop-prometheus"
        ep_path = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        entries = wheel.read(ep_path).decode()
        assert "openhop-prometheus = openhop_prometheus_plugin.main:main" in entries

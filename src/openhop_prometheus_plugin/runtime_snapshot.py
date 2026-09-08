from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .actions import action_snapshot
from .config import PluginConfig
from .http_server import MetricsHTTPServer
from .state import MetricsState


def publish_runtime_snapshot(
    data_dir: str | Path,
    config: PluginConfig,
    state: MetricsState,
    http_server: MetricsHTTPServer | None,
    *,
    next_collection_at: float | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    """Publish bounded, dashboard-safe runtime state through config.json."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.json"

    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}

    runtime = state.ui_snapshot(next_collection_at=next_collection_at)
    runtime.update(
        {
            "schema": 1,
            "plugin_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "last_error": last_error or runtime.get("last_error"),
            "http": {
                "bind_host": config.bind_host,
                "port": config.port,
                "metrics_path": config.metrics_path,
                "health_path": "/healthz",
                "external_bind": config.external_bind,
                "listening": http_server is not None,
            },
            "repeater": {
                "enabled": config.repeater_enabled,
                "scheme": config.repeater_scheme,
                "host": config.repeater_host,
                "port": config.repeater_port,
                "stats_path": config.repeater_stats_path,
                "api_token_configured": bool(config.repeater_api_token),
            },
            "actions": action_snapshot(data_dir),
        }
    )

    raw["_runtime"] = runtime
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(config_path)
    return runtime

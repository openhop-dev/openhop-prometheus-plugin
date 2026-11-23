from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .actions import action_snapshot
from .config import PluginConfig
from .http_server import MetricsHTTPServer
from .runtime_store import MAX_SNAPSHOT_BYTES, RuntimePublisher
from .state import MetricsState

HEARTBEAT_SECONDS = 5
FRESHNESS_SECONDS = 30


def _bounded(value: Any, secret: str, depth: int = 0) -> Any:
    """Bound diagnostic text/containers, including event-driven action results."""
    if depth > 8:
        return None
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[redacted]")
            if secret.lower().startswith("bearer ") and secret[7:].strip():
                value = value.replace(secret[7:].strip(), "[redacted]")
        return value[:512]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded(item, secret, depth + 1)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, secret, depth + 1) for item in value[:300]]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (bool, int, float)) else None


def publish_runtime_snapshot(
    data_dir: str | Path,
    config: PluginConfig,
    state: MetricsState,
    http_server: MetricsHTTPServer | None,
    *,
    next_collection_at: float | None = None,
    last_error: str | None = None,
    publisher: RuntimePublisher | None = None,
) -> dict[str, Any]:
    """Publish dashboard-safe state in RAM, never reading or rewriting settings.

    Legacy config._runtime is deliberately left untouched until a real UI save.
    This avoids a plugin/host read-modify-write race during upgrade.
    """
    now = datetime.now(timezone.utc)
    runtime = state.ui_snapshot(next_collection_at=next_collection_at)
    runtime.update(
        {
            "schema": 1,
            "plugin_version": __version__,
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=FRESHNESS_SECONDS)).isoformat(),
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
            "actions": action_snapshot(Path(data_dir)),
        }
    )
    runtime = _bounded(runtime, config.repeater_api_token)
    # A worst-case set of labels must not break status publication. Keep counts
    # accurate while trimming the dashboard preview (Prometheus cache unchanged).
    while len(json.dumps(runtime, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES - 1024:
        if not runtime.get("metrics"):
            runtime["actions"] = {}
            break
        runtime["metrics"] = runtime["metrics"][: len(runtime["metrics"]) // 2]
        runtime["metrics_truncated"] = True
    if publisher is None:
        with RuntimePublisher(data_dir) as temporary_publisher:
            temporary_publisher.write(runtime)
    else:
        publisher.write(runtime)
    return runtime

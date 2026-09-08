from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .collectors import RepeaterCollector
from .config import PluginConfig
from .http_server import MetricsHTTPServer
from .manager import CollectionManager

_STATE_FILE = "action-state.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(data_dir: Path) -> dict[str, Any]:
    path = data_dir / _STATE_FILE
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(data_dir: Path, value: dict[str, Any]) -> None:
    path = data_dir / _STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def action_snapshot(data_dir: Path) -> dict[str, Any]:
    return _read(data_dir)


def _run_action(
    data_dir: Path,
    action_name: str,
    request_id: str,
    callback: Callable[[], dict[str, Any] | None],
) -> bool:
    if not request_id:
        return False
    state = _read(data_dir)
    previous = state.get(action_name)
    if isinstance(previous, dict) and previous.get("request_id") == request_id:
        return False

    result: dict[str, Any] = {
        "request_id": request_id,
        "completed_at": _iso_now(),
        "status": "ok",
        "message": "completed",
    }
    try:
        detail = callback() or {}
        result["detail"] = detail
        if detail.get("message"):
            result["message"] = str(detail["message"])
    except Exception as exc:
        result["status"] = "error"
        result["message"] = str(exc)

    state[action_name] = result
    _write(data_dir, state)
    return True


def process_actions(
    data_dir: Path,
    config: PluginConfig,
    manager: CollectionManager,
    http_server: MetricsHTTPServer,
) -> bool:
    changed = False

    changed |= _run_action(
        data_dir,
        "endpoint",
        config.test_endpoint_request_id,
        lambda: {
            **http_server.test_endpoint(timeout=min(5.0, config.collection_timeout_seconds)),
            "message": "Prometheus /healthz and metrics endpoint responded successfully.",
        },
    )

    def test_repeater():
        if not config.repeater_enabled:
            raise RuntimeError("Repeater collector is disabled")
        result = RepeaterCollector(config).collect_safe()
        if not result.ok:
            raise RuntimeError(result.error or "Repeater collector failed")
        return {
            "metric_count": len(result.metrics),
            "duration_ms": round(result.duration_seconds * 1000),
            "message": f"Repeater /api/stats test succeeded and produced {len(result.metrics)} metric samples.",
        }

    changed |= _run_action(
        data_dir,
        "repeater",
        config.test_repeater_request_id,
        test_repeater,
    )

    def refresh():
        results = manager.collect_once(config)
        failures = [result.name for result in results if not result.ok]
        return {
            "collector_count": len(results),
            "failed_collectors": failures,
            "message": (
                "Metrics refreshed successfully."
                if not failures
                else f"Metrics refreshed with failed collectors: {', '.join(failures)}"
            ),
        }

    changed |= _run_action(
        data_dir,
        "refresh",
        config.refresh_request_id,
        refresh,
    )

    return bool(changed)

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "bind_host": "127.0.0.1",
    "port": 9109,
    "metrics_path": "/metrics",
    "collection_interval_seconds": 30,
    "collection_timeout_seconds": 10,
    "repeater_enabled": True,
    "repeater_scheme": "http",
    "repeater_host": "127.0.0.1",
    "repeater_port": 8000,
    "repeater_stats_path": "/api/stats",
    "repeater_api_token": "",
    "repeater_verify_tls": True,
    "test_endpoint_request_id": "",
    "test_repeater_request_id": "",
    "refresh_request_id": "",
}

_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/-]*$")


@dataclass(frozen=True, slots=True)
class PluginConfig:
    bind_host: str
    port: int
    metrics_path: str
    collection_interval_seconds: int
    collection_timeout_seconds: float

    repeater_enabled: bool
    repeater_scheme: str
    repeater_host: str
    repeater_port: int
    repeater_stats_path: str
    repeater_api_token: str
    repeater_verify_tls: bool

    test_endpoint_request_id: str
    test_repeater_request_id: str
    refresh_request_id: str

    @property
    def external_bind(self) -> bool:
        host = self.bind_host.strip().lower()
        return host not in {"127.0.0.1", "localhost", "::1"}

    @property
    def repeater_url(self) -> str:
        return (
            f"{self.repeater_scheme}://{self.repeater_host}:"
            f"{self.repeater_port}{self.repeater_stats_path}"
        )

    def user_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def _path(value: Any, name: str, *, allow_healthz: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("/"):
        raise ValueError(f"{name} must start with /")
    if len(raw) > 128 or not _PATH_RE.fullmatch(raw):
        raise ValueError(f"{name} contains invalid characters")
    if ".." in raw or "//" in raw or "?" in raw or "#" in raw:
        raise ValueError(f"{name} must be a simple URL path")
    if not allow_healthz and raw == "/healthz":
        raise ValueError(f"{name} cannot be /healthz")
    return raw.rstrip("/") or "/"


def _host(value: Any, name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} is required")
    if len(raw) > 255 or any(token in raw for token in ("/", "?", "#", "://")):
        raise ValueError(f"{name} must be a hostname or IP address, not a URL")
    if any(ch.isspace() for ch in raw):
        raise ValueError(f"{name} must not contain whitespace")
    return raw


def _port(value: Any, name: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def load_config(data_dir: str | Path) -> PluginConfig:
    data_dir = Path(data_dir)
    path = data_dir / "config.json"
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("config.json must contain a JSON object")
        raw = loaded

    cfg = dict(DEFAULT_CONFIG)
    # Ignore removed v0.1 MeshCore/system keys on upgrade rather than failing.
    cfg.update({key: value for key, value in raw.items() if key in DEFAULT_CONFIG})

    bind_host = _host(cfg["bind_host"], "bind_host")
    port = _port(cfg["port"], "port")
    if port < 1024:
        raise ValueError("port must be 1024 or higher for the unprivileged plugin runtime")
    metrics_path = _path(cfg["metrics_path"], "metrics_path", allow_healthz=False)

    interval = int(cfg["collection_interval_seconds"])
    if not 5 <= interval <= 3600:
        raise ValueError("collection_interval_seconds must be between 5 and 3600")
    timeout = float(cfg["collection_timeout_seconds"])
    if not 0.5 <= timeout <= 60:
        raise ValueError("collection_timeout_seconds must be between 0.5 and 60")
    if timeout >= interval:
        raise ValueError("collection_timeout_seconds must be less than collection_interval_seconds")

    repeater_scheme = str(cfg["repeater_scheme"]).strip().lower()
    if repeater_scheme not in {"http", "https"}:
        raise ValueError("repeater_scheme must be http or https")

    return PluginConfig(
        bind_host=bind_host,
        port=port,
        metrics_path=metrics_path,
        collection_interval_seconds=interval,
        collection_timeout_seconds=timeout,
        repeater_enabled=bool(cfg["repeater_enabled"]),
        repeater_scheme=repeater_scheme,
        repeater_host=_host(cfg["repeater_host"], "repeater_host"),
        repeater_port=_port(cfg["repeater_port"], "repeater_port"),
        repeater_stats_path=_path(cfg["repeater_stats_path"], "repeater_stats_path"),
        repeater_api_token=str(cfg["repeater_api_token"] or "").strip(),
        repeater_verify_tls=bool(cfg["repeater_verify_tls"]),
        test_endpoint_request_id=str(cfg["test_endpoint_request_id"] or "").strip(),
        test_repeater_request_id=str(cfg["test_repeater_request_id"] or "").strip(),
        refresh_request_id=str(cfg["refresh_request_id"] or "").strip(),
    )

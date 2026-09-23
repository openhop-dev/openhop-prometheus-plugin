from __future__ import annotations

import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from prometheus_client import CollectorRegistry, CONTENT_TYPE_LATEST, generate_latest

from .config import PluginConfig
from .metrics import SnapshotCollector
from .state import MetricsState


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class MetricsHTTPServer:
    def __init__(self, config: PluginConfig, state: MetricsState):
        self.config = config
        self.state = state
        self.registry = CollectorRegistry(auto_describe=False)
        self.registry.register(SnapshotCollector(state))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return self.config.bind_host, self.config.port
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def matches(self, config: PluginConfig) -> bool:
        return (
            config.bind_host == self.config.bind_host
            and config.port == self.config.port
            and config.metrics_path == self.config.metrics_path
        )

    def start(self) -> None:
        if self._server is not None:
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "openHopPrometheus/1.0.0"

            def log_message(self, format, *args):  # noqa: A003
                return

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):  # noqa: N802
                self.do_GET()

            def do_GET(self):  # noqa: N802
                path = urlsplit(self.path).path
                if path == outer.config.metrics_path:
                    outer.state.note_scrape()
                    try:
                        body = generate_latest(outer.registry)
                    except Exception as exc:
                        outer.state.note_scrape_error()
                        self._send(
                            500,
                            "text/plain; charset=utf-8",
                            f"metrics generation failed: {exc}\n".encode("utf-8"),
                        )
                        return
                    self._send(200, CONTENT_TYPE_LATEST, body)
                    return

                if path == "/healthz":
                    snapshot = outer.state.ui_snapshot()
                    body = json.dumps(
                        {
                            "status": "healthy" if snapshot["running"] else "stopped",
                            "runtime_status": snapshot["status"],
                            "collectors_healthy": snapshot["collectors_healthy"],
                            "collector_count": snapshot["collector_count"],
                            "last_collection_completed": snapshot["last_collection_completed"],
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    self._send(
                        200 if snapshot["running"] else 503,
                        "application/json; charset=utf-8",
                        body,
                    )
                    return

                self._send(404, "text/plain; charset=utf-8", b"not found\n")

        server_class = _IPv6ThreadingHTTPServer if ":" in self.config.bind_host else ThreadingHTTPServer
        self._server = server_class((self.config.bind_host, self.config.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="openhop-prometheus-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def _loopback_test_host(self) -> str:
        host = self.address[0]
        if host in {"0.0.0.0", ""}:
            return "127.0.0.1"
        if host == "::":
            return "::1"
        return host

    def test_endpoint(self, timeout: float = 3.0) -> dict:
        host = self._loopback_test_host()
        if ":" in host and not host.startswith("["):
            url_host = f"[{host}]"
        else:
            url_host = host
        port = self.address[1]
        health_url = f"http://{url_host}:{port}/healthz"
        metrics_url = f"http://{url_host}:{port}{self.config.metrics_path}"

        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            health_status = response.status
            health_body = response.read(4096).decode("utf-8", "replace")
        with urllib.request.urlopen(metrics_url, timeout=timeout) as response:
            metrics_status = response.status
            metrics_body = response.read(65536).decode("utf-8", "replace")

        if health_status != 200:
            raise RuntimeError(f"/healthz returned HTTP {health_status}: {health_body[:160]}")
        if metrics_status != 200 or "openhop_prometheus_up" not in metrics_body:
            raise RuntimeError(f"metrics endpoint verification failed with HTTP {metrics_status}")
        return {
            "health_status": health_status,
            "metrics_status": metrics_status,
            "metrics_bytes_checked": len(metrics_body.encode("utf-8")),
        }

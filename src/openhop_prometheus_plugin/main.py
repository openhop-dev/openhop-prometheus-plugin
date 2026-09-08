from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest

from . import __version__
from .actions import process_actions
from .config import PluginConfig, load_config
from .http_server import MetricsHTTPServer
from .manager import CollectionManager
from .metrics import SnapshotCollector
from .runtime_snapshot import publish_runtime_snapshot
from .state import MetricsState

LOGGER = logging.getLogger("openhop.prometheus")


def _data_dir() -> Path:
    raw = os.environ.get("OPENHOP_PLUGIN_DATA")
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openhop-prometheus",
        description="Prometheus exporter runtime plugin for openHop Repeater",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--once", action="store_true", help="Collect once, publish runtime state, then exit.")
    parser.add_argument("--check-config", action="store_true", help="Validate and print a redacted effective config.")
    parser.add_argument("--dump-metrics", action="store_true", help="Collect once and print Prometheus exposition text.")
    return parser


def _configure_logging(*, stream=None) -> None:
    level = os.environ.get("OPENHOP_PROMETHEUS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=stream or sys.stdout,
    )


def _redacted_config(config: PluginConfig) -> dict:
    value = config.user_dict()
    value["repeater_api_token"] = "configured" if config.repeater_api_token else ""
    return value


def _dump_metrics(config: PluginConfig) -> int:
    state = MetricsState(__version__)
    state.set_running(True)
    manager = CollectionManager(state)
    manager.collect_once(config)
    registry = CollectorRegistry(auto_describe=False)
    registry.register(SnapshotCollector(state))
    sys.stdout.buffer.write(generate_latest(registry))
    return 0


def _start_server(config: PluginConfig, state: MetricsState) -> MetricsHTTPServer:
    server = MetricsHTTPServer(config, state)
    server.start()
    LOGGER.info(
        "metrics_http_started bind=%s port=%d path=%s",
        config.bind_host,
        server.address[1],
        config.metrics_path,
    )
    if config.external_bind:
        LOGGER.warning(
            "metrics endpoint is bound beyond loopback; protect it with firewall/VPN/reverse proxy controls"
        )
    return server


def main() -> int:
    args = _parser().parse_args()
    # --dump-metrics must keep stdout as pure Prometheus exposition text.
    _configure_logging(stream=sys.stderr if args.dump_metrics else sys.stdout)

    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    plugin_id = os.environ.get("OPENHOP_PLUGIN_ID", "openhop.prometheus")

    try:
        config = load_config(data_dir)
    except Exception as exc:
        LOGGER.error("invalid plugin config: %s", exc)
        return 2

    if args.check_config:
        print(json.dumps(_redacted_config(config), indent=2))
        return 0
    if args.dump_metrics:
        return _dump_metrics(config)

    stop = threading.Event()

    def _stop(signum, frame):
        LOGGER.info("shutdown requested signal=%s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    state = MetricsState(__version__)
    state.set_running(True)
    manager = CollectionManager(state)
    http_server: MetricsHTTPServer | None = None

    LOGGER.info(
        "starting plugin_id=%s version=%s data_dir=%s",
        plugin_id,
        __version__,
        data_dir,
    )

    try:
        try:
            http_server = _start_server(config, state)
        except Exception as exc:
            state.mark_collection_fatal_error(str(exc))
            try:
                publish_runtime_snapshot(data_dir, config, state, None, last_error=str(exc))
            except Exception:
                LOGGER.exception("runtime_snapshot_publish_failed")
            LOGGER.error("unable to start metrics HTTP endpoint: %s", exc)
            return 2

        next_collection_at: float | None = None

        while not stop.is_set():
            cycle_error: str | None = None
            try:
                # Reload config each cycle. Normal UI saves request a plugin
                # restart, but this also supports safe manual edits.
                latest = load_config(data_dir)
                if http_server is not None and not http_server.matches(latest):
                    LOGGER.info("HTTP bind/path changed; restarting metrics listener")
                    http_server.stop()
                    http_server = _start_server(latest, state)
                config = latest

                manager.collect_once(config)
                if http_server is not None:
                    process_actions(data_dir, config, manager, http_server)

                next_collection_at = time.time() + config.collection_interval_seconds
                publish_runtime_snapshot(
                    data_dir,
                    config,
                    state,
                    http_server,
                    next_collection_at=next_collection_at,
                    last_error=None,
                )
            except Exception as exc:
                cycle_error = str(exc)
                state.mark_collection_fatal_error(cycle_error)
                LOGGER.exception("collection_cycle_failed")
                next_collection_at = time.time() + min(30, config.collection_interval_seconds)
                try:
                    publish_runtime_snapshot(
                        data_dir,
                        config,
                        state,
                        http_server,
                        next_collection_at=next_collection_at,
                        last_error=cycle_error,
                    )
                except Exception:
                    LOGGER.exception("runtime_snapshot_publish_failed")

            if args.once:
                return 0

            wait_seconds = max(1.0, (next_collection_at or time.time() + 5) - time.time())
            stop.wait(wait_seconds)

    finally:
        state.set_running(False)
        if http_server is not None:
            try:
                http_server.stop()
            except Exception:
                LOGGER.exception("metrics_http_stop_failed")
        try:
            publish_runtime_snapshot(data_dir, config, state, None)
        except Exception:
            LOGGER.exception("runtime_snapshot_shutdown_publish_failed")

    LOGGER.info("plugin stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

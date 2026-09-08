import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from openhop_prometheus_plugin.config import load_config
from openhop_prometheus_plugin.http_server import MetricsHTTPServer
from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.state import MetricsState


def make_server(tmp_path):
    cfg = replace(load_config(tmp_path), port=0)
    state = MetricsState("0.1.0")
    state.set_running(True)
    state.update_collector(CollectorResult(
        "plugin",
        True,
        [MetricSample("openhop_prometheus_process_threads", "threads", "gauge", 3, collector="plugin")],
        0.01,
    ))
    server = MetricsHTTPServer(cfg, state)
    server.start()
    return server, state


def test_metrics_and_healthz(tmp_path):
    server, state = make_server(tmp_path)
    try:
        port = server.address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as response:
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "healthy"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as response:
            text = response.read().decode()
            assert "openhop_prometheus_up 1.0" in text
        assert state.snapshot()["scrapes_total"] == 1
    finally:
        server.stop()


def test_concurrent_scrapes_are_safe(tmp_path):
    server, state = make_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{server.address[1]}/metrics"
        def scrape(_):
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status, len(response.read())
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(scrape, range(20)))
        assert all(status == 200 and size > 100 for status, size in results)
        assert state.snapshot()["scrapes_total"] == 20
    finally:
        server.stop()

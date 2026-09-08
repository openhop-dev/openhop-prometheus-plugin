# openHop Prometheus plugin

A lightweight openHop Repeater plugin that exports Repeater telemetry as Prometheus metrics and serves a small embedded dashboard.

This repository is private during integration testing and is expected to be made public after validation.

## Runtime flow

```text
openHop Repeater API
    -> /api/stats (required)
    -> additional mesh endpoints (best-effort)
openhop-prometheus plugin
    -> in-memory metric snapshot
Prometheus
    -> scrape /metrics
```

The plugin does not run a second host collector. System metrics come from the Repeater `sensors.readings` `hardware_stats` payload when available.

## Plugin data

The plugin manager should provide:

```text
OPENHOP_PLUGIN_DATA=/var/lib/openhop/plugins/openhop.prometheus/data
```

With `OPENHOP_PLUGIN_DATA` set, the plugin reads:

```text
$OPENHOP_PLUGIN_DATA/config.json
```

## Endpoints exposed by this plugin

Default listener:

```text
http://127.0.0.1:9109/metrics
http://127.0.0.1:9109/healthz
```

Collection runs in the background. Scrapes read a thread-safe snapshot and do not block on live Repeater API calls.

## config.json

Default configuration:

```json
{
  "bind_host": "127.0.0.1",
  "port": 9109,
  "metrics_path": "/metrics",
  "collection_interval_seconds": 30,
  "collection_timeout_seconds": 10,
  "repeater_enabled": true,
  "repeater_scheme": "http",
  "repeater_host": "127.0.0.1",
  "repeater_port": 8000,
  "repeater_stats_path": "/api/stats",
  "repeater_api_token": "",
  "repeater_verify_tls": true,
  "test_endpoint_request_id": "",
  "test_repeater_request_id": "",
  "refresh_request_id": ""
}
```

If `/api/stats` requires auth, set `repeater_api_token`. The plugin sends it as `X-API-Key` (and strips an accidental leading `Bearer ` if pasted).

## Repeater API sources

Required source:

- `/api/stats`

Additional mesh analytics sources (best-effort):

- `/api/packet_stats?hours=24`
- `/api/packet_type_stats?hours=24`
- `/api/route_stats?hours=24`
- `/api/lbt_diagnostics?hours=24`
- `/api/noise_floor_stats?hours=24`
- `/api/neighbor_links?active_within_seconds=90&limit=500`

Per-endpoint status:

- `openhop_repeater_api_endpoint_up{endpoint="..."}`
- `openhop_repeater_api_endpoint_response_bytes{endpoint="..."}`

Failures on optional endpoints do not fail the entire scrape.

## Metric families

Repeater counters and gauges from `/api/stats` include examples such as:

```text
openhop_repeater_rx_packets_total
openhop_repeater_forwarded_packets_total
openhop_repeater_crc_errors_total
openhop_repeater_uptime_seconds
openhop_repeater_noise_floor_dbm
openhop_repeater_recent_packets
openhop_repeater_recent_rssi_dbm{stat="min|max|avg"}
openhop_repeater_recent_snr_db{stat="min|max|avg"}
```

System-health sensor mappings include:

```text
openhop_system_cpu_usage_percent
openhop_system_memory_used_bytes
openhop_system_disk_usage_percent
openhop_system_network_transmit_bytes_total
openhop_system_temperature_celsius{sensor="..."}
openhop_system_info
```

Additional mesh analytics mappings include:

```text
openhop_repeater_window_total_packets
openhop_repeater_window_packet_type_total_packets{packet_type="..."}
openhop_repeater_window_route_total_packets{route="..."}
openhop_repeater_lbt_retry_rate_percent
openhop_repeater_noise_floor_average_dbm
openhop_repeater_neighbor_link_sample_count{peer_hash="...",path_hash_size="..."}
```

## Privacy and cardinality

The exporter intentionally does not expose payload bodies, raw packet bytes, JWT/API tokens, or GPS coordinates.

By request, neighbor-link metrics include `peer_hash` and `path_hash_size` labels from `/api/neighbor_links`.

## Plugin manifest

`openhop-plugin.json` declares a Python runtime plugin with embedded UI assets:

```json
{
  "schema": 1,
  "id": "openhop.prometheus",
  "runtime": {
    "type": "python",
    "entrypoint": "openhop-prometheus"
  },
  "ui": {
    "type": "application",
    "entry": "ui/index.html"
  }
}
```

Wheel data files include:

```text
share/openhop/plugins/openhop.prometheus/openhop-plugin.json
share/openhop/plugins/openhop.prometheus/config.default.json
share/openhop/plugins/openhop.prometheus/ui/index.html
share/openhop/plugins/openhop.prometheus/ui/app.js
share/openhop/plugins/openhop.prometheus/ui/styles.css
```

## Standalone development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
openhop-prometheus
```

## Testing

```bash
python -m compileall -q src tests
pytest -q
node --check ui/app.js
```

## Build wheel

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip build
python -m build --wheel
```

Output example:

```text
dist/openhop_prometheus_plugin-0.2.0-py3-none-any.whl
```

The openHop plugin manager installs the wheel release asset.

## CLI diagnostics

```bash
openhop-prometheus --check-config
openhop-prometheus --once
openhop-prometheus --dump-metrics
```

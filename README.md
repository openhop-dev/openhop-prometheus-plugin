# openHop Prometheus plugin

A lightweight openHop Repeater plugin that exports Repeater telemetry as Prometheus metrics and serves a small embedded dashboard.

The repository is currently private. Publication is a separate decision from building or tagging a release.

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
OPENHOP_PLUGIN_DATA=/var/lib/openhop_repeater/plugins/openhop.prometheus/data
```

With `OPENHOP_PLUGIN_DATA` set, the plugin reads:

```text
$OPENHOP_PLUGIN_DATA/config.json
```

### Runtime storage and upgrades

The dashboard runtime bridge requires **openHop Repeater 1.1.2 or later** and
Linux with a writable, shared `/dev/shm` tmpfs. Standard native and single-container
Repeater installations already provide this; no new listener, mount or systemd
setting is needed. If the manager and plugin run in separate containers, they must
see the same tmpfs and run under a compatible service UID.

Upgrade through Plugin Manager: stop Prometheus, upload the new wheel, then start
Prometheus again. A catalogue update uses the same versioned wheel and manages the
restart. Existing settings, the Repeater API token and completed action IDs are
retained; there is no need to reconfigure the exporter. Uploading a local wheel
alone does not restart an already-running plugin on older Repeater versions.

- `config.json` holds settings. Collection, dashboard polling, error reporting and
  shutdown never rewrite it. An old `_runtime` field is ignored and left untouched
  during upgrade; the next intentional settings save removes that obsolete field.
- `runtime.json` is a stable compatibility symlink into an owned, mode-0700
  `/dev/shm/openhop-prometheus-<uid>-<instance-hash>/` directory. The instance hash
  is derived from the canonical plugin data directory. Only the mode-0600 RAM
  target is atomically replaced, including during a five-second status heartbeat.
  Snapshot size is limited to 256 KiB; the metric preview is trimmed if necessary,
  without changing the Prometheus cache or metric counts.
- The dashboard reads runtime separately through the existing protected
  `/api/plugins/runtime` endpoint. Polls and scrapes do not start a collection or
  persist a status snapshot. Stopped snapshots are labelled stopped; a running
  snapshot that has not refreshed for 30 seconds becomes stale, not healthy.
- RAM state is recreated on plugin start after a reboot or container replacement;
  the persistent link is reused. An unavailable snapshot is shown as unavailable,
  rather than falling back to legacy status in settings.
- `action-state.json` remains persistent to prevent completed Refresh/Test requests
  being replayed after restart or upgrade. It is written only for a new requested
  action, not periodically. This is completed-request deduplication, not a claim
  of crash-proof exactly-once action execution.

If RAM publication is unavailable, collection and `/metrics` continue to work,
while the dashboard reports unavailable or expired status. Plugin Manager logs
show `runtime_snapshot_unavailable` or a bounded `runtime_snapshot` failure.
Check that `/dev/shm` really is tmpfs, that manager/plugin see the same mount, that
private runtime files belong to their service UID, and that `data/runtime.json`
is not an unrelated regular file or foreign link. Restart the plugin after fixing
startup storage access. There is **no persistent-file fallback**.

Routine successful-collection detail is DEBUG only. First failures and recoveries
remain visible at the default INFO level; continuing failures are summarized at
most once every five minutes per subsystem, including changing exception text.
Set the plugin log level to DEBUG for opt-in troubleshooting, or inherit
`OPENHOP_PROMETHEUS_LOG_LEVEL=DEBUG` as described below. Debug logs and
action/configuration changes can still write to persistent storage.
**Tmpfs may use swap**; this repair removes routine plugin file/log writes,
not every possible physical storage write by Linux or Repeater.

### Log level

Open **Settings → Plugin runtime → Log level**, choose a level, then select
**Save & restart**. The same setting is available as `log_level` in the existing
`$OPENHOP_PLUGIN_DATA/config.json` file; for example, set `"log_level": "WARNING"`.

- Supported selections are `DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL`.
- The default is `"log_level": ""` (**Default (inherit)**). A missing key, JSON
  `null` or a whitespace-only value also inherits. Existing configurations do not
  need a migration or a background rewrite.
- A nonempty `log_level` takes precedence over `OPENHOP_PROMETHEUS_LOG_LEVEL`.
  When inheriting, the environment chooses the level, falling back to INFO when
  absent or invalid. JSON level names are case-insensitive and trim surrounding
  whitespace; invalid nonempty values are rejected.
- Normal UI saves restart the plugin. Manual file edits are loaded at the next
  collection cycle, including removing an override to restore the inherited
  level. Loading/applying the level does not rewrite the settings file.

DEBUG is intended for temporary diagnosis: it enables collection detail that
Repeater may persist in its plugin log. Select a quieter level afterward, or
return to **Default (inherit)** if the environment already sets the desired level.

## Endpoints exposed by this plugin

Default listener:

```text
http://127.0.0.1:9109/metrics
http://127.0.0.1:9109/healthz
```

Collection runs in the background. Scrapes read a thread-safe snapshot and do not block on live Repeater API calls.
The metrics and health endpoints have no application-level authentication. Keep the default loopback bind unless a remote scraper needs access; for a LAN/VPN bind, restrict port 9109 to trusted monitoring hosts with network controls. The Repeater dashboard's JWT protects its `/api/plugins/settings` and `/api/plugins/runtime` endpoints, not this separate scrape listener. Plugin HTML and JavaScript are publicly served; the protected API enforces settings access.

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
  "log_level": "",
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
share/openhop/plugins/openhop.prometheus/ui/assets/prometheus-logo.svg
share/openhop/plugins/openhop.prometheus/ui/assets/PROMETHEUS-LICENSE
share/openhop/plugins/openhop.prometheus/ui/assets/PROVENANCE.md
```

## Standalone development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,browser]'
playwright install chromium
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
dist/openhop_prometheus_plugin-1.1.0-py3-none-any.whl
```

The openHop plugin manager installs the wheel release asset. See
[CHANGELOG.md](CHANGELOG.md) for release changes.

## UI development

The embedded UI follows the openHop NOMAD plugin light/dark palette. The official
Prometheus logo is bundled offline under `ui/assets/`, with pinned provenance
and its upstream license. All five dashboard tabs and settings remain available.

Run browser regressions explicitly (otherwise they skip without Playwright):

```bash
.venv/bin/pip install -e '.[dev,browser]'
.venv/bin/playwright install chromium
.venv/bin/pytest -q
```

Browser tests use an in-memory settings API, not a live Repeater.

## CLI diagnostics

```bash
openhop-prometheus --check-config
openhop-prometheus --once
openhop-prometheus --dump-metrics
```

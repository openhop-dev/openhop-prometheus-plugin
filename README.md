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
dist/openhop_prometheus_plugin-1.0.0-py3-none-any.whl
```

The openHop plugin manager installs the wheel release asset.

## Catalogue proposals (not a release command)

The tag-triggered `release.yml` publishes **one** wheel. A separate `workflow_run`
proposal workflow runs only after that release workflow succeeds; manual retries
use `workflow_dispatch` on the producer's default `dev` branch with an existing
`vMAJOR.MINOR.PATCH` tag. Proposal automation checks out trusted producer `dev`
and current upstream catalogue `main` with persisted credentials disabled. It
requires the tag/source to be on `dev`, an exact successful tag-bound release
run, a final GitHub Release with only the expected wheel, and a publicly
downloadable release asset. It calculates SHA-256 from **downloaded public wheel
bytes**, not from `dist/` or any local build. It checks the catalogue-owned
registration and Python-service wheel policy (manifest, metadata, entrypoint,
RECORD and archive safety), then runs the destination validator and test suite
against both baseline and candidate without a catalogue write token. A failed
check stops before any PR or publisher token. A private repository or release
download cannot pass this public-artifact gate.

After validation, separate scoped GitHub App installation tokens may create
or update a leased version-specific branch in **yellowcooln's catalogue fork**
and open a PR against upstream catalogue main. No catalogue content is pushed
to the upstream repository. The first entry is a draft for human review;
later release-only updates are ready for catalogue-owned policy to decide,
not producer self-certification.
Retries preserve an existing PR's draft/ready choice; an already approved
identical version is a no-op, and changing bytes at the same version or proposing
a downgrade is rejected. The proposal changes catalogue metadata only; it does
not merge, deploy, or modify the catalogue from this checkout.

### Setup before the first public release

1. Merge the producer automation into `dev` **before tagging**. The tag must be
   at a commit reachable from that trusted branch. Make
   `openhop-dev/openhop-prometheus-plugin` public before the release: both the
   source tag and GitHub Release wheel must be publicly readable without auth.
   Do not assume a private release can be approved by publishing a local hash.
2. Have the catalogue maintainers register `openhop.prometheus` in trusted
   `approved-apps.json` on upstream catalogue `main`: source/artifact repository
   `openhop-dev/openhop-prometheus-plugin`, distribution
   `openhop-prometheus-plugin`, wheel basename `openhop_prometheus_plugin`,
   `python-service-v1` with package root `openhop_prometheus_plugin`, module
   `openhop_prometheus_plugin.main`, console script `openhop-prometheus`, callable
   `main`, `public-tag` source verification, `wheel-only` release assets, branch
   prefix `automation/openhop-prometheus-v`, and the approved catalogue publisher
   App identity. The catalogue's schema, validator and tests must accept a new
   initial entry. Until this registration reaches upstream `main`, proposals
   fail closed. Catalogue changes belong on the maintainer's fork, not here.
3. Install the dedicated catalogue-publisher GitHub App on both
   `yellowcooln/openhop-plugin-catalogue` (Metadata read, Contents write)
   and `openhop-dev/openhop-plugin-catalogue` (Metadata read, Pull requests
   write). Two repository-scoped installation tokens keep branch writes in the
   fork and PR writes upstream. Configure producer Actions secrets
   `OPENHOP_CATALOGUE_APP_ID` and `OPENHOP_CATALOGUE_APP_PRIVATE_KEY` in the
   producer repository (or change the workflow to reference a protected release
   environment and store them there). These are **not currently configured**;
   no key belongs in this repository. The App identity must match trusted
   catalogue registration; the release workflow's `GITHUB_TOKEN` is read-only
   for proposal verification and does not write cross-repository PRs.
4. Only after these gates are in place, deliberately push the final `v1.0.0`
   tag from trusted `dev` to run the release workflow. Confirm its final public
   wheel and successful run. The proposal then produces a **draft** initial PR;
   the catalogue team reviews and approves the initial listing. If the trigger
   is missed after a successful release, manually dispatch the proposal from
   `dev` with the same tag. Subsequent versions can use the same release path,
   proposing only version, source revision, URL and SHA-256.

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

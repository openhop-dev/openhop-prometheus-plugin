from __future__ import annotations

from statistics import fmean
from typing import Any
from urllib.parse import urlencode

import requests

from ..config import PluginConfig
from ..model import MetricSample
from .base import BaseCollector


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any, limit: int = 120) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _apply_repeater_auth_headers(headers: dict[str, str], token: str) -> None:
    """Attach token headers accepted by Repeater auth middleware.

    Repeater API tokens are validated from X-API-Key.
    """
    raw = token.strip()
    if not raw:
        return

    token_value = raw
    if raw.lower().startswith("bearer "):
        token_value = raw[7:].strip()
    if not token_value:
        return

    headers["X-API-Key"] = token_value


def _metric(
    name: str,
    help_text: str,
    kind: str,
    value: Any,
    *,
    collector: str,
    labels: dict[str, str] | None = None,
    scale: float = 1.0,
) -> MetricSample | None:
    number = _num(value)
    if number is None:
        return None
    if kind == "counter" and number < 0:
        return None
    return MetricSample(
        name,
        help_text,
        kind,
        number * scale,
        labels=labels or {},
        collector=collector,
    )


class RepeaterCollector(BaseCollector):
    """Map the current openHop Repeater /api/stats schema to Prometheus."""

    name = "repeater"

    def __init__(self, config: PluginConfig):
        self.config = config

    def failure_metrics(self) -> list[MetricSample]:
        return [
            MetricSample(
                "openhop_repeater_up",
                "Whether the openHop Repeater statistics API is reachable.",
                "gauge",
                0,
                collector=self.name,
            )
        ]

    def collect(self) -> list[MetricSample]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "openhop-prometheus/0.2.0",
        }
        _apply_repeater_auth_headers(headers, self.config.repeater_api_token)

        payload, response_bytes = self._fetch_primary_stats(headers)

        metrics: list[MetricSample] = [
            MetricSample(
                "openhop_repeater_up",
                "Whether the openHop Repeater statistics API is reachable.",
                "gauge",
                1,
                collector=self.name,
            ),
            MetricSample(
                "openhop_repeater_api_response_bytes",
                "Size of the most recent openHop Repeater /api/stats response.",
                "gauge",
                response_bytes,
                collector=self.name,
            ),
        ]

        self._collect_info(payload, metrics)
        self._collect_repeater_counters(payload, metrics)
        self._collect_radio(payload, metrics)
        self._collect_recent_packets(payload, metrics)
        self._collect_neighbors(payload, metrics)
        self._collect_gps(payload, metrics)
        self._collect_sensors(payload, metrics)
        self._collect_extended_mesh_metrics(headers, metrics)
        return metrics

    def _repeater_base_url(self) -> str:
        return f"{self.config.repeater_scheme}://{self.config.repeater_host}:{self.config.repeater_port}"

    def _endpoint_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if not params:
            return f"{self._repeater_base_url()}{normalized}"
        return f"{self._repeater_base_url()}{normalized}?{urlencode(params)}"

    def _fetch_primary_stats(self, headers: dict[str, str]) -> tuple[dict[str, Any], int]:
        try:
            response = requests.get(
                self.config.repeater_url,
                headers=headers,
                timeout=self.config.collection_timeout_seconds,
                verify=self.config.repeater_verify_tls,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Repeater API connection failed: {exc}") from exc

        if response.status_code == 401:
            raise RuntimeError(
                "Repeater API returned HTTP 401; configure a valid Repeater API token "
                "or enable permitted read-only API access"
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Repeater API returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Repeater API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Repeater API returned an unexpected JSON shape")
        return payload, len(response.content)

    def _fetch_optional_endpoint(
        self,
        headers: dict[str, str],
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, int, bool]:
        url = self._endpoint_url(path, params)
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=self.config.collection_timeout_seconds,
                verify=self.config.repeater_verify_tls,
            )
        except requests.RequestException:
            return None, 0, False

        if response.status_code >= 400:
            return None, len(response.content), False

        try:
            raw = response.json()
        except ValueError:
            return None, len(response.content), False

        if not isinstance(raw, dict):
            return None, len(response.content), False

        # Most /api/* endpoints wrap payloads as {success, data}. Handle both forms.
        payload: Any = raw
        if "success" in raw:
            if raw.get("success") is not True:
                return None, len(response.content), False
            payload = raw.get("data")

        if not isinstance(payload, dict):
            return None, len(response.content), False

        return payload, len(response.content), True

    def _collect_extended_mesh_metrics(
        self,
        headers: dict[str, str],
        metrics: list[MetricSample],
    ) -> None:
        endpoints = [
            (
                "packet_stats",
                "/api/packet_stats",
                {"hours": 24},
                self._collect_packet_stats_endpoint,
            ),
            (
                "packet_type_stats",
                "/api/packet_type_stats",
                {"hours": 24},
                self._collect_packet_type_stats_endpoint,
            ),
            (
                "route_stats",
                "/api/route_stats",
                {"hours": 24},
                self._collect_route_stats_endpoint,
            ),
            (
                "lbt_diagnostics",
                "/api/lbt_diagnostics",
                {"hours": 24},
                self._collect_lbt_diagnostics_endpoint,
            ),
            (
                "noise_floor_stats",
                "/api/noise_floor_stats",
                {"hours": 24},
                self._collect_noise_floor_stats_endpoint,
            ),
            (
                "neighbor_links",
                "/api/neighbor_links",
                {"active_within_seconds": 90, "limit": 500},
                self._collect_neighbor_links_endpoint,
            ),
        ]

        for endpoint_name, path, params, collector in endpoints:
            payload, response_bytes, ok = self._fetch_optional_endpoint(
                headers,
                path,
                params,
            )
            metrics.append(
                MetricSample(
                    "openhop_repeater_api_endpoint_up",
                    "Whether an optional openHop Repeater mesh API endpoint returned a valid payload.",
                    "gauge",
                    1.0 if ok else 0.0,
                    labels={"endpoint": endpoint_name},
                    collector=self.name,
                )
            )
            metrics.append(
                MetricSample(
                    "openhop_repeater_api_endpoint_response_bytes",
                    "Size of the most recent optional openHop Repeater endpoint response body.",
                    "gauge",
                    float(response_bytes),
                    labels={"endpoint": endpoint_name},
                    collector=self.name,
                )
            )
            if ok and payload is not None:
                collector(payload, metrics)

    def _collect_packet_stats_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        for metric_name, help_text, key in (
            (
                "openhop_repeater_window_total_packets",
                "Total packets observed in the packet_stats time window.",
                "total_packets",
            ),
            (
                "openhop_repeater_window_transmitted_packets",
                "Transmitted packets observed in the packet_stats time window.",
                "transmitted_packets",
            ),
            (
                "openhop_repeater_window_dropped_packets",
                "Dropped packets observed in the packet_stats time window.",
                "dropped_packets",
            ),
            (
                "openhop_repeater_window_avg_rssi_dbm",
                "Average RSSI in dBm from packet_stats over the selected window.",
                "avg_rssi",
            ),
            (
                "openhop_repeater_window_avg_snr_db",
                "Average SNR in dB from packet_stats over the selected window.",
                "avg_snr",
            ),
            (
                "openhop_repeater_window_avg_score",
                "Average packet score from packet_stats over the selected window.",
                "avg_score",
            ),
            (
                "openhop_repeater_window_avg_payload_bytes",
                "Average payload length in bytes from packet_stats over the selected window.",
                "avg_payload_length",
            ),
            (
                "openhop_repeater_window_avg_tx_delay_milliseconds",
                "Average transmit delay in milliseconds from packet_stats over the selected window.",
                "avg_tx_delay",
            ),
        ):
            self._append(metrics, _metric(metric_name, help_text, "gauge", payload.get(key), collector=self.name))

        packet_types = [entry for entry in _list(payload.get("packet_types")) if isinstance(entry, dict)]
        for entry in packet_types:
            count = _num(entry.get("count"))
            if count is None:
                continue
            raw_type = entry.get("type")
            if _num(raw_type) is not None:
                type_label = str(int(float(raw_type)))
            else:
                type_label = _text(raw_type, 32) or "unknown"
            metrics.append(
                MetricSample(
                    "openhop_repeater_window_packet_type_packets",
                    "Packets by numeric packet type in the packet_stats window.",
                    "gauge",
                    count,
                    labels={"packet_type": type_label},
                    collector=self.name,
                )
            )

        drop_reasons = [entry for entry in _list(payload.get("drop_reasons")) if isinstance(entry, dict)]
        for entry in drop_reasons:
            count = _num(entry.get("count"))
            if count is None:
                continue
            reason = _text(entry.get("reason"), 64) or "unknown"
            metrics.append(
                MetricSample(
                    "openhop_repeater_window_drop_reason_packets",
                    "Dropped packets by drop reason in the packet_stats window.",
                    "gauge",
                    count,
                    labels={"reason": reason},
                    collector=self.name,
                )
            )

    def _collect_packet_type_stats_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        totals = _dict(payload.get("packet_type_totals"))
        for packet_type, value in sorted(totals.items()):
            count = _num(value)
            if count is None:
                continue
            metrics.append(
                MetricSample(
                    "openhop_repeater_window_packet_type_total_packets",
                    "Packets by packet type label in packet_type_stats.",
                    "gauge",
                    count,
                    labels={"packet_type": _text(packet_type, 64) or "unknown"},
                    collector=self.name,
                )
            )

    def _collect_route_stats_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        totals = _dict(payload.get("route_totals"))
        for route_name, value in sorted(totals.items()):
            count = _num(value)
            if count is None:
                continue
            metrics.append(
                MetricSample(
                    "openhop_repeater_window_route_total_packets",
                    "Packets by route label in route_stats.",
                    "gauge",
                    count,
                    labels={"route": _text(route_name, 64) or "unknown"},
                    collector=self.name,
                )
            )

    def _collect_lbt_diagnostics_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        summary = _dict(payload.get("summary"))
        mapping = [
            ("openhop_repeater_lbt_total_transmissions", "Total transmissions included in lbt_diagnostics.", "total_transmissions"),
            ("openhop_repeater_lbt_total_attempts", "Total LBT attempts included in lbt_diagnostics.", "total_attempts"),
            ("openhop_repeater_lbt_first_attempt_success_packets", "Packets that succeeded on first attempt in lbt_diagnostics.", "first_attempt_success"),
            ("openhop_repeater_lbt_retry_packets", "Packets that required one or more retries in lbt_diagnostics.", "retry_packets"),
            ("openhop_repeater_lbt_retry_rate_percent", "Retry packet rate percent from lbt_diagnostics.", "retry_rate_pct"),
            ("openhop_repeater_lbt_first_attempt_success_rate_percent", "First-attempt success rate percent from lbt_diagnostics.", "first_attempt_success_rate_pct"),
            ("openhop_repeater_lbt_avg_attempts", "Average attempts per transmission from lbt_diagnostics.", "avg_attempts"),
            ("openhop_repeater_lbt_median_attempts", "Median attempts per transmission from lbt_diagnostics.", "median_attempts"),
            ("openhop_repeater_lbt_p95_attempts", "p95 attempts per transmission from lbt_diagnostics.", "p95_attempts"),
            ("openhop_repeater_lbt_max_attempts", "Maximum attempts seen in lbt_diagnostics.", "max_attempts"),
            ("openhop_repeater_lbt_attempts_1", "Transmissions requiring one attempt in lbt_diagnostics.", "attempts_1"),
            ("openhop_repeater_lbt_attempts_2", "Transmissions requiring two attempts in lbt_diagnostics.", "attempts_2"),
            ("openhop_repeater_lbt_attempts_3", "Transmissions requiring three attempts in lbt_diagnostics.", "attempts_3"),
            ("openhop_repeater_lbt_attempts_4_plus", "Transmissions requiring four or more attempts in lbt_diagnostics.", "attempts_4_plus"),
            ("openhop_repeater_lbt_attempts_3_plus", "Transmissions requiring three or more attempts in lbt_diagnostics.", "attempts_3_plus"),
            ("openhop_repeater_lbt_attempts_3_plus_percent", "Three-plus attempt percentage from lbt_diagnostics.", "attempts_3_plus_pct"),
            ("openhop_repeater_lbt_attempts_4_plus_percent", "Four-plus attempt percentage from lbt_diagnostics.", "attempts_4_plus_pct"),
            ("openhop_repeater_lbt_failed_transmissions", "Failed transmissions in lbt_diagnostics.", "failed_transmissions"),
            ("openhop_repeater_lbt_busy_channel_events", "Busy-channel events in lbt_diagnostics.", "busy_channel_events"),
            ("openhop_repeater_lbt_severe_contention_count", "Severe-contention transmission count in lbt_diagnostics.", "severe_contention_count"),
            ("openhop_repeater_lbt_severe_contention_percent", "Severe-contention percentage in lbt_diagnostics.", "severe_contention_pct"),
            ("openhop_repeater_lbt_severe_attempt_threshold", "Attempt threshold used for severe contention classification.", "severe_attempt_threshold"),
        ]
        for metric_name, help_text, key in mapping:
            self._append(metrics, _metric(metric_name, help_text, "gauge", summary.get(key), collector=self.name))

        has_data = 1.0 if summary.get("has_lbt_data") is True else 0.0
        metrics.append(
            MetricSample(
                "openhop_repeater_lbt_has_data",
                "Whether lbt_diagnostics reported any transmission data in the selected window.",
                "gauge",
                has_data,
                collector=self.name,
            )
        )

        for entry in [item for item in _list(payload.get("packet_types")) if isinstance(item, dict)]:
            packet_type = entry.get("packet_type")
            label = _text(entry.get("packet_type_label"), 64) or "unknown"
            labels = {
                "packet_type": str(int(float(packet_type))) if _num(packet_type) is not None else "unknown",
                "packet_type_label": label,
            }
            for metric_name, help_text, key in (
                (
                    "openhop_repeater_lbt_packet_type_transmissions",
                    "Transmissions by packet type in lbt_diagnostics.",
                    "transmissions",
                ),
                (
                    "openhop_repeater_lbt_packet_type_retry_packets",
                    "Retry packets by packet type in lbt_diagnostics.",
                    "retry_packets",
                ),
                (
                    "openhop_repeater_lbt_packet_type_retry_rate_percent",
                    "Retry rate percent by packet type in lbt_diagnostics.",
                    "retry_rate_pct",
                ),
            ):
                sample = _metric(metric_name, help_text, "gauge", entry.get(key), collector=self.name, labels=labels)
                self._append(metrics, sample)

    def _collect_noise_floor_stats_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        stats = _dict(payload.get("stats")) if isinstance(payload.get("stats"), dict) else payload
        for metric_name, help_text, key in (
            (
                "openhop_repeater_noise_floor_measurements",
                "Noise floor measurement count in the selected window.",
                "measurement_count",
            ),
            (
                "openhop_repeater_noise_floor_average_dbm",
                "Average noise floor in dBm over the selected window.",
                "avg_noise_floor",
            ),
            (
                "openhop_repeater_noise_floor_minimum_dbm",
                "Minimum noise floor in dBm over the selected window.",
                "min_noise_floor",
            ),
            (
                "openhop_repeater_noise_floor_maximum_dbm",
                "Maximum noise floor in dBm over the selected window.",
                "max_noise_floor",
            ),
        ):
            self._append(metrics, _metric(metric_name, help_text, "gauge", stats.get(key), collector=self.name))

    def _collect_neighbor_links_endpoint(
        self,
        payload: dict[str, Any],
        metrics: list[MetricSample],
    ) -> None:
        links = [entry for entry in _list(payload.get("links")) if isinstance(entry, dict)]

        self._append(
            metrics,
            _metric(
                "openhop_repeater_neighbor_link_active_window_seconds",
                "Configured active-within window used by the neighbor_links query.",
                "gauge",
                payload.get("active_within_seconds"),
                collector=self.name,
            ),
        )
        metrics.append(
            MetricSample(
                "openhop_repeater_neighbor_link_rows",
                "Number of neighbor link snapshots returned by neighbor_links.",
                "gauge",
                len(links),
                collector=self.name,
            )
        )
        metrics.append(
            MetricSample(
                "openhop_repeater_neighbor_link_active_rows",
                "Number of active neighbor link snapshots returned by neighbor_links.",
                "gauge",
                sum(1 for row in links if row.get("active") is True),
                collector=self.name,
            )
        )

        for row in links:
            peer_hash = _text(row.get("peer_hash"), 64)
            if not peer_hash:
                continue
            path_hash_size = row.get("path_hash_size")
            labels = {
                "peer_hash": peer_hash,
                "path_hash_size": (
                    str(int(float(path_hash_size))) if _num(path_hash_size) is not None else "unknown"
                ),
            }
            if row.get("active") is not None:
                metrics.append(
                    MetricSample(
                        "openhop_repeater_neighbor_link_active",
                        "Whether a specific neighbor link is currently marked active.",
                        "gauge",
                        1.0 if row.get("active") is True else 0.0,
                        labels=labels,
                        collector=self.name,
                    )
                )
            for metric_name, help_text, key in (
                (
                    "openhop_repeater_neighbor_link_sample_count",
                    "Observed packet sample count for a specific neighbor link.",
                    "sample_count",
                ),
                (
                    "openhop_repeater_neighbor_link_duplicate_sample_count",
                    "Duplicate packet sample count for a specific neighbor link.",
                    "duplicate_sample_count",
                ),
                (
                    "openhop_repeater_neighbor_link_age_seconds",
                    "Age in seconds of the latest sample for a specific neighbor link.",
                    "age_seconds",
                ),
                (
                    "openhop_repeater_neighbor_link_last_rssi_dbm",
                    "Most recent RSSI for a specific neighbor link.",
                    "last_rssi",
                ),
                (
                    "openhop_repeater_neighbor_link_last_snr_db",
                    "Most recent SNR for a specific neighbor link.",
                    "last_snr",
                ),
                (
                    "openhop_repeater_neighbor_link_last_score",
                    "Most recent score for a specific neighbor link.",
                    "last_score",
                ),
                (
                    "openhop_repeater_neighbor_link_ewma_rssi_dbm",
                    "EWMA RSSI for a specific neighbor link.",
                    "ewma_rssi",
                ),
                (
                    "openhop_repeater_neighbor_link_ewma_snr_db",
                    "EWMA SNR for a specific neighbor link.",
                    "ewma_snr",
                ),
                (
                    "openhop_repeater_neighbor_link_ewma_score",
                    "EWMA score for a specific neighbor link.",
                    "ewma_score",
                ),
                (
                    "openhop_repeater_neighbor_link_best_score",
                    "Best score observed for a specific neighbor link.",
                    "best_score",
                ),
                (
                    "openhop_repeater_neighbor_link_worst_score",
                    "Worst score observed for a specific neighbor link.",
                    "worst_score",
                ),
            ):
                sample = _metric(
                    metric_name,
                    help_text,
                    "gauge",
                    row.get(key),
                    collector=self.name,
                    labels=labels,
                )
                self._append(metrics, sample)

    def _append(self, metrics: list[MetricSample], sample: MetricSample | None) -> None:
        if sample is not None:
            metrics.append(sample)

    def _collect_info(self, payload: dict[str, Any], metrics: list[MetricSample]) -> None:
        config = _dict(payload.get("config"))
        radio_stack = _dict(payload.get("radio_stack"))
        labels = {
            "site_name": _text(payload.get("site_name")),
            "node_name": _text(config.get("node_name")),
            "version": _text(payload.get("version")),
            "core_version": _text(payload.get("core_version")),
            "radio_type": _text(payload.get("radio_type") or config.get("radio_type")),
            "local_hash": _text(payload.get("local_hash")),
        }
        labels = {key: value for key, value in labels.items() if value}
        if labels:
            metrics.append(
                MetricSample(
                    "openhop_repeater_info",
                    "openHop Repeater build and node information.",
                    "info",
                    labels=labels,
                    collector=self.name,
                )
            )

        radio_labels = {
            "status": _text(payload.get("radio_status")),
            "type": _text(payload.get("radio_type")),
            "mode": _text(radio_stack.get("mode")),
            "default_radio": _text(radio_stack.get("default_radio")),
            "tx_mode": _text(radio_stack.get("tx_mode")),
            "fabric": str(bool(radio_stack.get("fabric"))).lower(),
        }
        radio_labels = {key: value for key, value in radio_labels.items() if value}
        if radio_labels:
            metrics.append(
                MetricSample(
                    "openhop_repeater_radio_info",
                    "openHop Repeater radio-stack information.",
                    "info",
                    labels=radio_labels,
                    collector=self.name,
                )
            )

    def _collect_repeater_counters(
        self, payload: dict[str, Any], metrics: list[MetricSample]
    ) -> None:
        collector = self.name
        mapping = [
            ("openhop_repeater_rx_packets_total", "Total packets received by the Repeater.", "counter", "rx_count", 1.0),
            ("openhop_repeater_forwarded_packets_total", "Total packets forwarded by the Repeater.", "counter", "forwarded_count", 1.0),
            ("openhop_repeater_dropped_packets_total", "Total packets dropped by the Repeater.", "counter", "dropped_count", 1.0),
            ("openhop_repeater_received_flood_packets_total", "Total flood packets received by the Repeater.", "counter", "recv_flood_count", 1.0),
            ("openhop_repeater_received_direct_packets_total", "Total direct packets received by the Repeater.", "counter", "recv_direct_count", 1.0),
            ("openhop_repeater_sent_flood_packets_total", "Total flood packets sent by the Repeater.", "counter", "sent_flood_count", 1.0),
            ("openhop_repeater_sent_direct_packets_total", "Total direct packets sent by the Repeater.", "counter", "sent_direct_count", 1.0),
            ("openhop_repeater_flood_duplicates_total", "Total duplicate flood packets detected by the Repeater.", "counter", "flood_dup_count", 1.0),
            ("openhop_repeater_direct_duplicates_total", "Total duplicate direct packets detected by the Repeater.", "counter", "direct_dup_count", 1.0),
            ("openhop_repeater_crc_errors_total", "Total radio CRC errors reported by the Repeater.", "counter", "crc_error_count", 1.0),
            ("openhop_repeater_airtime_seconds_total", "Cumulative transmitted/repeated airtime reported by the Repeater, in seconds.", "counter", "total_airtime_ms", 0.001),
            ("openhop_repeater_rx_airtime_seconds_total", "Cumulative received airtime reported by the Repeater, in seconds.", "counter", "total_rx_airtime_ms", 0.001),
            ("openhop_repeater_uptime_seconds", "Repeater process uptime in seconds.", "gauge", "uptime_seconds", 1.0),
            ("openhop_repeater_rx_per_hour", "Repeater receive rate reported for the current hourly window.", "gauge", "rx_per_hour", 1.0),
            ("openhop_repeater_forwarded_per_hour", "Repeater forwarded-packet rate reported for the current hourly window.", "gauge", "forwarded_per_hour", 1.0),
            ("openhop_repeater_duplicate_cache_size", "Number of packet hashes currently held in the duplicate cache.", "gauge", "duplicate_cache_size", 1.0),
            ("openhop_repeater_duplicate_cache_ttl_seconds", "Duplicate-cache entry time-to-live in seconds.", "gauge", "cache_ttl", 1.0),
            ("openhop_repeater_noise_floor_dbm", "Current radio noise floor in dBm.", "gauge", "noise_floor_dbm", 1.0),
            ("openhop_repeater_current_airtime_seconds", "Current duty-cycle accounting-window airtime in seconds.", "gauge", "current_airtime_ms", 0.001),
            ("openhop_repeater_max_airtime_seconds", "Configured maximum duty-cycle accounting-window airtime in seconds.", "gauge", "max_airtime_ms", 0.001),
            ("openhop_repeater_utilization_percent", "Current Repeater airtime utilisation percentage as reported by /api/stats.", "gauge", "utilization_percent", 1.0),
        ]
        for name, help_text, kind, key, scale in mapping:
            self._append(
                metrics,
                _metric(name, help_text, kind, payload.get(key), collector=collector, scale=scale),
            )

    def _collect_radio(self, payload: dict[str, Any], metrics: list[MetricSample]) -> None:
        status = _text(payload.get("radio_status")).lower()
        if status:
            metrics.append(
                MetricSample(
                    "openhop_repeater_radio_up",
                    "Whether the Repeater radio reports an OK/ready status.",
                    "gauge",
                    1.0 if status in {"ok", "ready", "up", "connected"} else 0.0,
                    collector=self.name,
                )
            )
        radio_stack = _dict(payload.get("radio_stack"))
        radio_ids = _list(radio_stack.get("radio_ids"))
        metrics.append(
            MetricSample(
                "openhop_repeater_radios",
                "Number of radio IDs in the Repeater radio stack.",
                "gauge",
                len(radio_ids),
                collector=self.name,
            )
        )

    def _collect_recent_packets(
        self, payload: dict[str, Any], metrics: list[MetricSample]
    ) -> None:
        packets = [item for item in _list(payload.get("recent_packets")) if isinstance(item, dict)]
        metrics.append(
            MetricSample(
                "openhop_repeater_recent_packets",
                "Number of packet records in the bounded /api/stats recent-packet window.",
                "gauge",
                len(packets),
                collector=self.name,
            )
        )
        if not packets:
            return

        metrics.extend(
            [
                MetricSample(
                    "openhop_repeater_recent_transmitted_packets",
                    "Number of transmitted records in the current recent-packet window.",
                    "gauge",
                    sum(1 for p in packets if p.get("transmitted") is True),
                    collector=self.name,
                ),
                MetricSample(
                    "openhop_repeater_recent_duplicate_packets",
                    "Number of duplicate records in the current recent-packet window.",
                    "gauge",
                    sum(1 for p in packets if p.get("is_duplicate") is True),
                    collector=self.name,
                ),
                MetricSample(
                    "openhop_repeater_recent_lbt_busy_packets",
                    "Number of records in the recent-packet window that reported LBT channel busy.",
                    "gauge",
                    sum(1 for p in packets if p.get("lbt_channel_busy") is True),
                    collector=self.name,
                ),
            ]
        )

        lbt_attempts = [p.get("lbt_attempts") for p in packets if _num(p.get("lbt_attempts")) is not None]
        if lbt_attempts:
            metrics.append(
                MetricSample(
                    "openhop_repeater_recent_lbt_attempts",
                    "Sum of LBT attempts across the current recent-packet window.",
                    "gauge",
                    sum(float(value) for value in lbt_attempts),
                    collector=self.name,
                )
            )

        airtime_values = [float(p["airtime_ms"]) for p in packets if _num(p.get("airtime_ms")) is not None]
        if airtime_values:
            metrics.append(
                MetricSample(
                    "openhop_repeater_recent_airtime_seconds",
                    "Sum of packet airtime across the current recent-packet window.",
                    "gauge",
                    sum(airtime_values) / 1000.0,
                    collector=self.name,
                )
            )

        # Signal metrics are gauges over the bounded recent-packet sample, not counters.
        for field, metric_prefix, unit_help in (
            ("rssi", "openhop_repeater_recent_rssi_dbm", "RSSI in dBm"),
            ("snr", "openhop_repeater_recent_snr_db", "SNR in dB"),
        ):
            values = [float(p[field]) for p in packets if _num(p.get(field)) is not None]
            if not values:
                continue
            for stat, value in (("min", min(values)), ("max", max(values)), ("avg", fmean(values))):
                metrics.append(
                    MetricSample(
                        metric_prefix,
                        f"{unit_help} across the current recent-packet window.",
                        "gauge",
                        value,
                        labels={"stat": stat},
                        collector=self.name,
                    )
                )

        latest = max(
            packets,
            key=lambda p: float(p.get("timestamp") or 0.0) if _num(p.get("timestamp")) is not None else 0.0,
        )
        self._append(
            metrics,
            _metric(
                "openhop_repeater_last_packet_timestamp_seconds",
                "Unix timestamp of the newest packet record in /api/stats.",
                "gauge",
                latest.get("timestamp"),
                collector=self.name,
            ),
        )
        self._append(
            metrics,
            _metric(
                "openhop_repeater_last_packet_rssi_dbm",
                "RSSI of the newest packet record in /api/stats.",
                "gauge",
                latest.get("rssi"),
                collector=self.name,
            ),
        )
        self._append(
            metrics,
            _metric(
                "openhop_repeater_last_packet_snr_db",
                "SNR of the newest packet record in /api/stats.",
                "gauge",
                latest.get("snr"),
                collector=self.name,
            ),
        )

    def _collect_neighbors(self, payload: dict[str, Any], metrics: list[MetricSample]) -> None:
        neighbors = _dict(payload.get("neighbors"))
        metrics.extend(
            [
                MetricSample(
                    "openhop_repeater_neighbors",
                    "Number of neighbours currently present in /api/stats.",
                    "gauge",
                    len(neighbors),
                    collector=self.name,
                ),
                MetricSample(
                    "openhop_repeater_zero_hop_neighbors",
                    "Number of neighbours currently marked zero-hop.",
                    "gauge",
                    sum(
                        1
                        for value in neighbors.values()
                        if isinstance(value, dict) and value.get("zero_hop") is True
                    ),
                    collector=self.name,
                ),
                MetricSample(
                    "openhop_repeater_repeater_neighbors",
                    "Number of neighbour records currently marked as repeaters.",
                    "gauge",
                    sum(
                        1
                        for value in neighbors.values()
                        if isinstance(value, dict) and value.get("is_repeater") is True
                    ),
                    collector=self.name,
                ),
            ]
        )

    def _collect_gps(self, payload: dict[str, Any], metrics: list[MetricSample]) -> None:
        gps = _dict(payload.get("gps"))
        if not gps:
            return
        metrics.append(
            MetricSample(
                "openhop_repeater_gps_enabled",
                "Whether GPS support is enabled in the Repeater.",
                "gauge",
                1.0 if gps.get("enabled") is True else 0.0,
                collector=self.name,
            )
        )
        status = _dict(gps.get("status"))
        fix = _dict(gps.get("fix"))
        metrics.append(
            MetricSample(
                "openhop_repeater_gps_fix_valid",
                "Whether the Repeater currently reports a valid GPS fix.",
                "gauge",
                1.0 if (status.get("fix_valid") is True or fix.get("valid") is True) else 0.0,
                collector=self.name,
            )
        )
        satellites = _dict(gps.get("satellites"))
        self._append(
            metrics,
            _metric(
                "openhop_repeater_gps_satellites_used",
                "Number of GPS satellites currently used for the fix.",
                "gauge",
                satellites.get("used_count"),
                collector=self.name,
            ),
        )
        self._append(
            metrics,
            _metric(
                "openhop_repeater_gps_satellites_in_view",
                "Number of GPS satellites currently in view.",
                "gauge",
                satellites.get("in_view_count"),
                collector=self.name,
            ),
        )
        # Position coordinates are intentionally not exported by default.

    def _collect_sensors(self, payload: dict[str, Any], metrics: list[MetricSample]) -> None:
        sensors = _dict(payload.get("sensors"))
        if not sensors:
            return

        for name, help_text, value in (
            ("openhop_repeater_sensors_enabled", "Whether Repeater sensor polling is enabled.", sensors.get("enabled")),
            ("openhop_repeater_sensors_running", "Whether the Repeater sensor subsystem is currently running.", sensors.get("running")),
            ("openhop_repeater_sensors_configured", "Number of sensors configured in the Repeater.", sensors.get("configured")),
            ("openhop_repeater_sensors_loaded", "Number of sensors loaded by the Repeater.", sensors.get("loaded")),
        ):
            if isinstance(value, bool):
                number: Any = 1.0 if value else 0.0
            else:
                number = value
            self._append(
                metrics,
                _metric(name, help_text, "gauge", number, collector=self.name),
            )

        self._append(
            metrics,
            _metric(
                "openhop_repeater_sensor_poll_interval_seconds",
                "Configured Repeater sensor polling interval in seconds.",
                "gauge",
                sensors.get("poll_interval_seconds"),
                collector=self.name,
            ),
        )

        readings = [item for item in _list(sensors.get("readings")) if isinstance(item, dict)]
        metrics.append(
            MetricSample(
                "openhop_repeater_sensor_readings",
                "Number of sensor reading records returned by /api/stats.",
                "gauge",
                len(readings),
                collector=self.name,
            )
        )

        for reading in readings:
            if reading.get("type") != "hardware_stats" or not isinstance(reading.get("data"), dict):
                continue
            self._collect_hardware_stats(_dict(reading["data"]), metrics)
            break

    def _collect_hardware_stats(
        self, data: dict[str, Any], metrics: list[MetricSample]
    ) -> None:
        cpu = _dict(data.get("cpu"))
        memory = _dict(data.get("memory"))
        disk = _dict(data.get("disk"))
        network = _dict(data.get("network"))
        system = _dict(data.get("system"))
        temperatures = _dict(data.get("temperatures"))

        gauges = [
            ("openhop_system_cpu_usage_percent", "CPU usage reported by the Repeater system-health sensor.", cpu.get("usage_percent")),
            ("openhop_system_cpu_logical_cores", "Logical CPU count reported by the Repeater system-health sensor.", cpu.get("count")),
            ("openhop_system_cpu_frequency_megahertz", "CPU frequency in MHz reported by the Repeater system-health sensor.", cpu.get("frequency")),
            ("openhop_system_memory_total_bytes", "Total physical memory reported by the Repeater system-health sensor.", memory.get("total")),
            ("openhop_system_memory_available_bytes", "Available physical memory reported by the Repeater system-health sensor.", memory.get("available")),
            ("openhop_system_memory_used_bytes", "Used physical memory reported by the Repeater system-health sensor.", memory.get("used")),
            ("openhop_system_memory_usage_percent", "Physical memory usage percentage reported by the Repeater system-health sensor.", memory.get("usage_percent")),
            ("openhop_system_disk_total_bytes", "Filesystem total bytes reported by the Repeater system-health sensor.", disk.get("total")),
            ("openhop_system_disk_used_bytes", "Filesystem used bytes reported by the Repeater system-health sensor.", disk.get("used")),
            ("openhop_system_disk_free_bytes", "Filesystem free bytes reported by the Repeater system-health sensor.", disk.get("free")),
            ("openhop_system_disk_usage_percent", "Filesystem usage percentage reported by the Repeater system-health sensor.", disk.get("usage_percent")),
            ("openhop_system_uptime_seconds", "Host uptime reported by the Repeater system-health sensor.", system.get("uptime")),
            ("openhop_system_boot_time_timestamp_seconds", "Host boot-time Unix timestamp reported by the Repeater system-health sensor.", system.get("boot_time")),
        ]
        for name, help_text, value in gauges:
            self._append(
                metrics,
                _metric(name, help_text, "gauge", value, collector=self.name),
            )

        load_avg = _dict(cpu.get("load_avg"))
        for key, name, help_text in (
            ("1min", "openhop_system_load1", "One-minute host load average from the Repeater system-health sensor."),
            ("5min", "openhop_system_load5", "Five-minute host load average from the Repeater system-health sensor."),
            ("15min", "openhop_system_load15", "Fifteen-minute host load average from the Repeater system-health sensor."),
        ):
            self._append(
                metrics,
                _metric(name, help_text, "gauge", load_avg.get(key), collector=self.name),
            )

        for key, name, help_text in (
            ("bytes_sent", "openhop_system_network_transmit_bytes_total", "Total host network bytes transmitted as reported by the Repeater system-health sensor."),
            ("bytes_recv", "openhop_system_network_receive_bytes_total", "Total host network bytes received as reported by the Repeater system-health sensor."),
            ("packets_sent", "openhop_system_network_transmit_packets_total", "Total host network packets transmitted as reported by the Repeater system-health sensor."),
            ("packets_recv", "openhop_system_network_receive_packets_total", "Total host network packets received as reported by the Repeater system-health sensor."),
        ):
            self._append(
                metrics,
                _metric(name, help_text, "counter", network.get(key), collector=self.name),
            )

        system_labels = {
            "os": _text(system.get("os"), 160),
            "kernel": _text(system.get("kernel"), 120),
            "arch": _text(system.get("arch"), 64),
        }
        system_labels = {key: value for key, value in system_labels.items() if value}
        if system_labels:
            metrics.append(
                MetricSample(
                    "openhop_system_info",
                    "Host operating-system information reported by the Repeater system-health sensor.",
                    "info",
                    labels=system_labels,
                    collector=self.name,
                )
            )

        # Temperature sensor names are configuration-bounded and low-cardinality.
        for sensor_name, value in sorted(temperatures.items()):
            number = _num(value)
            if number is None:
                continue
            metrics.append(
                MetricSample(
                    "openhop_system_temperature_celsius",
                    "Host temperature readings reported by the Repeater system-health sensor.",
                    "gauge",
                    number,
                    labels={"sensor": str(sensor_name)[:64]},
                    collector=self.name,
                )
            )

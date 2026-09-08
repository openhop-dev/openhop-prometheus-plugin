import json

from openhop_prometheus_plugin.collectors.repeater import RepeaterCollector
from openhop_prometheus_plugin.config import load_config


class Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


def _config(tmp_path, **values):
    (tmp_path / "config.json").write_text(json.dumps(values))
    return load_config(tmp_path)


def realistic_stats_payload():
    return {
        "local_hash": "0xfe07",
        "duplicate_cache_size": 206,
        "cache_ttl": 3600,
        "rx_count": 2468,
        "forwarded_count": 700,
        "dropped_count": 1767,
        "recv_flood_count": 2465,
        "recv_direct_count": 3,
        "sent_flood_count": 700,
        "sent_direct_count": 0,
        "flood_dup_count": 1764,
        "direct_dup_count": 0,
        "rx_per_hour": 50,
        "forwarded_per_hour": 27,
        "uptime_seconds": 12051.3,
        "noise_floor_dbm": -102.45,
        "crc_error_count": 466,
        "current_airtime_ms": 4919.296,
        "max_airtime_ms": 3600,
        "utilization_percent": 136.647,
        "total_airtime_ms": 599617.536,
        "total_rx_airtime_ms": 2115375.104,
        "radio_status": "ok",
        "radio_type": "sx1262",
        "site_name": "admin",
        "version": "1.1.2.dev334",
        "core_version": "1.1.3.dev28",
        "config": {"node_name": "NBEOPNH03", "radio_type": "sx1262"},
        "radio_stack": {
            "mode": "single",
            "radio_ids": ["radio0"],
            "default_radio": "radio0",
            "tx_mode": "bridge",
            "fabric": False,
        },
        "neighbors": {
            "a": {"is_repeater": True, "zero_hop": True},
            "b": {"is_repeater": True, "zero_hop": False},
            "c": {"is_repeater": False, "zero_hop": False},
        },
        "recent_packets": [
            {
                "timestamp": 1788859170.1,
                "rssi": -103,
                "snr": -9.75,
                "airtime_ms": 541.696,
                "transmitted": True,
                "is_duplicate": False,
                "lbt_attempts": 1,
                "lbt_channel_busy": True,
                "payload": "secret-payload",
                "raw_packet": "raw-secret",
            },
            {
                "timestamp": 1788859187.6,
                "rssi": -75,
                "snr": 10.75,
                "airtime_ms": 1197.056,
                "transmitted": False,
                "is_duplicate": True,
                "lbt_attempts": 0,
                "lbt_channel_busy": False,
            },
        ],
        "gps": {
            "enabled": False,
            "status": {"fix_valid": False},
            "fix": {"valid": False},
            "position": {"latitude": 50.7, "longitude": -1.8},
            "satellites": {"used_count": None, "in_view_count": None},
        },
        "sensors": {
            "enabled": True,
            "poll_interval_seconds": 10.0,
            "configured": 1,
            "loaded": 1,
            "running": True,
            "readings": [
                {
                    "name": "system-health",
                    "type": "hardware_stats",
                    "ok": True,
                    "data": {
                        "cpu": {
                            "usage_percent": 2.4,
                            "count": 4,
                            "frequency": 1500.0,
                            "load_avg": {"1min": 0.04, "5min": 0.03, "15min": 0.01},
                        },
                        "memory": {
                            "total": 8454012928,
                            "available": 7725514752,
                            "used": 728498176,
                            "usage_percent": 8.6,
                        },
                        "disk": {
                            "total": 30803668992,
                            "used": 6365458432,
                            "free": 23140253696,
                            "usage_percent": 20.7,
                        },
                        "network": {
                            "bytes_sent": 46727615966,
                            "bytes_recv": 5175594557,
                            "packets_sent": 11217419,
                            "packets_recv": 22832186,
                        },
                        "system": {
                            "uptime": 4019095.2,
                            "boot_time": 1784840592.0,
                            "os": "Debian GNU/Linux 13 (trixie)",
                            "kernel": "6.18.34+rpt-rpi-2712",
                            "arch": "aarch64",
                        },
                        "temperatures": {"cpu_thermal": 41.9, "rp1_adc": 47.918},
                    },
                }
            ],
        },
        # These are intentionally sensitive/high-cardinality and must not leak.
        "public_key": "fe07e055947ad1392a97de57cad58f9d3ffd7224125390a92c4c940ca2b6c5e5",
    }


def test_repeater_maps_current_stats_schema(monkeypatch, tmp_path):
    captured = {}

    def get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return Response(realistic_stats_payload())

    monkeypatch.setattr("openhop_prometheus_plugin.collectors.repeater.requests.get", get)
    cfg = _config(tmp_path, repeater_api_token="secret-token")
    result = RepeaterCollector(cfg).collect_safe()
    assert result.ok, result.error

    metrics = {(metric.name, tuple(sorted(metric.labels.items()))): metric for metric in result.metrics}
    by_name = {metric.name: metric for metric in result.metrics if not metric.labels}

    assert by_name["openhop_repeater_up"].value == 1
    assert by_name["openhop_repeater_rx_packets_total"].value == 2468
    assert by_name["openhop_repeater_forwarded_packets_total"].value == 700
    assert by_name["openhop_repeater_crc_errors_total"].value == 466
    assert round(by_name["openhop_repeater_airtime_seconds_total"].value, 3) == 599.618
    assert by_name["openhop_repeater_neighbors"].value == 3
    assert by_name["openhop_repeater_zero_hop_neighbors"].value == 1
    assert by_name["openhop_repeater_recent_duplicate_packets"].value == 1
    assert by_name["openhop_repeater_last_packet_rssi_dbm"].value == -75
    assert by_name["openhop_system_cpu_usage_percent"].value == 2.4
    assert by_name["openhop_system_memory_total_bytes"].value == 8454012928
    assert by_name["openhop_system_network_transmit_bytes_total"].value == 46727615966
    assert by_name["openhop_repeater_gps_fix_valid"].value == 0

    info = next(metric for metric in result.metrics if metric.name == "openhop_repeater_info")
    assert info.labels["node_name"] == "NBEOPNH03"
    assert info.labels["version"] == "1.1.2.dev334"
    assert captured["headers"]["X-API-Key"] == "secret-token"
    assert "Authorization" not in captured["headers"]

    # Sensitive values and per-neighbour identifiers are never labels.
    exported_text = json.dumps([metric.ui_dict() for metric in result.metrics])
    assert "fe07e055947ad1392a97de57cad58f9d3ffd7224125390a92c4c940ca2b6c5e5" not in exported_text
    assert "secret-payload" not in exported_text
    assert "raw-secret" not in exported_text
    assert "latitude" not in exported_text
    assert "longitude" not in exported_text


def test_recent_signal_metric_uses_bounded_stat_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "openhop_prometheus_plugin.collectors.repeater.requests.get",
        lambda *args, **kwargs: Response(realistic_stats_payload()),
    )
    result = RepeaterCollector(_config(tmp_path)).collect_safe()
    assert result.ok
    rssi = {
        metric.labels["stat"]: metric.value
        for metric in result.metrics
        if metric.name == "openhop_repeater_recent_rssi_dbm"
    }
    assert rssi == {"min": -103, "max": -75, "avg": -89}


def test_repeater_401_reports_auth_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "openhop_prometheus_plugin.collectors.repeater.requests.get",
        lambda *args, **kwargs: Response({}, status=401),
    )
    result = RepeaterCollector(_config(tmp_path)).collect_safe()
    assert not result.ok
    assert "401" in result.error
    assert result.failure_metrics[0].name == "openhop_repeater_up"
    assert result.failure_metrics[0].value == 0


def test_repeater_token_allows_bearer_prefix_input(monkeypatch, tmp_path):
    captured = {}

    def get(_url, **kwargs):
        captured["headers"] = kwargs["headers"]
        return Response(realistic_stats_payload())

    monkeypatch.setattr("openhop_prometheus_plugin.collectors.repeater.requests.get", get)
    cfg = _config(tmp_path, repeater_api_token="Bearer pasted-token")
    result = RepeaterCollector(cfg).collect_safe()
    assert result.ok, result.error
    assert captured["headers"]["X-API-Key"] == "pasted-token"
    assert "Authorization" not in captured["headers"]


def test_repeater_collects_extended_mesh_endpoints(monkeypatch, tmp_path):
    calls = []

    endpoint_payloads = {
        "/api/stats": realistic_stats_payload(),
        "/api/packet_stats?hours=24": {
            "success": True,
            "data": {
                "total_packets": 25,
                "transmitted_packets": 20,
                "dropped_packets": 5,
                "avg_rssi": -95.5,
                "avg_snr": 4.2,
                "avg_score": 0.78,
                "avg_payload_length": 41.3,
                "avg_tx_delay": 13.7,
                "packet_types": [{"type": 4, "count": 9}],
                "drop_reasons": [{"reason": "duty_cycle", "count": 3}],
            },
        },
        "/api/packet_type_stats?hours=24": {
            "success": True,
            "data": {"packet_type_totals": {"Node Advertisement (ADVERT)": 12, "Response (RESPONSE)": 7}},
        },
        "/api/route_stats?hours=24": {
            "success": True,
            "data": {"route_totals": {"Flood": 17, "Direct": 8}},
        },
        "/api/lbt_diagnostics?hours=24": {
            "success": True,
            "data": {
                "summary": {
                    "total_transmissions": 10,
                    "total_attempts": 14,
                    "first_attempt_success": 7,
                    "retry_packets": 3,
                    "retry_rate_pct": 30.0,
                    "first_attempt_success_rate_pct": 70.0,
                    "avg_attempts": 1.4,
                    "median_attempts": 1.0,
                    "p95_attempts": 3.0,
                    "max_attempts": 4,
                    "attempts_1": 7,
                    "attempts_2": 2,
                    "attempts_3": 1,
                    "attempts_4_plus": 0,
                    "attempts_3_plus": 1,
                    "attempts_3_plus_pct": 10.0,
                    "attempts_4_plus_pct": 0.0,
                    "failed_transmissions": 0,
                    "busy_channel_events": 3,
                    "severe_contention_count": 0,
                    "severe_contention_pct": 0.0,
                    "severe_attempt_threshold": 4,
                    "has_lbt_data": True,
                },
                "packet_types": [
                    {
                        "packet_type": 1,
                        "packet_type_label": "Response (RESPONSE)",
                        "transmissions": 8,
                        "retry_packets": 3,
                        "retry_rate_pct": 37.5,
                    }
                ],
            },
        },
        "/api/noise_floor_stats?hours=24": {
            "success": True,
            "data": {
                "stats": {
                    "measurement_count": 240,
                    "avg_noise_floor": -102.4,
                    "min_noise_floor": -109.3,
                    "max_noise_floor": -96.7,
                }
            },
        },
        "/api/neighbor_links?active_within_seconds=90&limit=500": {
            "success": True,
            "data": {
                "active_within_seconds": 90,
                "links": [
                    {
                        "peer_hash": "AB",
                        "path_hash_size": 1,
                        "sample_count": 12,
                        "duplicate_sample_count": 2,
                        "age_seconds": 4.5,
                        "active": True,
                        "last_rssi": -93.0,
                        "last_snr": 6.0,
                        "last_score": 0.81,
                        "ewma_rssi": -94.2,
                        "ewma_snr": 5.8,
                        "ewma_score": 0.77,
                        "best_score": 0.92,
                        "worst_score": 0.31,
                    }
                ],
            },
        },
    }

    def get(url, **kwargs):
        calls.append(url)
        for suffix, payload in endpoint_payloads.items():
            if url.endswith(suffix):
                return Response(payload)
        raise AssertionError(f"Unexpected endpoint call: {url}")

    monkeypatch.setattr("openhop_prometheus_plugin.collectors.repeater.requests.get", get)
    result = RepeaterCollector(_config(tmp_path, repeater_api_token="abc-token")).collect_safe()

    assert result.ok, result.error
    metrics = {(m.name, tuple(sorted(m.labels.items()))): m for m in result.metrics}
    by_name = {m.name: m for m in result.metrics if not m.labels}

    assert by_name["openhop_repeater_window_total_packets"].value == 25
    assert by_name["openhop_repeater_lbt_retry_rate_percent"].value == 30.0
    assert by_name["openhop_repeater_noise_floor_average_dbm"].value == -102.4
    assert by_name["openhop_repeater_neighbor_link_rows"].value == 1

    assert metrics[
        (
            "openhop_repeater_window_packet_type_total_packets",
            (("packet_type", "Node Advertisement (ADVERT)"),),
        )
    ].value == 12
    assert metrics[
        ("openhop_repeater_window_route_total_packets", (("route", "Flood"),))
    ].value == 17
    assert metrics[
        (
            "openhop_repeater_neighbor_link_sample_count",
            (("path_hash_size", "1"), ("peer_hash", "AB")),
        )
    ].value == 12

    for endpoint_name in (
        "packet_stats",
        "packet_type_stats",
        "route_stats",
        "lbt_diagnostics",
        "noise_floor_stats",
        "neighbor_links",
    ):
        key = ("openhop_repeater_api_endpoint_up", (("endpoint", endpoint_name),))
        assert metrics[key].value == 1.0

    assert any(url.endswith("/api/neighbor_links?active_within_seconds=90&limit=500") for url in calls)

(() => {
  "use strict";

  const PLUGIN_ID = "openhop.prometheus";
  const API = "/api/plugins/settings";
  const AUTO_REFRESH_MS = 5000;

  const defaults = {
    bind_host: "127.0.0.1",
    port: 9109,
    metrics_path: "/metrics",
    collection_interval_seconds: 30,
    collection_timeout_seconds: 10,
    repeater_enabled: true,
    repeater_scheme: "http",
    repeater_host: "127.0.0.1",
    repeater_port: 8000,
    repeater_stats_path: "/api/stats",
    repeater_api_token: "",
    repeater_verify_tls: true,
    test_endpoint_request_id: "",
    test_repeater_request_id: "",
    refresh_request_id: ""
  };

  const collectorLabels = {
    plugin: "Plugin runtime",
    repeater: "openHop Repeater"
  };

  const fieldIds = [
    "bind_host", "port", "metrics_path", "collection_interval_seconds",
    "collection_timeout_seconds", "repeater_enabled", "repeater_scheme",
    "repeater_host", "repeater_port", "repeater_stats_path",
    "repeater_api_token", "repeater_verify_tls"
  ];

  let currentConfig = { ...defaults };
  let runtime = {};
  let formDirty = false;

  const $ = (id) => document.getElementById(id);
  const notice = $("notice");
  const settingsForm = $("settings-form");

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setNotice(message, kind = "") {
    notice.textContent = message || "";
    notice.className = `notice ${kind}`.trim();
  }

  function repeaterJwt() {
    try {
      return window.localStorage.getItem("pymc_jwt_token") || "";
    } catch (_) {
      return "";
    }
  }

  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    const token = repeaterJwt();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
      throw new Error(
        token
          ? "Your openHop dashboard session has expired. Log in again and reopen Prometheus."
          : "Authentication is required. Open Prometheus from the logged-in openHop dashboard."
      );
    }
    return response;
  }

  function configFromResponse(payload) {
    if (payload && typeof payload === "object" && payload.config && typeof payload.config === "object") {
      return payload.config;
    }
    return payload && typeof payload === "object" ? payload : {};
  }

  function stripRuntime(config) {
    const clean = { ...config };
    delete clean._runtime;
    return clean;
  }

  // Strip removed v0.1 keys as the UI saves, so upgrades naturally clean them up.
  function supportedConfig(config) {
    const out = {};
    Object.keys(defaults).forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(config, key)) out[key] = config[key];
    });
    return out;
  }

  async function fetchConfig() {
    const response = await apiFetch(`${API}?id=${encodeURIComponent(PLUGIN_ID)}`);
    if (!response.ok) throw new Error(`Prometheus plugin data load failed (HTTP ${response.status})`);
    return configFromResponse(await response.json());
  }

  async function postConfig(config, { restart = true } = {}) {
    const response = await apiFetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: PLUGIN_ID, config: supportedConfig(config), restart })
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Save failed (HTTP ${response.status})${text ? `: ${text}` : ""}`);
    }
    currentConfig = { ...defaults, ...supportedConfig(config) };
    return response;
  }

  function setValue(id, value) {
    const el = $(id);
    if (!el) return;
    if (el.type === "checkbox") el.checked = Boolean(value);
    else el.value = value === null || value === undefined ? "" : String(value);
  }

  function populateSettings(config) {
    const cfg = { ...defaults, ...supportedConfig(config) };
    fieldIds.forEach((id) => setValue(id, cfg[id]));
    updateBindWarning();
    formDirty = false;
  }

  function valueNumber(id) {
    const raw = $(id).value.trim();
    const value = Number(raw);
    if (!Number.isFinite(value)) throw new Error(`${id.replaceAll("_", " ")} must be a number.`);
    return value;
  }

  function validatePath(value, label) {
    const raw = String(value || "").trim();
    if (!raw.startsWith("/")) throw new Error(`${label} must start with /.`);
    if (raw.includes("..") || raw.includes("//") || raw.includes("?") || raw.includes("#")) {
      throw new Error(`${label} must be a simple URL path.`);
    }
    return raw.length > 1 && raw.endsWith("/") ? raw.slice(0, -1) : raw;
  }

  function buildConfig() {
    const port = valueNumber("port");
    const interval = valueNumber("collection_interval_seconds");
    const timeout = valueNumber("collection_timeout_seconds");
    const repeaterPort = valueNumber("repeater_port");

    if (port < 1024 || port > 65535) throw new Error("Prometheus port must be between 1024 and 65535.");
    if (interval < 5 || interval > 3600) throw new Error("Collection interval must be between 5 and 3600 seconds.");
    if (timeout < 0.5 || timeout > 60 || timeout >= interval) {
      throw new Error("Collection timeout must be at least 0.5 seconds and less than the collection interval.");
    }
    if (repeaterPort < 1 || repeaterPort > 65535) throw new Error("Repeater port is invalid.");

    const config = {
      ...supportedConfig(stripRuntime(currentConfig)),
      bind_host: $("bind_host").value.trim(),
      port,
      metrics_path: validatePath($("metrics_path").value, "Metrics path"),
      collection_interval_seconds: interval,
      collection_timeout_seconds: timeout,
      repeater_enabled: $("repeater_enabled").checked,
      repeater_scheme: $("repeater_scheme").value,
      repeater_host: $("repeater_host").value.trim(),
      repeater_port: repeaterPort,
      repeater_stats_path: validatePath($("repeater_stats_path").value, "Repeater stats path"),
      repeater_api_token: $("repeater_api_token").value.trim(),
      repeater_verify_tls: $("repeater_verify_tls").checked
    };
    if (!config.bind_host) throw new Error("Bind address is required.");
    if (!config.repeater_host) throw new Error("Repeater host is required.");
    return config;
  }

  function requestId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function requestAction(field, label) {
    try {
      const config = supportedConfig(stripRuntime(currentConfig));
      config[field] = requestId();
      setNotice(`${label} requested. The plugin is restarting…`);
      await postConfig(config, { restart: true });
      setNotice(`${label} requested. Results will appear when the plugin is back online.`, "ok");
      setTimeout(() => loadConfig(false).catch(() => {}), 1800);
      setTimeout(() => loadConfig(false).catch(() => {}), 4000);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error), "error");
    }
  }

  function setTab(name) {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  }

  function statusClass(status) {
    if (["healthy", "ok"].includes(status)) return "good";
    if (["degraded", "warning", "stale"].includes(status)) return "warn";
    if (["error", "failed", "stopped"].includes(status)) return "bad";
    return "neutral";
  }

  function fmtDurationMs(ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms < 1000) return `${Math.round(ms)} ms`;
    return `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)} s`;
  }

  function fmtNumber(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (Math.abs(n) >= 1e9) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (Math.abs(n) >= 1e6) return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
    if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (Number.isInteger(n)) return n.toLocaleString();
    return n.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function fmtAge(iso) {
    if (!iso) return "never";
    const timestamp = Date.parse(iso);
    if (!Number.isFinite(timestamp)) return "unknown";
    const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  function endpointHost() {
    const bind = currentConfig.bind_host || defaults.bind_host;
    if (["0.0.0.0", "::"].includes(bind)) return window.location.hostname || "REPEATER_IP";
    if (["127.0.0.1", "localhost", "::1"].includes(bind) && window.location.hostname) {
      return bind;
    }
    return bind;
  }

  function endpointUrl(path = null) {
    const host = endpointHost();
    const displayHost = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
    return `http://${displayHost}:${currentConfig.port || defaults.port}${path || currentConfig.metrics_path || defaults.metrics_path}`;
  }

  function promTarget() {
    const bind = currentConfig.bind_host || defaults.bind_host;
    const host = ["127.0.0.1", "localhost", "::1", "0.0.0.0", "::"].includes(bind)
      ? (window.location.hostname || "REPEATER_IP")
      : bind;
    return `${host}:${currentConfig.port || defaults.port}`;
  }

  function updateCountdown() {
    const target = runtime.next_collection_at ? Date.parse(runtime.next_collection_at) : NaN;
    if (!Number.isFinite(target)) {
      $("next-collection").textContent = "—";
      return;
    }
    const seconds = Math.max(0, Math.ceil((target - Date.now()) / 1000));
    $("next-collection").textContent = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }

  function metricByName(name, labels = null) {
    return (runtime.metrics || []).find((metric) => {
      if (metric.name !== name) return false;
      if (!labels) return true;
      return Object.entries(labels).every(([key, value]) => metric.labels && metric.labels[key] === value);
    });
  }

  function renderOverview() {
    const status = runtime.status || "starting";
    const cls = statusClass(status);
    $("global-status").className = `status-pill ${cls}`;
    $("global-status").innerHTML = `<span class="status-dot"></span>${esc(status)}`;
    $("overview-health").className = `health-badge ${cls}`;
    $("overview-health").textContent = status;
    $("overview-title").textContent = status === "healthy"
      ? "Prometheus exporter is healthy"
      : status === "degraded"
        ? "Exporter is serving cached metrics with Repeater issues"
        : status === "stopped" ? "Plugin runtime is stopped" : "Prometheus exporter is starting";
    $("overview-summary").textContent = runtime.last_error || `${runtime.collectors_healthy || 0} of ${runtime.collector_count || 0} enabled collectors are healthy.`;
    $("overview-endpoint").textContent = endpointUrl();
    $("last-collection").textContent = `Last collection ${fmtAge(runtime.last_collection_completed)}`;
    $("kpi-metrics").textContent = fmtNumber(runtime.metric_count || 0);
    $("kpi-families").textContent = `${fmtNumber(runtime.metric_family_count || 0)} metric families`;
    $("kpi-collectors").textContent = `${runtime.collectors_healthy || 0}/${runtime.collector_count || 0}`;
    $("kpi-duration").textContent = fmtDurationMs(runtime.collection_duration_ms);
    $("kpi-scrapes").textContent = fmtNumber(runtime.scrapes_total || 0);
    $("kpi-scrape-errors").textContent = `${fmtNumber(runtime.scrape_errors_total || 0)} errors`;
    $("tab-metric-count").textContent = runtime.metric_count || 0;

    const collectors = (runtime.collectors || []).filter((item) => item.enabled);
    $("overview-collectors").classList.toggle("empty-state", collectors.length === 0);
    $("overview-collectors").innerHTML = collectors.length
      ? collectors.map((item) => {
          const c = item.healthy ? "good" : item.last_success ? "warn" : "bad";
          const subtitle = item.healthy ? `${item.metric_count} samples · ${fmtDurationMs(item.duration_ms)}` : item.error || "No successful collection yet";
          return `<div class="collector-row"><span class="health-light ${c}"></span><div><strong>${esc(collectorLabels[item.name] || item.name)}</strong><small>${esc(subtitle)}</small></div><div class="collector-meta">${esc(fmtAge(item.last_success))}</div></div>`;
        }).join("")
      : "No collector data yet.";

    const highlights = [
      [metricByName("openhop_repeater_noise_floor_dbm"), "Noise floor", " dBm"],
      [metricByName("openhop_repeater_last_packet_rssi_dbm"), "Last RSSI", " dBm"],
      [metricByName("openhop_repeater_last_packet_snr_db"), "Last SNR", " dB"],
      [metricByName("openhop_repeater_rx_packets_total"), "RX packets", ""],
      [metricByName("openhop_repeater_forwarded_packets_total"), "Forwarded", ""],
      [metricByName("openhop_repeater_utilization_percent"), "Airtime use", "%"]
    ].filter(([metric]) => metric);
    $("rf-highlights").classList.toggle("empty-state", highlights.length === 0);
    $("rf-highlights").innerHTML = highlights.length
      ? highlights.map(([metric, label, unit]) => `<div class="highlight"><span>${esc(label)}</span><strong>${esc(fmtNumber(metric.value))}${esc(unit)}</strong></div>`).join("")
      : "No Repeater metrics yet.";

    renderBindGuidance();
  }

  function renderBindGuidance() {
    const bind = currentConfig.bind_host || defaults.bind_host;
    const loopback = ["127.0.0.1", "localhost", "::1"].includes(bind);
    const wildcard = ["0.0.0.0", "::"].includes(bind);
    let html;
    if (loopback) {
      html = `<strong>Loopback only.</strong> The exporter is safely reachable only from the Repeater host. A remote Prometheus server needs a trusted LAN/VPN bind address.`;
    } else if (wildcard) {
      html = `<strong>Listening on all interfaces.</strong> Restrict port <code>${esc(currentConfig.port)}</code> with a firewall, VPN or authenticated reverse proxy.`;
    } else {
      html = `<strong>Network bound.</strong> Confirm that only your trusted monitoring network can reach <code>${esc(bind)}:${esc(currentConfig.port)}</code>.`;
    }
    $("bind-guidance").innerHTML = html;
    updateBindWarning();
  }

  function renderMetrics() {
    const metrics = runtime.metrics || [];
    const collectorSelect = $("metric-collector");
    const existing = collectorSelect.value;
    const collectors = [...new Set(metrics.map((metric) => metric.collector).filter(Boolean))].sort();
    collectorSelect.innerHTML = `<option value="">All collectors</option>${collectors.map((name) => `<option value="${esc(name)}">${esc(collectorLabels[name] || name)}</option>`).join("")}`;
    collectorSelect.value = collectors.includes(existing) ? existing : "";

    const query = $("metric-search").value.trim().toLowerCase();
    const collector = collectorSelect.value;
    const type = $("metric-type").value;
    const filtered = metrics.filter((metric) => {
      const haystack = `${metric.name} ${metric.collector} ${JSON.stringify(metric.labels || {})}`.toLowerCase();
      return (!query || haystack.includes(query)) && (!collector || metric.collector === collector) && (!type || metric.type === type);
    });

    $("metrics-body").innerHTML = filtered.map((metric) => {
      const labels = Object.entries(metric.labels || {}).map(([key, value]) => `<span class="label-chip">${esc(key)}=${esc(value)}</span>`).join("") || "—";
      return `<tr><td class="metric-name">${esc(metric.name)}</td><td><span class="metric-type ${esc(metric.type)}">${esc(metric.type)}</span></td><td class="metric-value">${esc(fmtNumber(metric.value))}</td><td>${labels}</td><td>${esc(collectorLabels[metric.collector] || metric.collector || "—")}</td></tr>`;
    }).join("");
    $("metrics-empty").classList.toggle("hidden", filtered.length !== 0);
  }

  function renderCollectors() {
    const collectors = (runtime.collectors || []).filter((collector) => collector.enabled);
    $("collector-cards").innerHTML = collectors.length
      ? collectors.map((collector) => {
          const cls = collector.healthy ? "good" : collector.last_success ? "warn" : "bad";
          const button = collector.name === "repeater"
            ? `<button class="button compact secondary" data-test-collector="repeater" type="button">Test Repeater</button>` : "";
          return `<article class="collector-card card"><div class="collector-card-head"><h3>${esc(collectorLabels[collector.name] || collector.name)}</h3><span class="collector-state ${cls}">${collector.healthy ? "healthy" : "unhealthy"}</span></div><div class="collector-stats"><div class="collector-stat"><span>Samples</span><strong>${esc(fmtNumber(collector.metric_count))}</strong></div><div class="collector-stat"><span>Duration</span><strong>${esc(fmtDurationMs(collector.duration_ms))}</strong></div><div class="collector-stat"><span>Last success</span><strong>${esc(fmtAge(collector.last_success))}</strong></div></div><div class="collector-error">${collector.error ? esc(collector.error) : ""}</div>${button}</article>`;
        }).join("")
      : `<div class="empty-state">No enabled collectors are reporting runtime state yet.</div>`;

    document.querySelectorAll("[data-test-collector='repeater']").forEach((button) => button.addEventListener("click", () => requestAction("test_repeater_request_id", "Repeater connection test")));
    renderActionResults();
  }

  function actionResultHtml(label, result) {
    if (!result || typeof result !== "object") return "";
    const cls = result.status === "ok" ? "good" : "bad";
    return `<div class="test-result ${cls}"><strong>${esc(label)}</strong><br>${esc(result.message || result.status)} <small>· ${esc(fmtAge(result.completed_at))}</small></div>`;
  }

  function renderActionResults() {
    const actions = runtime.actions || {};
    const items = [
      actionResultHtml("Prometheus endpoint", actions.endpoint),
      actionResultHtml("openHop Repeater", actions.repeater),
      actionResultHtml("Manual refresh", actions.refresh)
    ].filter(Boolean);
    $("test-results").classList.toggle("empty-state", items.length === 0);
    $("test-results").innerHTML = items.length ? items.join("") : "No connection tests have been run yet.";

    const endpoint = actions.endpoint;
    const endpointBox = $("endpoint-test-result");
    if (!endpoint) {
      endpointBox.className = "test-result neutral";
      endpointBox.textContent = "No endpoint test has been run yet.";
    } else {
      endpointBox.className = `test-result ${endpoint.status === "ok" ? "good" : "bad"}`;
      endpointBox.textContent = `${endpoint.message || endpoint.status} · ${fmtAge(endpoint.completed_at)}`;
    }
  }

  function renderPrometheus() {
    $("prom-metrics-url").textContent = endpointUrl();
    $("prom-health-url").textContent = endpointUrl("/healthz");
    const path = currentConfig.metrics_path || defaults.metrics_path;
    $("prom-config").textContent = [
      "scrape_configs:",
      "  - job_name: openhop",
      `    metrics_path: ${path}`,
      "    static_configs:",
      "      - targets:",
      `          - ${promTarget()}`
    ].join("\n");

    const bind = currentConfig.bind_host || defaults.bind_host;
    const warning = $("prom-bind-warning");
    if (["127.0.0.1", "localhost", "::1"].includes(bind)) {
      warning.className = "warning-box warn";
      warning.innerHTML = "The exporter is loopback-only. A Prometheus server on another host cannot scrape it until you change the bind address in Settings.";
    } else {
      warning.className = "warning-box good";
      warning.innerHTML = "The exporter is network reachable. Restrict the scrape port to your trusted monitoring network; the endpoint has no application-level authentication.";
    }
    renderActionResults();
  }

  function render() {
    renderOverview();
    renderMetrics();
    renderCollectors();
    renderPrometheus();
    updateCountdown();
  }

  async function loadConfig(populate = false) {
    try {
      const config = await fetchConfig();
      currentConfig = { ...defaults, ...supportedConfig(stripRuntime(config)) };
      runtime = config._runtime && typeof config._runtime === "object" ? config._runtime : {};
      if (populate || !formDirty) populateSettings(currentConfig);
      render();
      if (!runtime.generated_at) setNotice("Plugin runtime data is not available yet. The process may still be starting.", "warn");
      else if (notice.classList.contains("error")) setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error), "error");
    }
  }

  async function copyTarget(id) {
    const el = $(id);
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.textContent || "");
      setNotice("Copied to clipboard.", "ok");
      setTimeout(() => { if (notice.textContent === "Copied to clipboard.") setNotice(""); }, 1200);
    } catch (_) {
      setNotice("Clipboard access was unavailable; select and copy the value manually.", "warn");
    }
  }

  function updateBindWarning() {
    if (!$("bind_host")) return;
    const bind = $("bind_host").value.trim();
    const box = $("settings-bind-warning");
    if (bind && !["127.0.0.1", "localhost", "::1"].includes(bind)) {
      box.className = "warning-box warn";
      box.textContent = "This bind address can expose Prometheus metrics beyond the local host. Restrict the port with network controls.";
    } else {
      box.className = "warning-box hidden";
      box.textContent = "";
    }
  }

  document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
  document.querySelectorAll("[data-tab-jump]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tabJump)));
  document.querySelectorAll(".copy").forEach((button) => button.addEventListener("click", () => copyTarget(button.dataset.copyTarget)));
  ["metric-search", "metric-collector", "metric-type"].forEach((id) => $(id).addEventListener("input", renderMetrics));
  $("metric-collector").addEventListener("change", renderMetrics);
  $("metric-type").addEventListener("change", renderMetrics);

  $("refresh-now").addEventListener("click", () => requestAction("refresh_request_id", "Metrics refresh"));
  $("metrics-refresh").addEventListener("click", () => requestAction("refresh_request_id", "Metrics refresh"));
  $("overview-test-endpoint").addEventListener("click", () => requestAction("test_endpoint_request_id", "Prometheus endpoint test"));
  $("prom-test-endpoint").addEventListener("click", () => requestAction("test_endpoint_request_id", "Prometheus endpoint test"));
  $("test-repeater").addEventListener("click", () => requestAction("test_repeater_request_id", "Repeater connection test"));

  fieldIds.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", () => { formDirty = true; if (id === "bind_host") updateBindWarning(); });
    el.addEventListener("change", () => { formDirty = true; if (id === "bind_host") updateBindWarning(); });
  });

  $("reload-settings").addEventListener("click", () => {
    formDirty = false;
    loadConfig(true);
  });

  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const config = buildConfig();
      setNotice("Saving settings and restarting the Prometheus plugin…");
      await postConfig(config, { restart: true });
      formDirty = false;
      setNotice("Saved. The Prometheus plugin is restarting with the new configuration.", "ok");
      setTimeout(() => loadConfig(true).catch(() => {}), 1800);
      setTimeout(() => loadConfig(true).catch(() => {}), 4000);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error), "error");
    }
  });

  setInterval(updateCountdown, 1000);
  setInterval(() => loadConfig(false), AUTO_REFRESH_MS);
  loadConfig(true);
})();

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .collectors import PluginCollector, RepeaterCollector
from .config import PluginConfig
from .diagnostics import FailureLog
from .model import CollectorResult
from .state import MetricsState

LOGGER = logging.getLogger("openhop.prometheus")


class CollectionManager:
    def __init__(self, state: MetricsState):
        self.state = state
        self._failures = FailureLog()

    def build_collectors(self, config: PluginConfig):
        collectors = [PluginCollector()]
        if config.repeater_enabled:
            collectors.append(RepeaterCollector(config))
        return collectors

    def collect_once(self, config: PluginConfig) -> list[CollectorResult]:
        collectors = self.build_collectors(config)
        self.state.set_enabled_collectors(collector.name for collector in collectors)
        started = self.state.mark_collection_started()
        results: list[CollectorResult] = []

        with ThreadPoolExecutor(
            max_workers=max(1, len(collectors)),
            thread_name_prefix="openhop-prom-collector",
        ) as executor:
            futures = {executor.submit(collector.collect_safe): collector for collector in collectors}
            for future in as_completed(futures):
                collector = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive: collect_safe should catch
                    result = CollectorResult(
                        name=collector.name,
                        ok=False,
                        metrics=[],
                        duration_seconds=0.0,
                        error=str(exc),
                        failure_metrics=collector.failure_metrics(),
                    )
                self.state.update_collector(result)
                results.append(result)
                if result.ok:
                    self._failures.recovered(f"collector={result.name}")
                    LOGGER.debug(
                        "collector=%s ok metrics=%d duration_ms=%d",
                        result.name,
                        len(result.metrics),
                        round(result.duration_seconds * 1000),
                    )
                else:
                    self._failures.report(
                        f"collector={result.name}",
                        result.error,
                        secret=config.repeater_api_token,
                    )

        self.state.mark_collection_completed(started)
        return sorted(results, key=lambda result: result.name)

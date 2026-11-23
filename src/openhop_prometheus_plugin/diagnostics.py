"""Bounded default diagnostics; detailed collection success is opt-in DEBUG."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

LOGGER = logging.getLogger("openhop.prometheus")


def safe_error(error: object, secret: str = "") -> str:
    text = str(error)
    if secret:
        text = text.replace(secret, "[redacted]")
        if secret.lower().startswith("bearer ") and secret[7:].strip():
            text = text.replace(secret[7:].strip(), "[redacted]")
    return text.replace("\r", " ").replace("\n", " ")[:512]


class FailureLog:
    """First failure + five-minute reminders + recovery, not a per-cycle log.

    Rate limiting is by fixed subsystem, including changing exception text, so
    timestamps and alternating errors cannot defeat it. No unbounded error set.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def report(
        self, source: str, error: object, *, secret: str = "", level: int = logging.WARNING
    ) -> None:
        source = source[:128]
        now = self._clock()
        with self._lock:
            previous = self._failures.get(source)
            if previous is not None and now - previous[0] < 300:
                self._failures[source] = (previous[0], previous[1] + 1)
                return
            suppressed = previous[1] if previous else 0
            self._failures[source] = (now, 0)
            self._failures.move_to_end(source)
            while len(self._failures) > 32:
                self._failures.popitem(last=False)
            LOGGER.log(
                level,
                "%s failed error=%s suppressed=%d",
                source,
                safe_error(error, secret),
                suppressed,
            )

    def recovered(self, source: str) -> None:
        with self._lock:
            previous = self._failures.pop(source, None)
            if previous is not None:
                LOGGER.info("%s recovered suppressed=%d", source, previous[1])

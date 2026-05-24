"""Throttled, thread-safe progress reporter for the Textual TUI."""

from collections.abc import Callable
import time
from typing import Any


class TUIProgressReporter:
    """Routes ProgressReporter events into a Textual app.

    - ``checkpoint`` / ``item_event``: always logged immediately via ``app._log``.
    - ``tick``: always updates the title bar via ``app._task_start``; logs to
      the log panel at most once per ``min_tick_interval`` seconds, plus
      always on the final tick (``current == total``).

    All UI calls are dispatched via ``app.call_from_thread`` so the reporter
    is safe to call from background worker threads.
    """

    def __init__(
        self,
        app: Any,
        task_key: str,
        label: str,
        min_tick_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._app = app
        self._task_key = task_key
        self._label = label
        self._min_tick_interval = min_tick_interval
        self._clock = clock
        # Initialize so the first tick always falls outside the window.
        self._last_tick_at = -min_tick_interval

    def checkpoint(self, msg: str) -> None:
        self._app.call_from_thread(self._app._log, msg)

    def item_event(self, msg: str) -> None:
        self._app.call_from_thread(self._app._log, msg)

    def tick(self, current: int, total: int) -> None:
        self._app.call_from_thread(
            self._app._task_start,
            self._task_key,
            f'{self._label} {current}/{total}',
        )
        now = self._clock()
        is_final = current == total
        if is_final or (now - self._last_tick_at) >= self._min_tick_interval:
            self._app.call_from_thread(
                self._app._log,
                f'Processed {current}/{total}',
            )
            self._last_tick_at = now

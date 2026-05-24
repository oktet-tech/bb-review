"""Progress reporting protocol for long-running sync operations.

A ProgressReporter is a small interface that sync code can call to surface
progress events. The presentation (logging, throttling, CLI overwrite, etc.)
is the reporter's responsibility — sync code just emits events.
"""

from typing import Protocol


class ProgressReporter(Protocol):
    """Receives progress events from long-running sync operations."""

    def checkpoint(self, msg: str) -> None:
        """A phase transition. Reporters surface this immediately."""
        ...

    def tick(self, current: int, total: int) -> None:
        """Per-item progress. Reporters choose their own cadence."""
        ...

    def item_event(self, msg: str) -> None:
        """A notable event for one item. Reporters surface this immediately."""
        ...


class NullProgressReporter:
    """No-op reporter used as the default when callers don't pass one."""

    def checkpoint(self, msg: str) -> None:
        pass

    def tick(self, current: int, total: int) -> None:
        pass

    def item_event(self, msg: str) -> None:
        pass

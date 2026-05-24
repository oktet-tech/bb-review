"""ProgressReporter implementation for Click-based CLI commands.

Preserves the single-line carriage-return counter UX that ``cli/queue.py``
used before the reporter refactor, while adding checkpoint and item-event
lines from the same source.
"""

import click


class ClickProgressReporter:
    """Writes progress events to stdout via Click.

    ``tick`` overwrites a single line using ``\\r`` (unthrottled — terminal
    overwrites are cheap and a stable counter feels better than a 2s jump).
    ``checkpoint`` and ``item_event`` always write on their own line, and
    insert a trailing newline first if the previous output was a tick.
    The final tick (``current == total``) also emits the trailing newline so
    subsequent caller output starts on a fresh row.
    """

    def __init__(self):
        self._last_was_tick = False

    def checkpoint(self, msg: str) -> None:
        self._flush_tick_line()
        click.echo(msg)

    def item_event(self, msg: str) -> None:
        self._flush_tick_line()
        click.echo(msg)

    def tick(self, current: int, total: int) -> None:
        click.echo(f'\rFetching: {current}/{total}...', nl=False)
        self._last_was_tick = True
        if current == total:
            click.echo()
            self._last_was_tick = False

    def _flush_tick_line(self) -> None:
        if self._last_was_tick:
            click.echo()
            self._last_was_tick = False

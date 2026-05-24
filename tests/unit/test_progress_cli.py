"""Tests for ClickProgressReporter (CLI carriage-return counter)."""

import click
from click.testing import CliRunner

from bb_review.cli._progress import ClickProgressReporter


def _run(action) -> str:
    """Run ``action`` inside a Click command and capture stdout."""

    @click.command()
    def cmd():
        action()

    runner = CliRunner()
    result = runner.invoke(cmd, [])
    assert result.exit_code == 0, result.output
    return result.output


def test_checkpoint_writes_line():
    def go():
        ClickProgressReporter().checkpoint('Fetching from RB...')

    out = _run(go)
    assert out == 'Fetching from RB...\n'


def test_item_event_writes_line():
    def go():
        ClickProgressReporter().item_event('r/123: checking diff 1->2...')

    out = _run(go)
    assert out == 'r/123: checking diff 1->2...\n'


def test_tick_overwrites_with_cr():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.tick(2, 5)
        r.tick(3, 5)

    out = _run(go)
    # Each tick overwrites; no trailing newline until something else fires.
    assert out == '\rFetching: 1/5...\rFetching: 2/5...\rFetching: 3/5...'


def test_checkpoint_after_tick_inserts_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.checkpoint('Reconciling...')

    out = _run(go)
    assert out == '\rFetching: 1/5...\nReconciling...\n'


def test_item_event_after_tick_inserts_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.item_event('r/123: checking diff 1->2...')

    out = _run(go)
    assert out == '\rFetching: 1/5...\nr/123: checking diff 1->2...\n'


def test_final_tick_emits_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 2)
        r.tick(2, 2)

    out = _run(go)
    # Final tick (current == total) appends a newline so subsequent
    # non-tick output starts on a fresh row.
    assert out == '\rFetching: 1/2...\rFetching: 2/2...\n'


def test_consecutive_non_tick_calls_do_not_insert_extra_newline():
    def go():
        r = ClickProgressReporter()
        r.checkpoint('A')
        r.checkpoint('B')

    out = _run(go)
    assert out == 'A\nB\n'

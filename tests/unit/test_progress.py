"""Tests for TUIProgressReporter (throttled, thread-safe progress)."""

from types import SimpleNamespace

from bb_review.ui.progress_reporter import TUIProgressReporter


class _FakeApp:
    """Stand-in for UnifiedApp: records calls instead of touching the UI."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.task_updates: list[tuple[str, str]] = []

    def call_from_thread(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def _log(self, text: str) -> None:
        self.log_lines.append(text)

    def _task_start(self, key: str, label: str) -> None:
        self.task_updates.append((key, label))


def _make_reporter(app, clock):
    return TUIProgressReporter(
        app=app,
        task_key='sync',
        label='sync',
        min_tick_interval=2.0,
        clock=clock,
    )


def test_checkpoint_logs_immediately():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.checkpoint('Fetching from RB...')
    assert app.log_lines == ['Fetching from RB...']


def test_item_event_logs_immediately():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.item_event('r/123: checking diff 1->2...')
    assert app.log_lines == ['r/123: checking diff 1->2...']


def test_tick_always_updates_title_bar():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)
    now[0] = 0.5
    reporter.tick(2, 100)
    now[0] = 0.6
    reporter.tick(3, 100)
    assert app.task_updates == [
        ('sync', 'sync 1/100'),
        ('sync', 'sync 2/100'),
        ('sync', 'sync 3/100'),
    ]


def test_first_tick_always_logs():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.tick(1, 100)
    assert app.log_lines == ['Processed 1/100']


def test_tick_throttled_within_window():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 0.5
    reporter.tick(2, 100)            # t=0.5 -> suppressed
    now[0] = 1.9
    reporter.tick(3, 100)            # t=1.9 -> suppressed
    assert app.log_lines == ['Processed 1/100']


def test_tick_logs_again_after_window():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 2.1
    reporter.tick(50, 100)           # t=2.1 -> logs again
    assert app.log_lines == ['Processed 1/100', 'Processed 50/100']


def test_final_tick_always_logs_even_when_throttled():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 0.5
    reporter.tick(100, 100)          # t=0.5 (within window) -> still logs (final)
    assert app.log_lines == ['Processed 1/100', 'Processed 100/100']


def test_label_used_in_title_bar():
    app = _FakeApp()
    reporter = TUIProgressReporter(
        app=app,
        task_key='my_reviews_sync',
        label='my-sync',
        min_tick_interval=2.0,
        clock=lambda: 0.0,
    )
    reporter.tick(5, 20)
    assert app.task_updates == [('my_reviews_sync', 'my-sync 5/20')]

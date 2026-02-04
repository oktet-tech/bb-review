"""Unified interactive TUI merging queue triage and review management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import ContentSwitcher, Footer, Header, Tab, Tabs

from bb_review.db.models import AnalysisListItem
from bb_review.db.queue_models import QueueItem, QueueStatus
from bb_review.db.review_db import ReviewDatabase

from .review_handler import ReviewHandler
from .widgets.log_panel import LogPanel
from .widgets.queue_pane import QueuePane
from .widgets.reviews_pane import ReviewsPane


if TYPE_CHECKING:
    from bb_review.config import Config
    from bb_review.db.queue_db import QueueDatabase

logger = logging.getLogger(__name__)

TAB_QUEUE = "tab-queue"
TAB_REVIEWS = "tab-reviews"


class UnifiedApp(App):
    """Unified interactive TUI with tabbed queue + reviews panes."""

    TITLE = "BB Review Interactive"

    BINDINGS = [
        Binding("question_mark", "command_palette", "Commands"),
        Binding("ctrl+t", "switch_tab", "Switch Tab", show=False),
        Binding("1", "show_queue", "Queue", show=False),
        Binding("2", "show_reviews", "Reviews", show=False),
        Binding("l", "toggle_log", "Log", priority=True),
        Binding("c", "clear_log", "Clear Log", priority=True, show=False),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "quit_app", "Quit", show=False),
    ]

    CSS = """
    Screen {
        background: $surface;
    }

    #main-content {
        height: 1fr;
    }

    Tabs {
        dock: top;
    }
    """

    def __init__(
        self,
        *,
        # Queue data
        queue_items: list[QueueItem] | None = None,
        queue_db: QueueDatabase | None = None,
        queue_filter_status: QueueStatus | None = None,
        queue_exclude_statuses: list[QueueStatus] | None = None,
        queue_filter_repo: str | None = None,
        queue_filter_limit: int = 50,
        # Reviews data
        analyses: list[AnalysisListItem] | None = None,
        review_db: ReviewDatabase | None = None,
        config: Config | None = None,
        output_path: str | None = None,
        review_filter_rr_id: int | None = None,
        review_filter_repo: str | None = None,
        review_filter_status: str | None = None,
        review_filter_chain_id: str | None = None,
        review_filter_limit: int = 50,
        # UI
        initial_tab: str = "queue",
    ) -> None:
        super().__init__()
        self._queue_items = queue_items or []
        self._queue_db = queue_db
        self._q_filter_status = queue_filter_status
        self._q_exclude_statuses = queue_exclude_statuses
        self._q_filter_repo = queue_filter_repo
        self._q_filter_limit = queue_filter_limit

        self._analyses = analyses or []
        self._review_db = review_db
        self._config = config
        self._output_path = output_path
        self._r_filter_rr_id = review_filter_rr_id
        self._r_filter_repo = review_filter_repo
        self._r_filter_status = review_filter_status
        self._r_filter_chain_id = review_filter_chain_id
        self._r_filter_limit = review_filter_limit

        self._initial_tab = TAB_QUEUE if initial_tab == "queue" else TAB_REVIEWS
        self._review_handler: ReviewHandler | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(
            Tab("[1] Queue", id=TAB_QUEUE),
            Tab("[2] Reviews", id=TAB_REVIEWS),
        )
        with Vertical(id="main-content"):
            with ContentSwitcher(initial=self._initial_tab):
                if self._queue_db is not None:
                    yield QueuePane(
                        self._queue_items,
                        self._queue_db,
                        id=TAB_QUEUE,
                    )
                else:
                    # Placeholder when queue DB isn't available
                    yield Vertical(id=TAB_QUEUE)
                if self._review_db is not None:
                    yield ReviewsPane(self._analyses, id=TAB_REVIEWS)
                else:
                    yield Vertical(id=TAB_REVIEWS)
            yield LogPanel()
        yield Footer()

    def on_mount(self) -> None:
        # Activate the correct tab
        self.query_one(Tabs).active = self._initial_tab

        # Create review handler
        if self._review_db is not None:
            self._review_handler = ReviewHandler(
                app=self,
                db=self._review_db,
                config=self._config,
                output_path=self._output_path,
            )

        # Focus the active pane's table
        self._focus_active_pane()

    # -- Tab switching --

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        switcher = self.query_one(ContentSwitcher)
        switcher.current = event.tab.id
        self._focus_active_pane()

    def action_switch_tab(self) -> None:
        tabs = self.query_one(Tabs)
        if tabs.active == TAB_QUEUE:
            tabs.active = TAB_REVIEWS
        else:
            tabs.active = TAB_QUEUE

    def action_show_queue(self) -> None:
        self.query_one(Tabs).active = TAB_QUEUE

    def action_show_reviews(self) -> None:
        self.query_one(Tabs).active = TAB_REVIEWS

    def _focus_active_pane(self) -> None:
        switcher = self.query_one(ContentSwitcher)
        if switcher.current == TAB_QUEUE:
            try:
                self.query_one(QueuePane).focus_table()
            except Exception:
                pass
        else:
            try:
                self.query_one(ReviewsPane).focus_table()
            except Exception:
                pass

    # -- Log panel --

    def action_toggle_log(self) -> None:
        self.query_one(LogPanel).toggle()

    def action_clear_log(self) -> None:
        self.query_one(LogPanel).clear()

    def action_quit_app(self) -> None:
        self.exit()

    # -- Data refresh helpers (called by panes and handler) --

    def refresh_queue_items(self) -> list[QueueItem]:
        """Re-query queue items with stored filters."""
        if self._queue_db is None:
            return []
        return self._queue_db.list_items(
            status=self._q_filter_status,
            exclude_statuses=self._q_exclude_statuses,
            repository=self._q_filter_repo,
            limit=self._q_filter_limit,
        )

    def refresh_review_items(self) -> list[AnalysisListItem]:
        """Re-query analyses with stored filters."""
        if self._review_db is None:
            return []
        return self._review_db.list_analyses(
            review_request_id=self._r_filter_rr_id,
            repository=self._r_filter_repo,
            status=self._r_filter_status,
            chain_id=self._r_filter_chain_id,
            limit=self._r_filter_limit,
        )

    def refresh_reviews_pane(self) -> None:
        """Refresh the reviews pane data (called by ReviewHandler)."""
        try:
            self._analyses = self.refresh_review_items()
            self.query_one(ReviewsPane).refresh_data(self._analyses)
        except Exception:
            pass

    # -- Message handlers from panes --

    def on_queue_pane_sync_requested(self, event: QueuePane.SyncRequested) -> None:
        self._run_sync()

    def on_queue_pane_process_requested(self, event: QueuePane.ProcessRequested) -> None:
        self._run_process()

    def on_reviews_pane_action_requested(self, event: ReviewsPane.ActionRequested) -> None:
        if self._review_handler is None:
            self.notify("Review database not configured", severity="error")
            return
        self._review_handler.handle_action(event.action, self._analyses)

    # -- Background workers --

    def _log(self, text: str) -> None:
        """Write to the log panel (thread-safe via call_from_thread)."""
        log_panel = self.query_one(LogPanel)
        log_panel.write(text)

    @work(thread=True, exclusive=True, group="sync")
    def _run_sync(self) -> None:
        """Background worker: sync queue from Review Board."""
        if self._queue_db is None or self._config is None:
            self.call_from_thread(self.notify, "Config or queue DB not available", severity="error")
            return

        self.call_from_thread(self.query_one(LogPanel).show)
        self.call_from_thread(self._log, "Starting sync...")

        try:
            from bb_review.rr.rb_client import ReviewBoardClient

            rb_client = ReviewBoardClient(
                url=self._config.reviewboard.url,
                bot_username=self._config.reviewboard.bot_username,
                api_token=self._config.reviewboard.api_token,
                username=self._config.reviewboard.username,
                password=self._config.reviewboard.get_password(),
                use_kerberos=self._config.reviewboard.use_kerberos,
            )
            rb_client.connect()
            self.call_from_thread(self._log, "Connected to Review Board")

            from bb_review.queue_sync import sync_queue

            counts = sync_queue(
                rb_client=rb_client,
                queue_db=self._queue_db,
            )

            summary = (
                f"Sync complete: {counts['total']} fetched, "
                f"{counts['inserted']} new, "
                f"{counts['updated']} reset, "
                f"{counts['skipped']} unchanged"
            )
            self.call_from_thread(self._log, summary)
            self.call_from_thread(self.notify, summary, severity="information")

        except Exception as e:
            logger.exception("Sync failed")
            self.call_from_thread(self._log, f"Sync FAILED: {e}")
            self.call_from_thread(self.notify, f"Sync failed: {e}", severity="error")

        # Refresh queue pane
        self.call_from_thread(self._refresh_queue_pane)

    def _refresh_queue_pane(self) -> None:
        """Refresh the queue pane with fresh data."""
        try:
            items = self.refresh_queue_items()
            self._queue_items = items
            self.query_one(QueuePane).refresh_data(items)
        except Exception:
            pass

    @work(thread=True, exclusive=True, group="process")
    def _run_process(self) -> None:
        """Background worker: process next queue items."""
        if self._queue_db is None or self._config is None:
            self.call_from_thread(self.notify, "Config or queue DB not available", severity="error")
            return

        self.call_from_thread(self.query_one(LogPanel).show)
        self.call_from_thread(self._log, "Starting process...")

        config = self._config
        queue_db = self._queue_db

        try:
            # Reset stale items
            reset_count = queue_db.reset_stale_in_progress()
            if reset_count > 0:
                self.call_from_thread(self._log, f"Reset {reset_count} stale in_progress item(s)")

            items = queue_db.pick_next(5)
            if not items:
                self.call_from_thread(self._log, "No items with status=next to process.")
                self.call_from_thread(self.notify, "No items with status=next", severity="information")
                return

            self.call_from_thread(self._log, f"Processing {len(items)} item(s)...")

            from bb_review.db import ReviewDatabase
            from bb_review.git import RepoManager
            from bb_review.rr.rb_client import ReviewBoardClient

            rb_client = ReviewBoardClient(
                url=config.reviewboard.url,
                bot_username=config.reviewboard.bot_username,
                api_token=config.reviewboard.api_token,
                username=config.reviewboard.username,
                password=config.reviewboard.get_password(),
                use_kerberos=config.reviewboard.use_kerberos,
            )
            rb_client.connect()

            repo_manager = RepoManager(config.get_all_repos())
            review_db = ReviewDatabase(config.review_db.resolved_path)

            # Default to opencode method
            method = "opencode"
            analysis_method = method

            succeeded = 0
            failed = 0

            for item in items:
                rr_id = item.review_request_id
                self.call_from_thread(self._log, f"Processing r/{rr_id} (diff {item.diff_revision})...")

                try:
                    if review_db.has_real_analysis(rr_id, item.diff_revision, analysis_method):
                        existing = review_db.get_analysis_by_rr(rr_id, item.diff_revision)
                        analysis_id = existing.id if existing else None
                        queue_db.mark_done(rr_id, analysis_id)
                        self.call_from_thread(
                            self._log,
                            f"  Skipped r/{rr_id}: already analyzed (id={analysis_id})",
                        )
                        continue

                    queue_db.mark_in_progress(rr_id)

                    # Use the CLI process functions
                    from bb_review.cli.queue import _process_item_agent

                    _process_item_agent(
                        item,
                        method,
                        config,
                        rb_client,
                        repo_manager,
                        review_db,
                        queue_db,
                        model_name=None,
                        fake_review=False,
                        submit=False,
                        fallback=True,
                    )

                    succeeded += 1
                    self.call_from_thread(self._log, f"  r/{rr_id}: done")

                except Exception as e:
                    logger.exception(f"Failed to process r/{rr_id}")
                    queue_db.mark_failed(rr_id, str(e))
                    self.call_from_thread(self._log, f"  r/{rr_id} FAILED: {e}")
                    failed += 1

            summary = f"Process complete: {succeeded} succeeded, {failed} failed"
            self.call_from_thread(self._log, summary)
            self.call_from_thread(self.notify, summary, severity="information")

        except Exception as e:
            logger.exception("Process failed")
            self.call_from_thread(self._log, f"Process FAILED: {e}")
            self.call_from_thread(self.notify, f"Process failed: {e}", severity="error")

        # Refresh both panes
        self.call_from_thread(self._refresh_queue_pane)
        self.call_from_thread(self.refresh_reviews_pane)

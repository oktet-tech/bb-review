"""Fetch reviewer comments from Review Board into the mining cache."""

from collections.abc import Callable
import logging

from ..db.mining_db import MiningDatabase
from ..reviewers.diff_utils import extract_diff_hunk
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
from ..triage.models import RBComment


logger = logging.getLogger(__name__)

# RR statuses worth mining: both carry full human review history.
MINED_STATUSES = ["submitted", "discarded"]


def _extract_submitter(rr: dict) -> str:
    """Pull the submitter username from an RB review-request dict."""
    return rr.get("links", {}).get("submitter", {}).get("title", "")


def fetch_repo_rules_data(
    rb_client: ReviewBoardClient,
    mining_db: MiningDatabase,
    repo_name: str,
    rb_repo_name: str,
    bot_username: str,
    count: int,
    days: int = 0,
    refresh: bool = False,
    with_diff_hunks: bool = False,
    on_progress: Callable[[int, int, int], None] | None = None,
    comment_fetcher: RBCommentFetcher | None = None,
) -> dict[str, int]:
    """Fetch reviewer comments for the most recent RRs of a repository.

    Args:
        rb_client: Connected Review Board client.
        mining_db: Cache database to upsert into.
        repo_name: Config repository name; stored as `repository` in the cache.
        rb_repo_name: Review Board repository name used for the RB query.
        bot_username: Bot account whose comments are excluded.
        count: Max number of recent RRs to mine.
        days: If > 0, only consider RRs updated within this many days.
        refresh: If True, re-fetch RRs even if already cached.
        with_diff_hunks: If True, also fetch and cache the diff hunk for each
            diff comment. For already-cached RRs this acts as a backfill.
        on_progress: Called with (current, total, comment_count) per RR.
        comment_fetcher: Override for the RBCommentFetcher (used in tests).

    Returns:
        Counts dict with keys: total, fetched, skipped, comments,
        hunks_backfilled.
    """
    review_requests = rb_client.list_repo_review_requests(
        repository=rb_repo_name,
        statuses=MINED_STATUSES,
        limit=count,
        days=days,
    )
    if comment_fetcher is None:
        comment_fetcher = RBCommentFetcher(rb_client, bot_username)

    total = len(review_requests)
    fetched = 0
    skipped = 0
    comment_total = 0
    hunks_backfilled = 0

    diff_cache: dict[tuple[int, int], str | None] = {}

    def _get_raw_diff(rr_id: int, rev: int) -> str | None:
        """Memoized per-(rr_id, rev) raw-diff fetch.

        Returns None and caches the negative result if the RB call fails,
        so a single bad diff fetch doesn't abort the batch and doesn't get
        retried for every comment on that filediff.
        """
        key = (rr_id, rev)
        if key in diff_cache:
            return diff_cache[key]
        try:
            raw = rb_client.get_diff(rr_id, rev).raw_diff
        except Exception as e:
            logger.warning(f"Failed to fetch diff for RR #{rr_id} rev {rev}: {e}")
            diff_cache[key] = None
            return None
        diff_cache[key] = raw
        return raw

    def _augment_with_hunks(rr_id: int, comments: list[RBComment]) -> None:
        """Set comment.diff_hunk in place for diff comments with a known rev."""
        for c in comments:
            if c.is_body_comment or not c.file_path or not c.line_number:
                continue
            if c.diff_revision is None:
                continue
            raw = _get_raw_diff(rr_id, c.diff_revision)
            if raw is None:
                continue
            c.diff_hunk = extract_diff_hunk(raw, c.file_path, c.line_number)

    for i, rr in enumerate(review_requests):
        rr_id = rr["id"]

        if not refresh and mining_db.has_review_request(rr_id):
            if with_diff_hunks:
                added = _backfill_hunks(rr_id, mining_db, _get_raw_diff)
                if added > 0:
                    hunks_backfilled += 1
                else:
                    skipped += 1
            else:
                skipped += 1
            if on_progress:
                on_progress(i + 1, total, 0)
            continue

        try:
            comments = comment_fetcher.fetch_all_comments(rr_id)
        except Exception as e:
            logger.warning(f"Failed to fetch comments for RR #{rr_id}: {e}")
            if on_progress:
                on_progress(i + 1, total, 0)
            continue

        if with_diff_hunks:
            _augment_with_hunks(rr_id, comments)

        mining_db.record_review_request(
            rr_id=rr_id,
            repository=repo_name,
            rr_status=rr.get("status", ""),
            rr_summary=rr.get("summary", ""),
            submitter=_extract_submitter(rr),
            branch=rr.get("branch", "") or "",
            rb_last_updated=rr.get("last_updated", "") or "",
            comments=comments,
        )
        fetched += 1
        comment_total += len(comments)
        if on_progress:
            on_progress(i + 1, total, len(comments))

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "comments": comment_total,
        "hunks_backfilled": hunks_backfilled,
    }


def _backfill_hunks(
    rr_id: int,
    mining_db: MiningDatabase,
    get_raw_diff: Callable[[int, int], str | None],
) -> int:
    """Fill in diff_hunk for cached comments of `rr_id` that have it NULL.

    Returns the number of comments whose hunk was actually populated. A
    return of zero means either no missing hunks or no hunk could be
    extracted (e.g. line not in any hunk, or the diff fetch failed).
    """
    missing = mining_db.get_comments_missing_hunks(rr_id)
    filled = 0
    for c in missing:
        if c.diff_revision is None or c.file_path is None or c.line_number is None:
            continue
        raw = get_raw_diff(rr_id, c.diff_revision)
        if raw is None:
            continue
        hunk = extract_diff_hunk(raw, c.file_path, c.line_number)
        if hunk is None:
            continue
        mining_db.update_comment_diff_hunk(rr_id, c.comment_id, hunk)
        filled += 1
    return filled

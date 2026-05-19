"""Fetch reviewer comments from Review Board into the mining cache."""

from collections.abc import Callable
import logging

from ..db.mining_db import MiningDatabase
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher


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
        on_progress: Called with (current, total, comment_count) per RR.
        comment_fetcher: Override for the RBCommentFetcher (used in tests).

    Returns:
        Counts dict with keys: total, fetched, skipped, comments.
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

    for i, rr in enumerate(review_requests):
        rr_id = rr["id"]

        if not refresh and mining_db.has_review_request(rr_id):
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
    }

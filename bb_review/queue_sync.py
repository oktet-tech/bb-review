"""Sync logic: fetch review requests from RB and reconcile with queue."""

import logging

from .db.queue_db import QueueDatabase
from .models import PendingReview
from .rr.rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)


def sync_queue(
    rb_client: ReviewBoardClient,
    queue_db: QueueDatabase,
    days: int = 10,
    limit: int = 200,
    repository: str | None = None,
    submitter: str | None = None,
    bot_only: bool = False,
) -> dict[str, int]:
    """Fetch recent RRs from Review Board and reconcile with the queue.

    Sync rules per fetched RR:
    1. Not in queue -> INSERT as todo
    2. In queue, same diff_revision, has non-fake analysis -> skip
    3. In queue, same diff_revision, no non-fake analysis -> keep status (metadata update)
    4. In queue, new diff_revision -> reset to todo, clear analysis_id

    Args:
        rb_client: Connected RB client.
        queue_db: Queue database instance.
        days: How far back to look.
        limit: Max RRs to fetch.
        repository: Filter by repository.
        submitter: Filter by submitter username.
        bot_only: If True, only fetch RRs assigned to the bot user.

    Returns:
        Dict with counts: inserted, updated (reset), skipped, total.
    """
    from_user = submitter
    if bot_only:
        # Fetch only reviews assigned to the bot
        pending = rb_client.get_pending_reviews(limit=limit)
    else:
        pending = rb_client.get_recent_reviews(
            days=days,
            limit=limit,
            repository=repository,
            from_user=from_user,
        )

    counts = {"inserted": 0, "updated": 0, "skipped": 0, "analyzed": 0, "total": len(pending)}

    for pr in pending:
        _sync_one(queue_db, pr, counts)

    return counts


def _sync_one(
    queue_db: QueueDatabase,
    pr: PendingReview,
    counts: dict[str, int],
) -> None:
    """Reconcile a single PendingReview with the queue."""
    existing = queue_db.get(pr.review_request_id)

    # Check if there's already a non-fake analysis for this exact diff
    if existing and existing.diff_revision == pr.diff_revision:
        has_analysis = queue_db.has_non_fake_analysis(
            pr.review_request_id,
            pr.diff_revision,
        )
        if has_analysis:
            counts["analyzed"] += 1
            logger.debug(f"r/{pr.review_request_id}: already analyzed (diff {pr.diff_revision}), skipping")
            # Still update metadata
            queue_db.upsert(
                review_request_id=pr.review_request_id,
                diff_revision=pr.diff_revision,
                repository=pr.repository,
                submitter=pr.submitter,
                summary=pr.summary,
                branch=pr.branch,
                base_commit=pr.base_commit,
                rb_created_at=pr.created_at,
            )
            return

    action, reset = queue_db.upsert(
        review_request_id=pr.review_request_id,
        diff_revision=pr.diff_revision,
        repository=pr.repository,
        submitter=pr.submitter,
        summary=pr.summary,
        branch=pr.branch,
        base_commit=pr.base_commit,
        rb_created_at=pr.created_at,
    )

    if action == "inserted":
        counts["inserted"] += 1
        logger.debug(f"r/{pr.review_request_id}: inserted as todo")
    elif action == "updated" and reset:
        counts["updated"] += 1
        logger.info(f"r/{pr.review_request_id}: new diff {pr.diff_revision}, reset to todo")
    else:
        counts["skipped"] += 1

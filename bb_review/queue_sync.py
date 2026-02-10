"""Sync logic: fetch review requests from RB and reconcile with queue."""

import logging

from .db.queue_db import QueueDatabase
from .db.queue_models import QueueStatus
from .models import PendingReview
from .rr.rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)

# Items in these statuses are pruned when no longer on RB
_PRUNABLE_STATUSES = {QueueStatus.TODO, QueueStatus.NEXT, QueueStatus.IGNORE}


def sync_queue(
    rb_client: ReviewBoardClient,
    queue_db: QueueDatabase,
    days: int = 10,
    limit: int = 200,
    repository: str | None = None,
    submitter: str | None = None,
    bot_only: bool = False,
    prune: bool = True,
) -> dict[str, int]:
    """Fetch recent RRs from Review Board and reconcile with the queue.

    Sync rules per fetched RR:
    1. Not in queue -> INSERT as todo
    2. In queue, same diff_revision, has non-fake analysis -> skip
    3. In queue, same diff_revision, no non-fake analysis -> keep status (metadata update)
    4. In queue, new diff_revision -> reset to todo, clear analysis_id

    When prune=True, queue items with status in (todo, next, ignore) that are
    no longer present in the fetched set are deleted. This handles RRs that
    were submitted, discarded, or otherwise removed from RB.

    Args:
        rb_client: Connected RB client.
        queue_db: Queue database instance.
        days: How far back to look.
        limit: Max RRs to fetch.
        repository: Filter by repository.
        submitter: Filter by submitter username.
        bot_only: If True, only fetch RRs assigned to the bot user.
        prune: If True, delete queue items no longer on RB.

    Returns:
        Dict with counts: inserted, updated (reset), skipped, total, pruned.
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

    counts = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "analyzed": 0,
        "total": len(pending),
        "pruned": 0,
    }

    for pr in pending:
        _sync_one(queue_db, pr, counts)

    if prune:
        fetched_rr_ids = {pr.review_request_id for pr in pending}
        counts["pruned"] = _prune_gone(queue_db, fetched_rr_ids)

    return counts


def _prune_gone(queue_db: QueueDatabase, fetched_rr_ids: set[int]) -> int:
    """Delete queue items that are no longer present on RB.

    Only prunes items with prunable statuses (todo, next, ignore).
    Items that are in_progress, done, or failed are kept.
    """
    all_items = queue_db.list_items(limit=10000)
    pruned = 0

    for item in all_items:
        if item.review_request_id not in fetched_rr_ids and item.status in _PRUNABLE_STATUSES:
            queue_db.delete_item(item.review_request_id)
            logger.info(f"r/{item.review_request_id}: pruned (no longer on RB)")
            pruned += 1

    return pruned


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
                issue_open_count=pr.issue_open_count,
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
        issue_open_count=pr.issue_open_count,
    )

    if action == "inserted":
        counts["inserted"] += 1
        logger.debug(f"r/{pr.review_request_id}: inserted as todo")
    elif action == "updated" and reset:
        counts["updated"] += 1
        logger.info(f"r/{pr.review_request_id}: new diff {pr.diff_revision}, reset to todo")
    else:
        counts["skipped"] += 1

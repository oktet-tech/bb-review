"""Auto-reply posting to Review Board based on fix plan items."""

from collections import defaultdict
import logging

from ..rr.rb_client import ReviewBoardClient
from .models import FixPlanItem, TriageAction


logger = logging.getLogger(__name__)


class RBReplier:
    """Posts replies to Review Board for triaged comments.

    Groups items by review_id, creates a reply draft per review,
    attaches diff-comment replies and body replies, then publishes.
    """

    def __init__(self, rb_client: ReviewBoardClient):
        self.rb_client = rb_client

    def post_replies(
        self,
        rr_id: int,
        items: list[FixPlanItem],
        review_comment_map: dict[int, int],
        dry_run: bool = False,
    ) -> list[int]:
        """Post replies for items that have reply_text.

        Args:
            rr_id: Review request ID.
            items: Fix plan items to post replies for.
            review_comment_map: Mapping of comment_id -> review_id.
            dry_run: If True, log but don't actually post.

        Returns:
            List of published reply IDs.
        """
        # Filter to items with reply text and reply/disagree/fix actions
        replyable = [
            item
            for item in items
            if item.reply_text
            and item.action
            in (
                TriageAction.REPLY,
                TriageAction.DISAGREE,
                TriageAction.FIX,
            )
        ]
        if not replyable:
            logger.info("No items with reply text to post")
            return []

        # Group by review_id
        by_review: dict[int, list[FixPlanItem]] = defaultdict(list)
        for item in replyable:
            review_id = review_comment_map.get(item.comment_id)
            if review_id is None:
                logger.warning(f"No review_id found for comment {item.comment_id}, skipping")
                continue
            by_review[review_id].append(item)

        published: list[int] = []

        for review_id, review_items in by_review.items():
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would post reply to review {review_id} "
                    f"with {len(review_items)} comment replies"
                )
                for item in review_items:
                    loc = f"{item.file_path}:{item.line_number}" if item.file_path else "body"
                    logger.info(f"  [{item.action.value}] {loc}: {item.reply_text[:80]}")
                continue

            try:
                reply_id = self._post_review_reply(rr_id, review_id, review_items)
                published.append(reply_id)
            except Exception as e:
                logger.error(f"Failed to post reply to review {review_id}: {e}")

        return published

    def _post_review_reply(
        self,
        rr_id: int,
        review_id: int,
        items: list[FixPlanItem],
    ) -> int:
        """Create, populate, and publish a single reply to a review."""
        # Separate body replies from diff-comment replies
        body_items = [i for i in items if i.file_path is None]
        diff_items = [i for i in items if i.file_path is not None]

        # Build body_top from body replies
        body_top = ""
        if body_items:
            body_top = "\n\n".join(i.reply_text for i in body_items)

        # Create reply draft
        reply = self.rb_client.post_reply(rr_id, review_id, body_top=body_top)
        reply_id = reply["id"]
        logger.debug(f"Created reply draft {reply_id} for review {review_id}")

        # Add diff-comment replies
        for item in diff_items:
            try:
                self.rb_client.post_diff_comment_reply(
                    rr_id,
                    review_id,
                    reply_id,
                    reply_to_id=item.comment_id,
                    text=item.reply_text,
                )
            except Exception as e:
                logger.error(f"Failed to add reply to comment {item.comment_id}: {e}")

        # Publish
        self.rb_client.publish_reply(rr_id, review_id, reply_id)
        logger.info(
            f"Published reply {reply_id} to review {review_id} "
            f"({len(diff_items)} diff + {len(body_items)} body replies)"
        )
        return reply_id

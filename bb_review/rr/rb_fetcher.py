"""Fetcher for all comments on a Review Board review request."""

import logging
import re

from ..triage.models import RBComment
from .rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)


class RBCommentFetcher:
    """Fetches and flattens all comments from a review request.

    Iterates over reviews, their diff comments, and body_top text,
    filtering out comments made by the bot itself.
    """

    def __init__(self, rb_client: ReviewBoardClient, bot_username: str):
        self.rb_client = rb_client
        self.bot_username = bot_username

    def fetch_all_comments(self, rr_id: int) -> list[RBComment]:
        """Fetch all comments for a review request.

        Returns a flat list of RBComment covering:
        - body_top text from each review (as body comments)
        - diff-level inline comments from each review
        Replies are included with reply_to_id set.
        """
        reviews = self.rb_client.get_reviews(rr_id)
        comments: list[RBComment] = []

        # Pre-warm filediff cache for resolving file paths from filediff links
        self.rb_client._warm_filediff_cache(rr_id)

        for review in reviews:
            reviewer = self._extract_username(review)
            if reviewer == self.bot_username:
                logger.debug(f"Skipping bot review {review.get('id')}")
                continue

            review_id = review["id"]

            # Body top as a body comment
            body_top = (review.get("body_top") or "").strip()
            if body_top:
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=review_id,  # use review_id as identifier for body comments
                        reviewer=reviewer,
                        text=body_top,
                        is_body_comment=True,
                        issue_opened=False,
                    )
                )

            # Diff comments
            diff_comments = self.rb_client.get_review_diff_comments(rr_id, review_id)
            for dc in diff_comments:
                file_path = self._resolve_file_path(rr_id, dc)
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=dc["id"],
                        reviewer=reviewer,
                        text=dc.get("text", ""),
                        file_path=file_path,
                        line_number=dc.get("first_line"),
                        issue_opened=dc.get("issue_opened", False),
                        issue_status=dc.get("issue_status"),
                    )
                )

            # Replies to this review
            replies = self.rb_client.get_review_replies(rr_id, review_id)
            for reply in replies:
                reply_reviewer = self._extract_username(reply)
                if reply_reviewer == self.bot_username:
                    continue

                reply_body = (reply.get("body_top") or "").strip()
                if reply_body:
                    comments.append(
                        RBComment(
                            review_id=review_id,
                            comment_id=reply["id"],
                            reviewer=reply_reviewer,
                            text=reply_body,
                            is_body_comment=True,
                            reply_to_id=review_id,
                        )
                    )

        logger.info(f"Fetched {len(comments)} comments for RR #{rr_id}")
        return comments

    def _extract_username(self, resource: dict) -> str:
        """Extract username from a review or reply resource."""
        links = resource.get("links", {})
        user_href = links.get("user", {}).get("href", "")
        # Href looks like: .../api/users/john_doe/
        match = re.search(r"/users/([^/]+)/", user_href)
        if match:
            return match.group(1)
        return "unknown"

    def _resolve_file_path(self, rr_id: int, diff_comment: dict) -> str | None:
        """Resolve the file path for a diff comment using the filediff cache."""
        # Try the filediff link to find the file path
        links = diff_comment.get("links", {})
        filediff_href = links.get("filediff", {}).get("href", "")

        # Extract filediff ID from href: .../filediffs/{id}/
        match = re.search(r"/filediffs/(\d+)/", filediff_href)
        if not match:
            return None

        filediff_id = int(match.group(1))
        files = self.rb_client._filediff_cache.get(rr_id, [])
        for f in files:
            if f.get("id") == filediff_id:
                return f.get("dest_file") or f.get("source_file")

        return None

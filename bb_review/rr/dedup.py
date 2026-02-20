"""Deduplication of review comments across diff iterations.

When an RR has multiple diffs, the bot re-analyzes each new diff from scratch.
If a developer dropped/rejected an issue on a prior diff, we should not re-file
the same issue. This module detects previously-dropped comments and filters them
out before posting.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re

from ..models import ReviewComment, ReviewResult
from .rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)

# Matches the formatted header: [SEVERITY] **SEVERITY** (type)
_HEADER_RE = re.compile(r"^\[[\w]+\]\s+\*\*\w+\*\*\s+\([^)]+\)\s*$", re.MULTILINE)
# Matches **Suggestion:** block at the end
_SUGGESTION_RE = re.compile(r"\n\*\*Suggestion:\*\*\n.*", re.DOTALL)


@dataclass
class DroppedComment:
    file_path: str
    text: str  # core message text (formatting stripped)


def fetch_dropped_comments(
    rb_client: ReviewBoardClient,
    rr_id: int,
    bot_username: str,
) -> list[DroppedComment]:
    """Fetch bot's own diff comments that were dropped on any prior review."""
    reviews = rb_client.get_reviews(rr_id)
    rb_client._warm_filediff_cache(rr_id)

    dropped: list[DroppedComment] = []
    for review in reviews:
        username = _extract_username(review)
        if username != bot_username:
            continue

        review_id = review["id"]
        diff_comments = rb_client.get_review_diff_comments(rr_id, review_id)
        for dc in diff_comments:
            if dc.get("issue_status") != "dropped":
                continue

            file_path = _resolve_file_path(rb_client, rr_id, dc)
            if not file_path:
                continue

            raw_text = dc.get("text", "")
            core = _extract_message_core(raw_text)
            dropped.append(DroppedComment(file_path=file_path, text=core))

    logger.debug("Fetched %d dropped comment(s) for RR #%d", len(dropped), rr_id)
    return dropped


def filter_dropped(
    result: ReviewResult,
    dropped: list[DroppedComment],
    threshold: float = 0.6,
) -> tuple[ReviewResult, list[ReviewComment]]:
    """Remove comments matching previously-dropped issues.

    Returns (filtered_result, removed_comments).
    """
    if not dropped:
        return result, []

    kept: list[ReviewComment] = []
    removed: list[ReviewComment] = []

    for comment in result.comments:
        if _is_duplicate(comment, dropped, threshold):
            removed.append(comment)
        else:
            kept.append(comment)

    if removed:
        logger.info("Filtered %d duplicate comment(s) for RR #%d", len(removed), result.review_request_id)

    filtered = ReviewResult(
        review_request_id=result.review_request_id,
        diff_revision=result.diff_revision,
        comments=kept,
        summary=result.summary,
        has_critical_issues=any(c.severity.value == "critical" for c in kept),
        analyzed_at=result.analyzed_at,
    )
    return filtered, removed


def _is_duplicate(
    comment: ReviewComment,
    dropped: list[DroppedComment],
    threshold: float,
) -> bool:
    """Check if a new comment matches any dropped comment on the same file."""
    for dc in dropped:
        if comment.file_path != dc.file_path:
            continue
        ratio = SequenceMatcher(None, comment.message, dc.text).ratio()
        if ratio >= threshold:
            logger.debug(
                'Duplicate (%.2f): %s:%d -> "%s"',
                ratio,
                comment.file_path,
                comment.line_number,
                comment.message[:60],
            )
            return True
    return False


def _extract_message_core(rb_text: str) -> str:
    """Strip RB formatting to get the raw message for comparison.

    Removes the [SEVERITY] **SEVERITY** (type) header line and
    the **Suggestion:** block at the end, leaving just the core message.
    """
    text = _HEADER_RE.sub("", rb_text)
    text = _SUGGESTION_RE.sub("", text)
    return text.strip()


# Reused patterns from rb_fetcher.py
def _extract_username(resource: dict) -> str:
    links = resource.get("links", {})
    user_href = links.get("user", {}).get("href", "")
    match = re.search(r"/users/([^/]+)/", user_href)
    return match.group(1) if match else "unknown"


def _resolve_file_path(rb_client: ReviewBoardClient, rr_id: int, diff_comment: dict) -> str | None:
    links = diff_comment.get("links", {})
    filediff_href = links.get("filediff", {}).get("href", "")
    match = re.search(r"/filediffs/(\d+)/", filediff_href)
    if not match:
        return None
    filediff_id = int(match.group(1))
    files = rb_client._filediff_cache.get(rr_id, [])
    for f in files:
        if f.get("id") == filediff_id:
            return f.get("dest_file") or f.get("source_file")
    return None

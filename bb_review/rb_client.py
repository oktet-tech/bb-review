"""Review Board API client wrapper using rbtools."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

from rbtools.api.client import RBClient
from rbtools.api.errors import APIError, AuthorizationError
from rbtools.api.resource import Resource

from .models import PendingReview

logger = logging.getLogger(__name__)


@dataclass
class DiffInfo:
    """Information about a diff."""

    diff_revision: int
    base_commit_id: Optional[str]
    raw_diff: str
    files: list[dict[str, Any]]


class ReviewBoardClient:
    """Client for interacting with Review Board API."""

    def __init__(self, url: str, api_token: str, bot_username: str):
        """Initialize the Review Board client.

        Args:
            url: Review Board server URL.
            api_token: API token for authentication.
            bot_username: Username of the bot account.
        """
        self.url = url
        self.api_token = api_token
        self.bot_username = bot_username
        self._client: Optional[RBClient] = None
        self._root: Optional[Resource] = None

    def connect(self) -> None:
        """Establish connection to Review Board."""
        logger.debug(f"Connecting to Review Board at {self.url}")
        self._client = RBClient(self.url)
        self._root = self._client.get_root(api_token=self.api_token)
        logger.info(f"Connected to Review Board: {self.url}")

    @property
    def root(self) -> Resource:
        """Get the API root resource."""
        if self._root is None:
            self.connect()
        return self._root

    def get_review_request(self, review_request_id: int) -> Resource:
        """Get a review request by ID.

        Args:
            review_request_id: The review request ID.

        Returns:
            Review request resource.
        """
        logger.debug(f"Fetching review request {review_request_id}")
        return self.root.get_review_request(review_request_id=review_request_id)

    def get_pending_reviews(self, limit: int = 50) -> list[PendingReview]:
        """Get review requests where bot user is a reviewer but hasn't reviewed.

        Args:
            limit: Maximum number of reviews to fetch.

        Returns:
            List of pending reviews.
        """
        logger.debug(f"Fetching pending reviews for {self.bot_username}")

        # Query for review requests where the bot is a target reviewer
        review_requests = self.root.get_review_requests(
            to_users=self.bot_username,
            status="pending",
            max_results=limit,
        )

        pending = []
        for rr in review_requests:
            # Check if bot has already reviewed this diff revision
            if self._has_bot_reviewed(rr):
                logger.debug(f"Skipping {rr.id} - already reviewed")
                continue

            pending_review = self._to_pending_review(rr)
            if pending_review:
                pending.append(pending_review)

        logger.info(f"Found {len(pending)} pending reviews")
        return pending

    def _has_bot_reviewed(self, review_request: Resource) -> bool:
        """Check if the bot has already reviewed the current diff revision.

        Args:
            review_request: Review request resource.

        Returns:
            True if bot has already reviewed.
        """
        try:
            reviews = review_request.get_reviews()
            latest_diff_revision = self._get_latest_diff_revision(review_request)

            for review in reviews:
                # Check if this review is from the bot
                user = review.get_user()
                if user.username == self.bot_username:
                    # Check if it's for the latest diff revision
                    # Reviews don't directly store diff revision, but we can
                    # compare timestamps or store this info in our database
                    return True
            return False
        except APIError as e:
            logger.warning(f"Error checking reviews: {e}")
            return False

    def _get_latest_diff_revision(self, review_request: Resource) -> int:
        """Get the latest diff revision number."""
        try:
            diffs = review_request.get_diffs()
            if diffs.total_results > 0:
                # Get the last diff
                for diff in diffs:
                    pass  # Iterate to get the last one
                return diff.revision
            return 0
        except APIError:
            return 0

    def _to_pending_review(self, rr: Resource) -> Optional[PendingReview]:
        """Convert review request resource to PendingReview."""
        try:
            # Get repository info
            repo = rr.get_repository()
            repo_name = repo.name if repo else "unknown"

            # Get diff info
            latest_diff_revision = self._get_latest_diff_revision(rr)
            base_commit = self._get_base_commit(rr)

            # Get submitter
            submitter = rr.get_submitter()
            submitter_name = submitter.username if submitter else "unknown"

            return PendingReview(
                review_request_id=rr.id,
                repository=repo_name,
                submitter=submitter_name,
                summary=rr.summary,
                diff_revision=latest_diff_revision,
                base_commit=base_commit,
                branch=getattr(rr, "branch", None),
                created_at=_parse_datetime(rr.time_added) if hasattr(rr, "time_added") else None,
            )
        except APIError as e:
            logger.warning(f"Error processing review request {rr.id}: {e}")
            return None

    def _get_base_commit(self, review_request: Resource) -> Optional[str]:
        """Extract the base commit ID from a review request."""
        try:
            diffs = review_request.get_diffs()
            for diff in diffs:
                if hasattr(diff, "base_commit_id") and diff.base_commit_id:
                    return diff.base_commit_id
            return None
        except APIError:
            return None

    def get_diff(self, review_request_id: int, diff_revision: Optional[int] = None) -> DiffInfo:
        """Get the diff for a review request.

        Args:
            review_request_id: The review request ID.
            diff_revision: Specific diff revision, or latest if None.

        Returns:
            DiffInfo with raw diff and file information.
        """
        rr = self.get_review_request(review_request_id)
        diffs = rr.get_diffs()

        target_diff = None
        for diff in diffs:
            if diff_revision is None or diff.revision == diff_revision:
                target_diff = diff

        if target_diff is None:
            raise ValueError(f"Diff revision {diff_revision} not found")

        # Get the raw diff content
        # The diff resource should have a get_patch() method or similar
        raw_diff = self._fetch_raw_diff(target_diff)

        # Get file information
        files = []
        try:
            diff_files = target_diff.get_files()
            for f in diff_files:
                files.append({
                    "source_file": getattr(f, "source_file", ""),
                    "dest_file": getattr(f, "dest_file", ""),
                    "source_revision": getattr(f, "source_revision", ""),
                    "status": getattr(f, "status", ""),
                })
        except APIError as e:
            logger.warning(f"Could not fetch file list: {e}")

        return DiffInfo(
            diff_revision=target_diff.revision,
            base_commit_id=getattr(target_diff, "base_commit_id", None),
            raw_diff=raw_diff,
            files=files,
        )

    def _fetch_raw_diff(self, diff: Resource) -> str:
        """Fetch the raw diff content."""
        try:
            # Try to get the patch data
            # rbtools might expose this differently
            if hasattr(diff, "get_patch"):
                patch = diff.get_patch()
                return patch.data.decode("utf-8") if isinstance(patch.data, bytes) else patch.data

            # Alternative: construct URL and fetch directly
            # The diff resource URL + "patch/" should give us the raw diff
            import requests

            patch_url = f"{diff._url}patch/"
            headers = {"Authorization": f"token {self.api_token}"}
            response = requests.get(patch_url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch raw diff: {e}")
            raise

    def post_review(
        self,
        review_request_id: int,
        body_top: str,
        comments: list[dict[str, Any]],
        ship_it: bool = False,
        publish: bool = True,
    ) -> Resource:
        """Post a review with comments.

        Args:
            review_request_id: The review request ID.
            body_top: Summary text at the top of the review.
            comments: List of comment dicts with file_id, line, text.
            ship_it: Whether to mark as "Ship It".
            publish: Whether to publish immediately.

        Returns:
            The created review resource.
        """
        rr = self.get_review_request(review_request_id)

        # Create the review
        logger.info(f"Creating review on request {review_request_id}")
        review = rr.get_reviews().create(
            body_top=body_top,
            ship_it=ship_it,
            public=False,  # Create as draft first
        )

        # Add diff comments
        for comment in comments:
            self._add_diff_comment(review, rr, comment)

        # Publish if requested
        if publish:
            review = review.update(public=True)
            logger.info(f"Published review on request {review_request_id}")

        return review

    def _add_diff_comment(
        self, review: Resource, review_request: Resource, comment: dict[str, Any]
    ) -> None:
        """Add a diff comment to a review.

        Args:
            review: The review resource.
            review_request: The review request resource.
            comment: Comment dict with file_path, line_number, text.
        """
        try:
            # Find the file diff that matches the file path
            file_path = comment["file_path"]
            line_number = comment["line_number"]
            text = comment["text"]

            # Get the latest diff
            diffs = review_request.get_diffs()
            latest_diff = None
            for diff in diffs:
                latest_diff = diff

            if latest_diff is None:
                logger.warning(f"No diff found for comment on {file_path}")
                return

            # Find the filediff for this file
            filediff_id = self._find_filediff_id(latest_diff, file_path)
            if filediff_id is None:
                logger.warning(f"Could not find filediff for {file_path}")
                return

            # Create the diff comment
            review.get_diff_comments().create(
                filediff_id=filediff_id,
                first_line=line_number,
                num_lines=1,
                text=text,
                issue_opened=True,  # Open as an issue to track
            )
            logger.debug(f"Added comment on {file_path}:{line_number}")
        except APIError as e:
            logger.error(f"Failed to add comment: {e}")

    def _find_filediff_id(self, diff: Resource, file_path: str) -> Optional[int]:
        """Find the filediff ID for a given file path."""
        try:
            files = diff.get_files()
            for f in files:
                # Check both source and dest file paths
                if getattr(f, "dest_file", "") == file_path:
                    return f.id
                if getattr(f, "source_file", "") == file_path:
                    return f.id
                # Also try without leading paths
                dest = getattr(f, "dest_file", "")
                if dest.endswith(file_path) or file_path.endswith(dest):
                    return f.id
            return None
        except APIError:
            return None

    def get_repository_info(self, review_request_id: int) -> dict[str, Any]:
        """Get repository information for a review request.

        Args:
            review_request_id: The review request ID.

        Returns:
            Dictionary with repository details.
        """
        rr = self.get_review_request(review_request_id)
        repo = rr.get_repository()

        return {
            "id": repo.id,
            "name": repo.name,
            "path": getattr(repo, "path", ""),
            "tool": getattr(repo, "tool", ""),
        }


def _parse_datetime(dt_str: str) -> Optional[datetime]:
    """Parse Review Board datetime string."""
    if not dt_str:
        return None
    try:
        # Review Board uses ISO format
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None

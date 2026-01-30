"""Mock ReviewBoard client for testing."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockDiffInfo:
    """Mock diff information."""

    diff_revision: int = 1
    base_commit_id: str | None = "abc123"
    target_commit_id: str | None = None
    raw_diff: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)


class MockRBClient:
    """Mock ReviewBoard client for testing.

    Provides configurable responses without making real API calls.
    """

    def __init__(
        self,
        reviews: dict[int, dict] | None = None,
        diffs: dict[int, MockDiffInfo] | None = None,
        repositories: dict[int, dict] | None = None,
    ):
        """Initialize the mock client.

        Args:
            reviews: Mapping of review_id to review request data.
            diffs: Mapping of review_id to diff info.
            repositories: Mapping of review_id to repository info.
        """
        self.reviews = reviews or {}
        self.diffs = diffs or {}
        self.repositories = repositories or {}
        self.posted_reviews: list[dict[str, Any]] = []
        self._connected = False

    def connect(self) -> None:
        """Mock connection (always succeeds)."""
        self._connected = True

    def get_review_request(self, review_request_id: int) -> dict:
        """Get a mock review request.

        Args:
            review_request_id: Review ID.

        Returns:
            Review request data dict.
        """
        if review_request_id in self.reviews:
            return self.reviews[review_request_id]

        # Return default mock data
        return {
            "id": review_request_id,
            "summary": f"Test review #{review_request_id}",
            "description": "Test description",
            "branch": "main",
            "submitter": {"username": "testuser"},
            "links": {
                "repository": {"href": "/api/repositories/1/"},
            },
        }

    def get_repository_info(self, review_request_id: int) -> dict[str, Any]:
        """Get mock repository info.

        Args:
            review_request_id: Review ID.

        Returns:
            Repository info dict.
        """
        if review_request_id in self.repositories:
            return self.repositories[review_request_id]

        return {
            "id": 1,
            "name": "test-repo",
            "path": "/path/to/repo",
            "tool": "Git",
        }

    def get_diff(self, review_request_id: int, diff_revision: int | None = None) -> MockDiffInfo:
        """Get mock diff info.

        Args:
            review_request_id: Review ID.
            diff_revision: Optional specific revision.

        Returns:
            MockDiffInfo instance.
        """
        if review_request_id in self.diffs:
            return self.diffs[review_request_id]

        return MockDiffInfo(
            diff_revision=diff_revision or 1,
            base_commit_id="abc123def456",
            raw_diff=SAMPLE_DIFF,
        )

    def post_review(
        self,
        review_request_id: int,
        body_top: str,
        comments: list[dict[str, Any]],
        ship_it: bool = False,
        publish: bool = True,
    ) -> dict:
        """Mock posting a review.

        Args:
            review_request_id: Review ID.
            body_top: Review body text.
            comments: List of inline comments.
            ship_it: Whether to mark as ship-it.
            publish: Whether to publish immediately.

        Returns:
            Mock review response.
        """
        review_data = {
            "review_request_id": review_request_id,
            "body_top": body_top,
            "comments": comments,
            "ship_it": ship_it,
            "publish": publish,
        }
        self.posted_reviews.append(review_data)

        return {"id": len(self.posted_reviews)}

    def get_pending_reviews(self, limit: int = 50) -> list:
        """Get mock pending reviews (returns empty by default)."""
        return []

    def reset(self) -> None:
        """Clear posted reviews history."""
        self.posted_reviews = []


# Sample diff for testing
SAMPLE_DIFF = """diff --git a/src/main.c b/src/main.c
index abc123..def456 100644
--- a/src/main.c
+++ b/src/main.c
@@ -10,6 +10,8 @@ int main() {
     printf("Hello World\\n");
+    int x = 42;
+    printf("x = %d\\n", x);
     return 0;
 }
"""


class MockRBClientError(MockRBClient):
    """Mock RB client that raises errors."""

    def __init__(self, error: Exception | None = None):
        super().__init__()
        self.error = error or RuntimeError("Connection failed")

    def connect(self) -> None:
        raise self.error


class MockRBClientAuthError(MockRBClient):
    """Mock RB client that fails on auth."""

    def connect(self) -> None:
        raise RuntimeError("Authentication failed: Invalid credentials")

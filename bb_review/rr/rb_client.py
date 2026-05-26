"""Review Board API client with Kerberos support via curl."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

from ..models import PendingReview


logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """Raised when authentication to Review Board fails."""


@dataclass
class DiffInfo:
    """Information about a diff."""

    diff_revision: int
    base_commit_id: str | None
    target_commit_id: str | None  # The actual commit being reviewed (if available)
    raw_diff: str
    files: list[dict[str, Any]]


@dataclass
class ReviewRequestInfo:
    """Essential information about a review request for chain resolution."""

    id: int
    summary: str
    status: str  # pending, submitted, discarded
    repository_name: str
    depends_on: list[int]  # List of RR IDs this depends on
    base_commit_id: str | None
    diff_revision: int
    description: str = ""  # Full description text

    @property
    def full_summary(self) -> str:
        """Return summary + description combined."""
        if self.description:
            return f"{self.summary}\n\n{self.description}"
        return self.summary


class ReviewBoardClient:
    """Client for interacting with Review Board API.

    Uses curl with Kerberos (--negotiate) for authentication through Apache,
    and manages Review Board session cookies.
    """

    def __init__(
        self,
        url: str,
        bot_username: str,
        api_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        use_kerberos: bool = False,
    ):
        """Initialize the Review Board client.

        Args:
            url: Review Board server URL.
            bot_username: Username of the bot account.
            api_token: API token (not used with Kerberos - Apache strips it).
            username: Username for RB login.
            password: Password for RB login.
            use_kerberos: Use Kerberos for Apache layer.
        """
        self.url = url.rstrip("/")
        self.api_token = api_token
        self.username = username
        self.password = password
        self.use_kerberos = use_kerberos
        self.bot_username = bot_username
        self._cookie_file: Path | None = None
        self._connected = False
        self._filediff_cache: dict[int, list[dict]] = {}

    def connect(self) -> None:
        """Establish connection to Review Board."""
        logger.debug(f"Connecting to Review Board at {self.url}")

        # Create a temp cookie file for this session
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".cookies", prefix="rb_")
        tmp.close()
        self._cookie_file = Path(tmp.name)

        if self.use_kerberos:
            logger.info("Using Kerberos authentication for Apache layer")

            if self.username and self.password:
                # Login to Review Board with credentials through Kerberos
                self._rb_login()
            else:
                # Just establish Kerberos session
                self._kerberos_init()
        else:
            # Non-Kerberos: use API token or basic auth directly
            self._init_session()

        # Verify connection
        root = self._api_get("/api/")
        if root.get("stat") == "fail":
            err_msg = root.get("err", {}).get("msg", "Unknown error")
            raise RuntimeError(f"Failed to connect to Review Board: {err_msg}")

        self._connected = True
        logger.info(f"Connected to Review Board: {self.url}")

    def _curl(
        self,
        url: str,
        method: str = "GET",
        data: dict | None = None,
        headers: dict | None = None,
        accept: str = "application/json",
    ) -> tuple[int, str]:
        """Execute a curl request with Kerberos support.

        Returns:
            Tuple of (status_code, response_body)
        """
        cmd = ["curl", "-s", "-w", "\n%{http_code}", "--connect-timeout", "10", "--max-time", "30"]

        # Use cookies
        if self._cookie_file:
            cmd.extend(["-b", str(self._cookie_file)])
            cmd.extend(["-c", str(self._cookie_file)])

        # Kerberos auth
        if self.use_kerberos:
            cmd.extend(["--negotiate", "-u", ":"])

        if self.api_token and not headers:
            headers = {
                "Authorization": f"token {self.api_token}"
            }

        # Method
        if method != "GET":
            cmd.extend(["-X", method])

        # Headers
        cmd.extend(["-H", f"Accept: {accept}"])
        if headers:
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])

        # Data - use --data-urlencode to properly encode special characters
        if data:
            for k, v in data.items():
                cmd.extend(["--data-urlencode", f"{k}={v}"])

        cmd.append(url)

        logger.debug(f"curl {method} {url}")
        if data:
            logger.debug(f"curl data: {data}")

        # Retry on transient curl errors (timeout, connection refused/reset)
        # and on upstream-unavailable HTTP statuses from the RB load balancer.
        retryable_curl_codes = {7, 28, 56}
        retryable_http_codes = {502, 503, 504}
        max_retries = 2
        status_code = 0
        body = ""
        for attempt in range(max_retries + 1):
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                if result.returncode in retryable_curl_codes and attempt < max_retries:
                    delay = 2 * (attempt + 1)
                    logger.warning(
                        f"curl failed for {url} (exit {result.returncode}), retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                detail = (
                    result.stderr.strip() or f"exit code {result.returncode} (timeout or connection error)"
                )
                logger.error(f"curl failed for {url}: {detail}")
                raise RuntimeError(f"curl failed: {detail}")

            status_code, body = _split_status_and_body(result.stdout)

            if status_code in retryable_http_codes and attempt < max_retries:
                delay = 2 * (attempt + 1)
                logger.warning(f"HTTP {status_code} from {url}, retrying in {delay}s...")
                time.sleep(delay)
                continue
            break

        return status_code, body

    def _kerberos_init(self) -> None:
        """Initialize Kerberos session without RB login."""
        status, body = self._curl(f"{self.url}/api/")
        logger.debug(f"Kerberos init: status={status}")
        if status == 401:
            raise AuthenticationError(
                "Kerberos authentication failed (HTTP 401). "
                "Your ticket may be expired or missing -- run `kinit` to obtain a new one."
            )

    def _rb_login(self) -> None:
        """Login to Review Board using form-based login (like browser).

        This is needed when Apache handles Kerberos and strips Authorization headers,
        preventing API token or Basic auth from reaching Review Board.
        """
        logger.info(f"Logging into Review Board as {self.username}")

        # Step 1: Get the login page to obtain CSRF token
        status, body = self._curl(
            f"{self.url}/account/login/",
            accept="text/html",
        )

        if status == 401 and self.use_kerberos:
            raise AuthenticationError(
                "Kerberos authentication failed (HTTP 401). "
                "Your ticket may be expired or missing -- run `kinit` to obtain a new one."
            )
        if status != 200:
            raise RuntimeError(f"Failed to get login page: HTTP {status}")

        # Extract CSRF token from form
        csrf_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
        if not csrf_match:
            raise RuntimeError("Could not find CSRF token in login page")
        csrf_token = csrf_match.group(1)

        # Step 2: Submit the login form
        status, body = self._curl(
            f"{self.url}/account/login/",
            method="POST",
            data={
                "csrfmiddlewaretoken": csrf_token,
                "username": self.username,
                "password": self.password,
            },
            headers={"Referer": f"{self.url}/account/login/"},
            accept="text/html",
        )

        # Check if login succeeded - we should get a redirect (302) or session cookie
        # On success, RB redirects to dashboard; on failure, it shows error page
        if "Please enter a correct username and password" in body:
            raise RuntimeError("RB login failed: Invalid username or password")
        if "errorlist" in body.lower() and "error" in body.lower():
            raise RuntimeError("RB login failed: Check credentials")

        # Verify we're now authenticated by checking the session
        status, body = self._curl(f"{self.url}/api/session/")
        try:
            result = json.loads(body)
            if result.get("stat") == "ok" and result.get("session", {}).get("authenticated"):
                logger.info("Successfully logged into Review Board")
                return
        except json.JSONDecodeError:
            pass

        raise RuntimeError("RB login failed: Could not establish session")

    def _init_session(self) -> None:
        """Initialize session for non-Kerberos auth."""
        if self.username and self.password:
            self._rb_login()

    def _api_get(self, path: str, params: dict | None = None) -> dict:
        """Make a GET request to the API."""
        url = f"{self.url}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        status, body = self._curl(url)
        return _decode_api_response(status, body, path)

    def _api_post(self, path: str, data: dict | None = None) -> dict:
        """Make a POST request to the API."""
        url = f"{self.url}{path}"
        status, body = self._curl(url, method="POST", data=data)
        return _decode_api_response(status, body, path)

    def _api_put(self, path: str, data: dict | None = None) -> dict:
        """Make a PUT request to the API."""
        url = f"{self.url}{path}"
        status, body = self._curl(url, method="PUT", data=data)
        return _decode_api_response(status, body, path)

    def get_review_request(self, review_request_id: int) -> dict:
        """Get a review request by ID."""
        logger.debug(f"Fetching review request {review_request_id}")
        result = self._api_get(f"/api/review-requests/{review_request_id}/")
        if result.get("stat") == "fail":
            raise RuntimeError(f"Failed to get review request: {result.get('err', {}).get('msg')}")
        return result.get("review_request", result)

    def get_pending_reviews(
        self,
        limit: int = 50,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PendingReview]:
        """Get review requests where bot user is a reviewer but hasn't reviewed."""
        logger.debug(f"Fetching pending reviews for {self.bot_username}")

        result = self._api_get(
            "/api/review-requests/",
            {
                "to-users": self.bot_username,
                "status": "pending",
                "max-results": str(limit),
            },
        )

        review_requests = result.get("review_requests", [])
        total = len(review_requests)
        pending = []

        for i, rr in enumerate(review_requests):
            if self._has_bot_reviewed(rr["id"]):
                logger.debug(f"Skipping {rr['id']} - already reviewed")
            else:
                pending_review = self._to_pending_review(rr)
                if pending_review:
                    pending.append(pending_review)
            if on_progress:
                on_progress(i + 1, total)

        logger.info(f"Found {len(pending)} pending reviews")
        return pending

    def _has_bot_reviewed(self, review_request_id: int) -> bool:
        """Check if the bot has already reviewed."""
        try:
            result = self._api_get(f"/api/review-requests/{review_request_id}/reviews/")
            for review in result.get("reviews", []):
                links = review.get("links", {})
                user_href = links.get("user", {}).get("href", "")
                if self.bot_username in user_href:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Error checking reviews: {e}")
            return False

    def _get_latest_diff_revision(self, review_request_id: int) -> int:
        """Get the latest diff revision number."""
        try:
            result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/")
            diffs = result.get("diffs", [])
            return diffs[-1].get("revision", 0) if diffs else 0
        except Exception:
            return 0

    def _to_pending_review(self, rr: dict) -> PendingReview | None:
        """Convert review request dict to PendingReview."""
        try:
            review_request_id = rr["id"]

            repo_name = "unknown"
            links = rr.get("links", {})
            if "repository" in links:
                try:
                    repo_href = links["repository"]["href"]
                    repo_path = repo_href.replace(self.url, "")
                    repo_result = self._api_get(repo_path)
                    repo_name = repo_result.get("repository", {}).get("name", "unknown")
                except Exception:
                    pass

            latest_diff_revision = self._get_latest_diff_revision(review_request_id)
            base_commit = self._get_base_commit(review_request_id)

            submitter_name = "unknown"
            if "submitter" in links:
                try:
                    user_href = links["submitter"]["href"]
                    user_path = user_href.replace(self.url, "")
                    user_result = self._api_get(user_path)
                    submitter_name = user_result.get("user", {}).get("username", "unknown")
                except Exception:
                    pass

            return PendingReview(
                review_request_id=review_request_id,
                repository=repo_name,
                submitter=submitter_name,
                summary=rr.get("summary", ""),
                diff_revision=latest_diff_revision,
                base_commit=base_commit,
                branch=rr.get("branch"),
                created_at=_parse_datetime(rr.get("time_added")),
                issue_open_count=rr.get("issue_open_count", 0),
                ship_it_count=rr.get("ship_it_count", 0),
            )
        except Exception as e:
            logger.warning(f"Error processing review request {rr.get('id')}: {e}")
            return None

    def _get_base_commit(self, review_request_id: int) -> str | None:
        """Extract the base commit ID from a review request."""
        try:
            result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/")
            for diff in result.get("diffs", []):
                if diff.get("base_commit_id"):
                    return diff["base_commit_id"]
            return None
        except Exception:
            return None

    def diffs_equal(self, review_request_id: int, rev_a: int, rev_b: int) -> bool:
        """Check if two diff revisions have identical patch content.

        Used to distinguish real code changes from commit-message-only updates.
        Returns False on any API error (safer to assume content changed).
        """
        try:
            diff_a = self._fetch_raw_diff(review_request_id, rev_a)
            diff_b = self._fetch_raw_diff(review_request_id, rev_b)
            return diff_a == diff_b
        except Exception as e:
            logger.debug(f"r/{review_request_id}: diffs_equal({rev_a}, {rev_b}) failed: {e}")
            return False

    def _get_target_commit(self, review_request_id: int, diff_revision: int) -> str | None:
        """Get the target commit ID from the commits endpoint (RB 4.0+).

        For post-commit reviews, this returns the actual commit being reviewed.
        For pre-commit reviews or older RB versions, returns None.
        """
        try:
            result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/{diff_revision}/commits/")
            commits = result.get("commits", [])
            if commits:
                # Return the last commit (tip of the review)
                return commits[-1].get("commit_id")
            return None
        except Exception as e:
            logger.debug(f"Could not get target commit: {e}")
            return None

    def get_diff(self, review_request_id: int, diff_revision: int | None = None) -> DiffInfo:
        """Get the diff for a review request."""
        result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/")
        diffs = result.get("diffs", [])

        target_diff = None
        for diff in diffs:
            if diff_revision is None or diff.get("revision") == diff_revision:
                target_diff = diff

        if target_diff is None:
            raise ValueError(f"Diff revision {diff_revision} not found")

        revision = target_diff["revision"]

        # Get raw diff
        raw_diff = self._fetch_raw_diff(review_request_id, revision)

        # Get file info
        files = []
        try:
            files_result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/{revision}/files/")
            for f in files_result.get("files", []):
                files.append(
                    {
                        "id": f.get("id"),
                        "source_file": f.get("source_file", ""),
                        "dest_file": f.get("dest_file", ""),
                        "source_revision": f.get("source_revision", ""),
                        "status": f.get("status", ""),
                    }
                )
        except Exception as e:
            logger.warning(f"Could not fetch file list: {e}")

        # Try to get target commit (post-commit reviews)
        target_commit_id = None
        if target_diff.get("commit_count", 0) > 0:
            target_commit_id = self._get_target_commit(review_request_id, revision)

        return DiffInfo(
            diff_revision=revision,
            base_commit_id=target_diff.get("base_commit_id"),
            target_commit_id=target_commit_id,
            raw_diff=raw_diff,
            files=files,
        )

    def _fetch_raw_diff(self, review_request_id: int, revision: int) -> str:
        """Fetch the raw diff content."""
        # Use the diff resource endpoint with text/x-patch Accept header
        url = f"{self.url}/api/review-requests/{review_request_id}/diffs/{revision}/"
        status, body = self._curl(url, accept="text/x-patch")

        # Check if we got HTML instead of a diff (indicates auth or redirect issue)
        if body.strip().startswith("<!DOCTYPE") or body.strip().startswith("<html"):
            logger.error(f"Got HTML instead of diff (status {status}). First 200 chars: {body[:200]}")
            raise RuntimeError(f"Failed to fetch diff: got HTML response (status {status})")

        logger.debug(f"Fetched raw diff: {len(body)} chars, status {status}")
        return body

    def post_review(
        self,
        review_request_id: int,
        body_top: str,
        comments: list[dict[str, Any]],
        ship_it: bool = False,
        publish: bool = True,
    ) -> dict:
        """Post a review with comments."""
        logger.info(f"Creating review on request {review_request_id}")

        result = self._api_post(
            f"/api/review-requests/{review_request_id}/reviews/",
            {
                "body_top": body_top,
                "body_top_text_type": "markdown",
                "ship_it": "1" if ship_it else "0",
                "public": "0",
            },
        )

        if result.get("stat") != "ok":
            raise RuntimeError(f"Failed to create review: {result}")

        review = result.get("review", result)
        review_id = review["id"]

        # Pre-warm filediff cache to avoid N+1 API calls
        if comments:
            self._warm_filediff_cache(review_request_id)

        # Add comments
        for comment in comments:
            self._add_diff_comment(review_request_id, review_id, comment)

        # Publish
        if publish:
            self._api_put(
                f"/api/review-requests/{review_request_id}/reviews/{review_id}/",
                {"public": "1"},
            )
            logger.info(f"Published review {review_id}")

        return review

    def _add_diff_comment(self, review_request_id: int, review_id: int, comment: dict[str, Any]) -> None:
        """Add a diff comment to a review."""
        try:
            file_path = comment["file_path"]
            line_number = comment["line_number"]
            text = comment["text"]

            filediff_id = self._find_filediff_id(review_request_id, file_path)
            if filediff_id is None:
                logger.warning(f"Could not find filediff for {file_path}")
                return

            self._api_post(
                f"/api/review-requests/{review_request_id}/reviews/{review_id}/diff-comments/",
                {
                    "filediff_id": str(filediff_id),
                    "first_line": str(line_number),
                    "num_lines": "1",
                    "text": text,
                    "text_type": "markdown",
                    "issue_opened": "1",
                },
            )
            logger.debug(f"Added comment on {file_path}:{line_number}")
        except Exception as e:
            logger.error(f"Failed to add comment: {e}")

    def _warm_filediff_cache(self, review_request_id: int) -> None:
        """Fetch filediff list once and cache it to avoid repeated API calls."""
        if review_request_id in self._filediff_cache:
            return
        try:
            result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/")
            diffs = result.get("diffs", [])
            if not diffs:
                self._filediff_cache[review_request_id] = []
                return
            rev = diffs[-1]["revision"]
            files_result = self._api_get(f"/api/review-requests/{review_request_id}/diffs/{rev}/files/")
            self._filediff_cache[review_request_id] = files_result.get("files", [])
        except Exception:
            self._filediff_cache[review_request_id] = []

    def _find_filediff_id(self, review_request_id: int, file_path: str) -> int | None:
        """Find the filediff ID for a given file path (uses cache if available)."""
        files = self._filediff_cache.get(review_request_id)
        if files is None:
            self._warm_filediff_cache(review_request_id)
            files = self._filediff_cache.get(review_request_id, [])

        for f in files:
            dest = f.get("dest_file", "")
            source = f.get("source_file", "")

            if dest == file_path or source == file_path:
                return f["id"]
            if dest.endswith(file_path) or file_path.endswith(dest):
                return f["id"]

        return None

    def get_reviews(self, review_request_id: int) -> list[dict]:
        """Get all reviews for a review request."""
        result = self._api_get(f"/api/review-requests/{review_request_id}/reviews/")
        return result.get("reviews", [])

    def get_review_diff_comments(self, review_request_id: int, review_id: int) -> list[dict]:
        """Get diff comments for a specific review."""
        result = self._api_get(f"/api/review-requests/{review_request_id}/reviews/{review_id}/diff-comments/")
        return result.get("diff_comments", [])

    def get_review_replies(self, review_request_id: int, review_id: int) -> list[dict]:
        """Get replies to a specific review."""
        result = self._api_get(f"/api/review-requests/{review_request_id}/reviews/{review_id}/replies/")
        return result.get("replies", [])

    def post_reply(self, review_request_id: int, review_id: int, body_top: str = "") -> dict:
        """Create a reply draft on a review. Returns the reply resource."""
        result = self._api_post(
            f"/api/review-requests/{review_request_id}/reviews/{review_id}/replies/",
            {"body_top": body_top, "body_top_text_type": "markdown", "public": "0"},
        )
        if result.get("stat") != "ok":
            raise RuntimeError(f"Failed to create reply: {result}")
        return result.get("reply", result)

    def post_diff_comment_reply(
        self,
        review_request_id: int,
        review_id: int,
        reply_id: int,
        reply_to_id: int,
        text: str,
    ) -> None:
        """Add a diff-comment reply to an existing reply draft."""
        result = self._api_post(
            f"/api/review-requests/{review_request_id}/reviews/{review_id}/replies/{reply_id}/diff-comments/",
            {"reply_to_id": str(reply_to_id), "text": text, "text_type": "markdown"},
        )
        if result.get("stat") != "ok":
            raise RuntimeError(f"Failed to post diff comment reply: {result}")

    def update_issue_status(
        self,
        review_request_id: int,
        review_id: int,
        comment_id: int,
        issue_status: str,
    ) -> dict:
        """Update the issue status of a diff comment.

        Args:
            issue_status: One of 'open', 'resolved', 'dropped', 'verifying'.
        """
        result = self._api_put(
            f"/api/review-requests/{review_request_id}/reviews/{review_id}/diff-comments/{comment_id}/",
            {"issue_status": issue_status},
        )
        if result.get("stat") != "ok":
            raise RuntimeError(f"Failed to update issue status: {result}")
        return result

    def publish_reply(self, review_request_id: int, review_id: int, reply_id: int) -> None:
        """Publish a reply draft."""
        result = self._api_put(
            f"/api/review-requests/{review_request_id}/reviews/{review_id}/replies/{reply_id}/",
            {"public": "1"},
        )
        if result.get("stat") != "ok":
            raise RuntimeError(f"Failed to publish reply: {result}")

    def get_repository_info(self, review_request_id: int) -> dict[str, Any]:
        """Get repository information for a review request."""
        rr = self.get_review_request(review_request_id)

        links = rr.get("links", {})
        if "repository" not in links:
            return {"id": 0, "name": "unknown", "path": "", "tool": ""}

        repo_href = links["repository"]["href"]
        repo_path = repo_href.replace(self.url, "")
        repo_result = self._api_get(repo_path)
        repo = repo_result.get("repository", {})

        return {
            "id": repo.get("id", 0),
            "name": repo.get("name", "unknown"),
            "path": repo.get("path", ""),
            "tool": repo.get("tool", ""),
        }

    def get_recent_reviews(
        self,
        days: int = 10,
        limit: int = 200,
        repository: str | None = None,
        from_user: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PendingReview]:
        """Fetch recently-updated pending review requests from RB.

        Args:
            days: How far back to look (via last-updated-from).
            limit: Max results to return.
            repository: Filter by repository name (RB repo ID or name).
            from_user: Filter by submitter username.
            on_progress: Called with (current, total) after each RR is processed.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        params: dict[str, str] = {
            "status": "pending",
            "last-updated-from": cutoff,
            "max-results": str(limit),
        }
        if repository:
            params["repository"] = repository
        if from_user:
            params["from-user"] = from_user

        logger.debug(f"Fetching recent reviews: days={days}, limit={limit}")
        result = self._api_get("/api/review-requests/", params)
        review_requests = result.get("review_requests", [])
        total = len(review_requests)

        pending = []
        for i, rr in enumerate(review_requests):
            pr = self._to_pending_review(rr)
            if pr:
                pending.append(pr)
            if on_progress:
                on_progress(i + 1, total)

        logger.info(f"Fetched {len(pending)} recent reviews")
        return pending

    def resolve_repository_id(self, name_or_id: str) -> str:
        """Resolve an RB repository name to its numeric ID.

        RB's `?repository=` filter on `/api/review-requests/` only accepts
        the numeric repo ID, not the human-readable name. This helper looks
        up the ID via `/api/repositories/?name=...` so callers can pass
        either form.

        Args:
            name_or_id: A numeric repo ID (returned unchanged) or RB repo name.

        Returns:
            Numeric repo ID as a string.

        Raises:
            RuntimeError: If no repository matches the given name.
        """
        if name_or_id.isdigit():
            return name_or_id

        result = self._api_get("/api/repositories/", {"name": name_or_id})
        repos = result.get("repositories", [])
        if not repos:
            raise RuntimeError(f"No Review Board repository found with name '{name_or_id}'")
        repo_id = str(repos[0].get("id", ""))
        if not repo_id:
            raise RuntimeError(f"Review Board repository '{name_or_id}' has no id field")
        logger.debug(f"Resolved RB repository '{name_or_id}' to id {repo_id}")
        return repo_id

    def list_repo_review_requests(
        self,
        repository: str,
        statuses: list[str],
        limit: int = 50,
        days: int = 0,
    ) -> list[dict]:
        """List review requests for a repository, filtered by status.

        RB's `status` query parameter accepts a single value, so each
        status is queried separately and the results are merged,
        de-duplicated by id, and sorted by last_updated descending.

        Args:
            repository: RB repository name or numeric id. Names are resolved
                to ids via `resolve_repository_id` since RB's
                `?repository=` filter only accepts the numeric id.
            statuses: Statuses to include, e.g. ["submitted", "discarded"].
            limit: Max number of review requests to return.
            days: If > 0, only include RRs updated within this many days.

        Returns:
            Review request dicts, newest first, capped at `limit`.
        """
        repo_id = self.resolve_repository_id(repository)
        merged: dict[int, dict] = {}
        for status in statuses:
            params: dict[str, str] = {
                "repository": repo_id,
                "status": status,
                "max-results": str(limit),
            }
            if days > 0:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
                params["last-updated-from"] = cutoff

            result = self._api_get("/api/review-requests/", params)
            for rr in result.get("review_requests", []):
                merged[rr["id"]] = rr

        ordered = sorted(
            merged.values(),
            key=lambda r: r.get("last_updated", ""),
            reverse=True,
        )
        return ordered[:limit]

    def get_review_request_info(self, review_request_id: int) -> ReviewRequestInfo:
        """Get essential review request info including depends_on for chain resolution.

        Args:
            review_request_id: The review request ID.

        Returns:
            ReviewRequestInfo with all fields needed for chain resolution.

        Raises:
            RuntimeError: If the review request cannot be fetched.
        """
        rr = self.get_review_request(review_request_id)

        # Extract depends_on IDs
        # depends_on contains objects like: {'href': '.../api/review-requests/42762/', ...}
        depends_on_ids: list[int] = []
        depends_on = rr.get("depends_on", [])
        for dep in depends_on:
            if isinstance(dep, dict):
                # Try 'id' field first
                if "id" in dep:
                    depends_on_ids.append(dep["id"])
                # Otherwise extract from href URL
                elif "href" in dep:
                    match = re.search(r"/review-requests/(\d+)/", dep["href"])
                    if match:
                        depends_on_ids.append(int(match.group(1)))
            elif isinstance(dep, int):
                depends_on_ids.append(dep)

        # Get repository name
        repo_name = "unknown"
        links = rr.get("links", {})
        if "repository" in links:
            try:
                repo_href = links["repository"]["href"]
                repo_path = repo_href.replace(self.url, "")
                repo_result = self._api_get(repo_path)
                repo_name = repo_result.get("repository", {}).get("name", "unknown")
            except Exception:
                pass

        # Get base commit and diff revision
        base_commit = self._get_base_commit(review_request_id)
        diff_revision = self._get_latest_diff_revision(review_request_id)

        return ReviewRequestInfo(
            id=review_request_id,
            summary=rr.get("summary", ""),
            status=rr.get("status", "pending"),
            repository_name=repo_name,
            depends_on=depends_on_ids,
            base_commit_id=base_commit,
            diff_revision=diff_revision,
            description=rr.get("description", ""),
        )

    def __del__(self):
        """Cleanup cookie file."""
        if self._cookie_file and self._cookie_file.exists():
            try:
                self._cookie_file.unlink()
            except Exception:
                pass


def _parse_datetime(dt_str: str | None) -> datetime | None:
    """Parse Review Board datetime string."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_status_and_body(stdout: str) -> tuple[int, str]:
    """Split curl `-w '\\n%{http_code}'` output into (status_code, body).

    Falls back to (0, full_stdout) if the trailing status code is missing
    or unparseable, matching the prior inline logic.
    """
    lines = stdout.rsplit("\n", 1)
    if len(lines) != 2:
        return 0, stdout
    body, status = lines
    try:
        return int(status.strip()), body
    except ValueError:
        return 0, stdout


def _decode_api_response(status: int, body: str, path: str) -> dict:
    """Decode an RB API JSON body, raising on upstream 5xx HTML pages.

    A 5xx without JSON means the load balancer / Apache returned an HTML
    error page (typically after `_curl` retries were exhausted). Raise so
    callers see a clear failure instead of silently treating it as an
    empty result.
    """
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        if 500 <= status < 600:
            logger.error(f"HTTP {status} from {path}: {body[:200]}")
            raise RuntimeError(f"Review Board returned HTTP {status} for {path}") from err
        logger.error(f"Invalid JSON response: {body[:200]}")
        return {"stat": "fail", "err": {"msg": f"Invalid JSON (HTTP {status})"}}

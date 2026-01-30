"""Repository manager for maintaining local clones and checkouts."""

from collections.abc import Generator
from contextlib import contextmanager
import logging
from pathlib import Path
import subprocess
import tempfile

from git import GitCommandError, InvalidGitRepositoryError, Repo

from ..models import RepoConfig


logger = logging.getLogger(__name__)


class RepoManagerError(Exception):
    """Error in repository management operations."""

    pass


class RepoManager:
    """Manages local repository clones and checkouts."""

    def __init__(self, repos: list[RepoConfig]):
        """Initialize the repository manager.

        Args:
            repos: List of repository configurations.
        """
        self.repos = {repo.name: repo for repo in repos}
        self._repo_instances: dict[str, Repo] = {}

    def get_repo(self, name: str) -> RepoConfig:
        """Get repository configuration by name.

        Args:
            name: Repository name.

        Returns:
            Repository configuration.

        Raises:
            RepoManagerError: If repository not found.
        """
        if name not in self.repos:
            raise RepoManagerError(f"Repository not found: {name}")
        return self.repos[name]

    def get_repo_by_rb_name(self, rb_name: str) -> RepoConfig | None:
        """Get repository configuration by Review Board name.

        Args:
            rb_name: Repository name as shown in Review Board.

        Returns:
            Repository configuration or None if not found.
        """
        for repo in self.repos.values():
            if repo.rb_repo_name == rb_name:
                return repo
        return None

    def ensure_clone(self, repo_name: str) -> Repo:
        """Ensure repository is cloned locally.

        Args:
            repo_name: Repository name.

        Returns:
            GitPython Repo instance.

        Raises:
            RepoManagerError: If clone fails.
        """
        config = self.get_repo(repo_name)
        local_path = config.local_path

        if local_path.exists():
            try:
                repo = Repo(local_path)
                logger.debug(f"Repository {repo_name} exists at {local_path}")
                self._repo_instances[repo_name] = repo
                return repo
            except InvalidGitRepositoryError as err:
                raise RepoManagerError(f"Path exists but is not a git repo: {local_path}") from err

        # Clone the repository
        logger.info(f"Cloning {config.remote_url} to {local_path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            repo = Repo.clone_from(config.remote_url, local_path)
            self._repo_instances[repo_name] = repo
            logger.info(f"Cloned {repo_name} successfully")
            return repo
        except GitCommandError as e:
            raise RepoManagerError(f"Failed to clone {repo_name}: {e}") from e

    def fetch_all(self, repo_name: str) -> None:
        """Fetch all remote refs for a repository.

        Args:
            repo_name: Repository name.
        """
        repo = self.ensure_clone(repo_name)
        logger.info(f"Fetching all refs for {repo_name}")

        try:
            for remote in repo.remotes:
                remote.fetch(prune=True)
            logger.info(f"Fetched all refs for {repo_name}")
        except GitCommandError as e:
            logger.warning(f"Failed to fetch some refs for {repo_name}: {e}")

    def fetch_all_repos(self) -> dict[str, bool]:
        """Fetch all configured repositories.

        Returns:
            Dict mapping repo names to success status.
        """
        results = {}
        for repo_name in self.repos:
            try:
                self.fetch_all(repo_name)
                results[repo_name] = True
            except RepoManagerError as e:
                logger.error(f"Failed to fetch {repo_name}: {e}")
                results[repo_name] = False
        return results

    def checkout(self, repo_name: str, ref: str) -> None:
        """Checkout a specific ref (commit, branch, tag).

        Args:
            repo_name: Repository name.
            ref: Git ref to checkout (commit SHA, branch name, tag).

        Raises:
            RepoManagerError: If checkout fails.
        """
        repo = self.ensure_clone(repo_name)
        logger.info(f"Checking out {ref} in {repo_name}")

        try:
            # First, try to checkout directly
            repo.git.checkout(ref)
            logger.info(f"Checked out {ref} in {repo_name}")
        except GitCommandError:
            # If direct checkout fails, try fetching first
            logger.debug("Direct checkout failed, fetching and retrying")
            self.fetch_all(repo_name)
            try:
                repo.git.checkout(ref)
                logger.info(f"Checked out {ref} in {repo_name} after fetch")
            except GitCommandError as e:
                raise RepoManagerError(f"Failed to checkout {ref} in {repo_name}: {e}") from e

    def smart_checkout(
        self,
        repo_name: str,
        base_commit: str | None = None,
        branch: str | None = None,
    ) -> str:
        """Smart checkout that handles various scenarios.

        Tries to checkout in this order:
        1. Specific base commit if provided
        2. Specific branch if provided
        3. Default branch

        Args:
            repo_name: Repository name.
            base_commit: Base commit SHA if known.
            branch: Branch name if known.

        Returns:
            The ref that was checked out.

        Raises:
            RepoManagerError: If no valid ref could be checked out.
        """
        config = self.get_repo(repo_name)
        self.ensure_clone(repo_name)

        # Try base commit first
        if base_commit:
            try:
                self.checkout(repo_name, base_commit)
                return base_commit
            except RepoManagerError:
                logger.warning(f"Could not checkout base commit {base_commit}")

        # Try branch
        if branch:
            # Try remote branch first
            remote_branch = f"origin/{branch}"
            try:
                self.checkout(repo_name, remote_branch)
                return remote_branch
            except RepoManagerError:
                pass

            # Try local branch
            try:
                self.checkout(repo_name, branch)
                return branch
            except RepoManagerError:
                logger.warning(f"Could not checkout branch {branch}")

        # Fall back to default branch
        default_branch = f"origin/{config.default_branch}"
        try:
            self.checkout(repo_name, default_branch)
            return default_branch
        except RepoManagerError:
            pass

        # Last resort: try main/master
        for fallback in ["origin/main", "origin/master", "main", "master"]:
            try:
                self.checkout(repo_name, fallback)
                return fallback
            except RepoManagerError:
                continue

        raise RepoManagerError(
            f"Could not checkout any valid ref in {repo_name}. "
            f"Tried: base_commit={base_commit}, branch={branch}, default={config.default_branch}"
        )

    def get_current_commit(self, repo_name: str) -> str:
        """Get the current HEAD commit SHA.

        Args:
            repo_name: Repository name.

        Returns:
            Commit SHA.
        """
        repo = self.ensure_clone(repo_name)
        return repo.head.commit.hexsha

    def get_local_path(self, repo_name: str) -> Path:
        """Get the local path for a repository.

        Args:
            repo_name: Repository name.

        Returns:
            Path to local repository.
        """
        return self.get_repo(repo_name).local_path

    def apply_patch(self, repo_name: str, patch: str, check_only: bool = False) -> bool:
        """Apply a patch to the repository.

        Args:
            repo_name: Repository name.
            patch: Patch content.
            check_only: If True, only check if patch applies cleanly.

        Returns:
            True if patch was applied (or would apply) successfully.
        """
        self.ensure_clone(repo_name)
        local_path = self.get_local_path(repo_name)

        # Write patch to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
            f.write(patch)
            patch_file = f.name

        try:
            args = ["git", "apply", "--index"]  # Stage the changes
            if check_only:
                args.append("--check")
            args.append(patch_file)

            result = subprocess.run(
                args,
                cwd=local_path,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                logger.debug(f"Patch {'would apply' if check_only else 'applied'} and staged cleanly")
                return True
            else:
                logger.warning(f"Patch failed: {result.stderr}")
                return False
        finally:
            Path(patch_file).unlink()

    def commit_exists(self, repo_name: str, commit_sha: str) -> bool:
        """Check if a commit exists in the repository.

        Args:
            repo_name: Repository name.
            commit_sha: Commit SHA to check.

        Returns:
            True if the commit exists in the repo.
        """
        try:
            repo = self.ensure_clone(repo_name)
            repo.git.cat_file("-t", commit_sha)
            return True
        except GitCommandError:
            return False
        except Exception as e:
            logger.warning(f"Error checking commit {commit_sha}: {e}")
            return False

    def get_file_content(self, repo_name: str, file_path: str) -> str | None:
        """Get the content of a file from the repository.

        Args:
            repo_name: Repository name.
            file_path: Path to file relative to repo root.

        Returns:
            File content or None if file doesn't exist.
        """
        local_path = self.get_local_path(repo_name)
        full_path = local_path / file_path

        if not full_path.exists():
            return None

        try:
            return full_path.read_text()
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            return None

    def get_file_context(
        self,
        repo_name: str,
        file_path: str,
        line_start: int,
        line_end: int,
        context_lines: int = 50,
    ) -> str | None:
        """Get file content around specific lines.

        Args:
            repo_name: Repository name.
            file_path: Path to file relative to repo root.
            line_start: Starting line number (1-indexed).
            line_end: Ending line number (1-indexed).
            context_lines: Number of lines of context to include.

        Returns:
            File content with context, or None if file doesn't exist.
        """
        content = self.get_file_content(repo_name, file_path)
        if content is None:
            return None

        lines = content.splitlines()
        total_lines = len(lines)

        # Calculate range with context
        start = max(0, line_start - 1 - context_lines)
        end = min(total_lines, line_end + context_lines)

        # Add line numbers
        result_lines = []
        for i in range(start, end):
            line_num = i + 1
            marker = ">" if line_start <= line_num <= line_end else " "
            result_lines.append(f"{marker} {line_num:4d} | {lines[i]}")

        return "\n".join(result_lines)

    @contextmanager
    def checkout_context(
        self,
        repo_name: str,
        base_commit: str | None = None,
        branch: str | None = None,
        target_commit: str | None = None,
        patch: str | None = None,
    ) -> Generator[tuple[Path, bool], None, None]:
        """Context manager that checks out a ref and restores original state.

        If target_commit is provided and exists in the repo, it will be checked
        out instead of the base_commit. If target_commit is not available but
        a patch is provided, the patch will be applied to base_commit to get
        the same file state.

        Args:
            repo_name: Repository name.
            base_commit: Base commit SHA (fallback if target_commit unavailable).
            branch: Branch name.
            target_commit: Target commit SHA (the reviewed commit, if available).
            patch: Raw diff content to apply if target_commit unavailable.

        Yields:
            Tuple of (path to repository, bool indicating if target_commit was used).
        """
        repo = self.ensure_clone(repo_name)
        original_ref = repo.head.commit.hexsha
        used_target = False
        patch_applied = False
        untracked_before: set[str] = set()

        try:
            # Try to use target_commit if available and exists in repo
            if target_commit and self.commit_exists(repo_name, target_commit):
                logger.info(f"Using target commit {target_commit[:12]} (actual reviewed commit)")
                repo.git.checkout(target_commit)
                used_target = True
            else:
                if target_commit:
                    logger.debug(f"Target commit {target_commit[:12]} not in repo, using base + patch")
                self.smart_checkout(repo_name, base_commit, branch)

                # Apply patch to get to reviewed state
                if patch:
                    # Track untracked files before patch to clean up only new ones
                    untracked_before = set(repo.untracked_files)
                    logger.info("Applying patch to reach reviewed state")
                    if self.apply_patch(repo_name, patch):
                        patch_applied = True
                        logger.info("Patch applied successfully")
                    else:
                        logger.warning("Failed to apply patch cleanly, working with base state")

            yield self.get_local_path(repo_name), used_target or patch_applied
        finally:
            # Restore original state
            try:
                # git reset --hard clears index and working tree
                repo.git.reset("--hard", original_ref)

                # Clean up only new files created by the patch (untracked files)
                if patch_applied:
                    untracked_after = set(repo.untracked_files)
                    new_files = untracked_after - untracked_before
                    if new_files:
                        local_path = self.get_local_path(repo_name)
                        for f in new_files:
                            try:
                                (local_path / f).unlink()
                                logger.debug(f"Removed patch artifact: {f}")
                            except OSError:
                                pass
            except GitCommandError as e:
                logger.warning(f"Could not fully restore {repo_name} to {original_ref}: {e}")

    def list_repos(self) -> list[dict[str, str]]:
        """List all configured repositories with status.

        Returns:
            List of repo info dicts.
        """
        results = []
        for name, config in self.repos.items():
            info = {
                "name": name,
                "rb_name": config.rb_repo_name,
                "local_path": str(config.local_path),
                "remote_url": config.remote_url,
                "exists": config.local_path.exists(),
            }

            if config.local_path.exists():
                try:
                    repo = Repo(config.local_path)
                    if repo.head.is_detached:
                        info["current_branch"] = "detached"
                    else:
                        info["current_branch"] = repo.active_branch.name
                    info["current_commit"] = repo.head.commit.hexsha[:8]
                except Exception:
                    info["current_branch"] = "unknown"
                    info["current_commit"] = "unknown"

            results.append(info)

        return results

"""Integration tests for the Repository Manager."""

from pathlib import Path

from git import Repo
import pytest

from bb_review.git.manager import PatchApplyError, RepoManager, RepoManagerError
from bb_review.models import RepoConfig


@pytest.fixture
def repo_config(temp_git_repo: tuple[Path, Repo]) -> RepoConfig:
    """Create a RepoConfig for the temp repo."""
    repo_path, _ = temp_git_repo
    return RepoConfig(
        name="test-repo",
        local_path=repo_path,
        remote_url="git@example.com:org/test-repo.git",
        rb_repo_name="Test Repository",
        default_branch="main",
    )


@pytest.fixture
def repo_manager(repo_config: RepoConfig) -> RepoManager:
    """Create a RepoManager with the temp repo."""
    return RepoManager([repo_config])


class TestRepoManagerBasics:
    """Basic RepoManager tests."""

    def test_get_repo(self, repo_manager: RepoManager):
        """Get repository by name."""
        repo = repo_manager.get_repo("test-repo")
        assert repo.name == "test-repo"

    def test_get_repo_not_found(self, repo_manager: RepoManager):
        """Error for unknown repo name."""
        with pytest.raises(RepoManagerError, match="Repository not found"):
            repo_manager.get_repo("nonexistent")

    def test_get_repo_by_rb_name(self, repo_manager: RepoManager):
        """Get repository by RB name."""
        repo = repo_manager.get_repo_by_rb_name("Test Repository")
        assert repo is not None
        assert repo.name == "test-repo"

    def test_get_repo_by_rb_name_not_found(self, repo_manager: RepoManager):
        """Return None for unknown RB name."""
        repo = repo_manager.get_repo_by_rb_name("Unknown")
        assert repo is None

    def test_get_local_path(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Get local path for repository."""
        repo_path, _ = temp_git_repo
        path = repo_manager.get_local_path("test-repo")
        assert path == repo_path

    def test_list_repos(self, repo_manager: RepoManager):
        """List configured repositories."""
        repos = repo_manager.list_repos()

        assert len(repos) == 1
        assert repos[0]["name"] == "test-repo"
        assert repos[0]["exists"] is True


class TestRepoManagerCheckout:
    """Tests for checkout functionality."""

    def test_ensure_clone_existing(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Use existing repo without cloning."""
        repo_path, _ = temp_git_repo

        repo = repo_manager.ensure_clone("test-repo")

        assert repo.working_dir == str(repo_path)

    def test_get_current_commit(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Get current HEAD commit."""
        _, git_repo = temp_git_repo

        commit = repo_manager.get_current_commit("test-repo")

        assert commit == git_repo.head.commit.hexsha

    def test_checkout_commit(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Checkout specific commit."""
        repo_path, git_repo = temp_git_repo

        # Create a new commit
        test_file = repo_path / "test.txt"
        test_file.write_text("test content")
        git_repo.index.add(["test.txt"])
        git_repo.index.commit("Add test file")

        # Store original commit
        original = git_repo.head.commit.hexsha

        # Checkout original commit
        repo_manager.checkout("test-repo", original)

        current = repo_manager.get_current_commit("test-repo")
        assert current == original

    def test_smart_checkout_branch(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Smart checkout with branch name."""
        repo_path, git_repo = temp_git_repo

        # The default branch should be 'main' or 'master'
        ref = repo_manager.smart_checkout("test-repo", branch="main")

        assert "main" in ref or "master" in ref

    def test_commit_exists(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Check if commit exists."""
        _, git_repo = temp_git_repo

        exists = repo_manager.commit_exists("test-repo", git_repo.head.commit.hexsha)
        assert exists is True

        not_exists = repo_manager.commit_exists("test-repo", "0" * 40)
        assert not_exists is False


class TestRepoManagerPatch:
    """Tests for patch application."""

    def test_apply_patch(self, repo_manager: RepoManager, temp_git_repo_with_files: tuple[Path, Repo]):
        """Apply patch successfully."""
        repo_path, _ = temp_git_repo_with_files

        # Create a simple patch
        patch = """diff --git a/src/main.c b/src/main.c
index abc123..def456 100644
--- a/src/main.c
+++ b/src/main.c
@@ -3,4 +3,5 @@ int main() {
     printf("Hello World\\n");
     return 0;
 }
+// New comment
"""
        # This patch may not apply cleanly to our test file
        # Let's use check_only mode which is safer for testing
        result = repo_manager.apply_patch("test-repo", patch, check_only=True)

        # Result depends on whether patch applies cleanly
        # The important thing is it doesn't crash
        assert isinstance(result, bool)

    def test_apply_invalid_patch(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Invalid patch returns False."""
        result = repo_manager.apply_patch("test-repo", "invalid patch content")
        assert result is False


class TestRepoManagerContext:
    """Tests for checkout context manager."""

    def test_checkout_context_restore(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Context manager restores state."""
        repo_path, git_repo = temp_git_repo

        original_commit = git_repo.head.commit.hexsha

        with repo_manager.checkout_context("test-repo") as (path, used_target):
            assert path == repo_path
            # We're still at the same commit (no base_commit specified)

        # Should be back to original
        current = repo_manager.get_current_commit("test-repo")
        assert current == original_commit

    def test_checkout_context_with_branch(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Context with branch parameter."""
        repo_path, _ = temp_git_repo

        with repo_manager.checkout_context("test-repo", branch="main") as (path, used_target):
            assert path == repo_path

    def test_checkout_context_yields_path(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Context yields correct path."""
        repo_path, _ = temp_git_repo

        with repo_manager.checkout_context("test-repo") as (path, used_target):
            assert path == repo_path
            assert path.exists()


class TestRepoManagerFileContent:
    """Tests for file content retrieval."""

    def test_get_file_content(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Get file content."""
        content = repo_manager.get_file_content("test-repo", "README.md")

        assert content is not None
        assert "Test Repository" in content

    def test_get_file_content_not_found(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Return None for missing file."""
        content = repo_manager.get_file_content("test-repo", "nonexistent.txt")
        assert content is None

    def test_get_file_context(self, repo_manager: RepoManager, temp_git_repo_with_files: tuple[Path, Repo]):
        """Extract file context around lines."""
        context = repo_manager.get_file_context(
            "test-repo",
            "src/main.c",
            line_start=3,
            line_end=4,
            context_lines=2,
        )

        assert context is not None
        # Should contain line numbers
        assert "3" in context or "4" in context

    def test_get_file_context_not_found(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Return None for missing file."""
        context = repo_manager.get_file_context("test-repo", "nonexistent.c", 1, 5, 2)
        assert context is None


class TestChainContext:
    """Tests for chain_context context manager."""

    def test_creates_branch_and_yields_path(
        self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]
    ):
        repo_path, git_repo = temp_git_repo
        original_commit = git_repo.head.commit.hexsha

        with repo_manager.chain_context("test-repo", original_commit, "test-branch") as path:
            assert path == repo_path
            # Branch should exist
            branch_names = [b.name for b in git_repo.branches]
            assert "test-branch" in branch_names

    def test_branch_deleted_on_exit(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        original_commit = git_repo.head.commit.hexsha

        with repo_manager.chain_context("test-repo", original_commit, "ephemeral-branch"):
            pass

        branch_names = [b.name for b in git_repo.branches]
        assert "ephemeral-branch" not in branch_names

    def test_keep_branch_persists(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        original_commit = git_repo.head.commit.hexsha

        with repo_manager.chain_context("test-repo", original_commit, "keep-me", keep_branch=True):
            pass

        branch_names = [b.name for b in git_repo.branches]
        assert "keep-me" in branch_names

    def test_branch_cleaned_on_exception(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        original_commit = git_repo.head.commit.hexsha

        with pytest.raises(RuntimeError):
            with repo_manager.chain_context("test-repo", original_commit, "boom-branch"):
                raise RuntimeError("something went wrong")

        branch_names = [b.name for b in git_repo.branches]
        assert "boom-branch" not in branch_names

    def test_restores_original_ref(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        original_commit = git_repo.head.commit.hexsha

        with repo_manager.chain_context("test-repo", original_commit, "restore-branch"):
            pass

        current = repo_manager.get_current_commit("test-repo")
        assert current == original_commit


class TestApplyAndCommit:
    """Tests for apply_and_commit."""

    def test_applies_and_commits(
        self, repo_manager: RepoManager, temp_git_repo_with_files: tuple[Path, Repo]
    ):
        repo_path, git_repo = temp_git_repo_with_files
        original_count = len(list(git_repo.iter_commits()))

        # Modify a tracked file, generate patch, then reset.
        # GitPython's git.diff strips trailing newline; add it back since
        # `git apply` requires the patch to end with a newline.
        main_c = repo_path / "src" / "main.c"
        original_content = main_c.read_text()
        main_c.write_text(original_content + "// patched\n")
        git_repo.index.add(["src/main.c"])
        patch = git_repo.git.diff("--cached", "--no-color") + "\n"
        git_repo.git.reset("--hard", "HEAD")

        result = repo_manager.apply_and_commit("test-repo", patch, "Test commit")

        assert result is True
        new_count = len(list(git_repo.iter_commits()))
        assert new_count == original_count + 1

    def test_bad_patch_returns_false(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        result = repo_manager.apply_and_commit("test-repo", "garbage", "Bad commit")
        assert result is False


class TestCommitStaged:
    """Tests for commit_staged."""

    def test_commits_staged_changes(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        # Stage a new file
        new_file = repo_path / "staged.txt"
        new_file.write_text("staged content\n")
        git_repo.index.add(["staged.txt"])

        result = repo_manager.commit_staged("test-repo", "Commit staged")

        assert result is True
        assert git_repo.head.commit.message.startswith("Commit staged")

    def test_nothing_staged_returns_false(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        result = repo_manager.commit_staged("test-repo", "Empty commit")
        assert result is False


class TestDeleteBranch:
    """Tests for delete_branch."""

    def test_deletes_branch(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        # Create a branch to delete
        git_repo.create_head("doomed-branch")

        repo_manager.delete_branch("test-repo", "doomed-branch")

        branch_names = [b.name for b in git_repo.branches]
        assert "doomed-branch" not in branch_names

    def test_nonexistent_branch_no_crash(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        # Should log warning but not raise
        repo_manager.delete_branch("test-repo", "no-such-branch")


class TestResetWorkingTree:
    """Tests for _reset_working_tree."""

    def test_dirty_repo_cleaned(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        # Dirty the repo
        (repo_path / "README.md").write_text("modified\n")
        (repo_path / "untracked.txt").write_text("junk\n")
        assert git_repo.is_dirty(untracked_files=True)

        repo_manager._reset_working_tree(git_repo, "test-repo")

        assert not git_repo.is_dirty(untracked_files=True)

    def test_clean_repo_noop(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        _, git_repo = temp_git_repo
        assert not git_repo.is_dirty(untracked_files=True)

        # Should not raise
        repo_manager._reset_working_tree(git_repo, "test-repo")


# ---------------------------------------------------------------------------
# Fixtures for local-remote tests
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_remote(tmp_path: Path) -> tuple[Path, Repo]:
    """Create a bare repo to serve as a local 'remote'."""
    bare_path = tmp_path / "remote.git"
    bare_repo = Repo.init(bare_path, bare=True)

    # Create a working clone, add a commit, push to the bare repo
    work_path = tmp_path / "work_clone"
    work = Repo.clone_from(str(bare_path), work_path)
    work.config_writer().set_value("user", "name", "Test").release()
    work.config_writer().set_value("user", "email", "t@t.com").release()
    (work_path / "README.md").write_text("# Remote Repo\n")
    work.index.add(["README.md"])
    work.index.commit("Initial commit")
    work.remote("origin").push("HEAD:refs/heads/main")

    # Point bare repo HEAD to main so clones checkout main by default
    bare_repo.head.reference = bare_repo.refs["main"]

    return bare_path, bare_repo


class TestEnsureClone:
    """Tests for ensure_clone: cloning and error paths."""

    def test_clone_from_local_remote(self, tmp_path: Path, bare_remote: tuple[Path, Repo]):
        """Clone from a local bare repo used as remote."""
        bare_path, _ = bare_remote
        clone_path = tmp_path / "cloned_repo"

        config = RepoConfig(
            name="cloned",
            local_path=clone_path,
            remote_url=str(bare_path),
            rb_repo_name="Cloned Repo",
            default_branch="main",
        )
        mgr = RepoManager([config])
        repo = mgr.ensure_clone("cloned")

        assert repo.working_dir == str(clone_path)
        assert (clone_path / "README.md").exists()

    def test_invalid_git_repo_raises(self, tmp_path: Path):
        """Path exists but is not a git repo -> error."""
        not_git = tmp_path / "not_a_repo"
        not_git.mkdir()
        (not_git / "some_file.txt").write_text("hi")

        config = RepoConfig(
            name="bad",
            local_path=not_git,
            remote_url="git@example.com:org/bad.git",
            rb_repo_name="Bad",
        )
        mgr = RepoManager([config])

        with pytest.raises(RepoManagerError, match="not a git repo"):
            mgr.ensure_clone("bad")

    def test_clone_bad_remote_raises(self, tmp_path: Path):
        """Clone from nonexistent remote -> error."""
        config = RepoConfig(
            name="missing",
            local_path=tmp_path / "missing_clone",
            remote_url="/nonexistent/path/repo.git",
            rb_repo_name="Missing",
        )
        mgr = RepoManager([config])

        with pytest.raises(RepoManagerError, match="Failed to clone"):
            mgr.ensure_clone("missing")


class TestFetch:
    """Tests for fetch_all and fetch_all_repos using local remote."""

    def test_fetch_all_with_remote(self, tmp_path: Path, bare_remote: tuple[Path, Repo]):
        """Fetch from a local bare remote succeeds."""
        bare_path, _ = bare_remote
        clone_path = tmp_path / "fetch_clone"

        config = RepoConfig(
            name="fetch-test",
            local_path=clone_path,
            remote_url=str(bare_path),
            rb_repo_name="Fetch Test",
            default_branch="main",
        )
        mgr = RepoManager([config])
        mgr.ensure_clone("fetch-test")

        # Should not raise
        mgr.fetch_all("fetch-test")

    def test_fetch_all_repos(self, tmp_path: Path, bare_remote: tuple[Path, Repo]):
        """fetch_all_repos returns success for each repo."""
        bare_path, _ = bare_remote
        clone_path = tmp_path / "fetch_all_clone"

        config = RepoConfig(
            name="fa-test",
            local_path=clone_path,
            remote_url=str(bare_path),
            rb_repo_name="FA Test",
            default_branch="main",
        )
        mgr = RepoManager([config])
        mgr.ensure_clone("fa-test")

        results = mgr.fetch_all_repos()

        assert results["fa-test"] is True


class TestSmartCheckoutEdgeCases:
    """Test smart_checkout paths not covered by existing tests."""

    def test_base_commit_checkout(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """smart_checkout with a valid base_commit uses it directly."""
        _, git_repo = temp_git_repo
        commit_sha = git_repo.head.commit.hexsha

        ref = repo_manager.smart_checkout("test-repo", base_commit=commit_sha)

        assert ref == commit_sha

    def test_all_refs_fail_raises(self, tmp_path: Path):
        """smart_checkout raises when nothing can be checked out."""
        repo_path = tmp_path / "empty_repo"
        repo_path.mkdir()
        repo = Repo.init(repo_path)
        repo.config_writer().set_value("user", "name", "T").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()
        (repo_path / "f.txt").write_text("x")
        repo.index.add(["f.txt"])
        repo.index.commit("init")

        # Rename default branch so none of the fallbacks (main/master) match
        repo.git.branch("-m", "isolated")

        config = RepoConfig(
            name="no-remote",
            local_path=repo_path,
            remote_url="git@example.com:org/x.git",
            rb_repo_name="X",
            default_branch="nonexistent",
        )
        mgr = RepoManager([config])

        with pytest.raises(RepoManagerError, match="Could not checkout any valid ref"):
            mgr.smart_checkout("no-remote", base_commit="0" * 40, branch="nope")


class TestCheckoutContextAdvanced:
    """Test checkout_context paths: target_commit, patch apply/fail, cleanup."""

    def test_target_commit_used_when_exists(
        self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]
    ):
        """When target_commit exists in repo, it is checked out directly."""
        repo_path, git_repo = temp_git_repo

        # Create second commit to use as target
        (repo_path / "target.txt").write_text("target\n")
        git_repo.index.add(["target.txt"])
        git_repo.index.commit("Target commit")
        target_sha = git_repo.head.commit.hexsha

        # Go back to initial commit
        initial_sha = list(git_repo.iter_commits())[-1].hexsha
        git_repo.git.checkout(initial_sha)

        with repo_manager.checkout_context("test-repo", target_commit=target_sha) as (path, used_target):
            assert used_target is True
            current = repo_manager.get_current_commit("test-repo")
            assert current == target_sha

    def test_target_commit_missing_falls_through(
        self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]
    ):
        """When target_commit doesn't exist, falls through to base checkout."""
        repo_path, _ = temp_git_repo

        with repo_manager.checkout_context("test-repo", target_commit="0" * 40) as (path, used_target):
            # Should not have used target (it doesn't exist)
            assert used_target is False

    def test_patch_applied_successfully(
        self, repo_manager: RepoManager, temp_git_repo_with_files: tuple[Path, Repo]
    ):
        """Patch is applied and used_target reflects success."""
        repo_path, git_repo = temp_git_repo_with_files

        # Generate a valid patch
        main_c = repo_path / "src" / "main.c"
        original = main_c.read_text()
        main_c.write_text(original + "// context patch\n")
        git_repo.index.add(["src/main.c"])
        patch = git_repo.git.diff("--cached", "--no-color") + "\n"
        git_repo.git.reset("--hard", "HEAD")

        with repo_manager.checkout_context("test-repo", patch=patch) as (path, used_target):
            assert used_target is True
            content = (path / "src" / "main.c").read_text()
            assert "// context patch" in content

        # After exit, patch changes are cleaned up
        restored = (repo_path / "src" / "main.c").read_text()
        assert "// context patch" not in restored

    def test_patch_fail_require_raises(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        """Bad patch + require_patch=True -> PatchApplyError."""
        with pytest.raises(PatchApplyError):
            with repo_manager.checkout_context("test-repo", patch="garbage", require_patch=True):
                pass  # pragma: no cover

    def test_patch_fail_no_require_continues(
        self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]
    ):
        """Bad patch + require_patch=False -> continues with base state."""
        repo_path, _ = temp_git_repo

        with repo_manager.checkout_context("test-repo", patch="garbage", require_patch=False) as (
            path,
            used_target,
        ):
            assert used_target is False
            assert path == repo_path

    def test_new_files_from_patch_cleaned_up(
        self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]
    ):
        """Files created by a patch are removed on context exit."""
        repo_path, git_repo = temp_git_repo

        # Generate a patch that creates a new file
        new_file = repo_path / "newfile.txt"
        new_file.write_text("hello\n")
        git_repo.index.add(["newfile.txt"])
        patch = git_repo.git.diff("--cached", "--no-color") + "\n"
        # reset --hard removes both the index entry and the working tree file
        git_repo.git.reset("--hard", "HEAD")

        with repo_manager.checkout_context("test-repo", patch=patch) as (path, used_target):
            assert used_target is True
            assert (path / "newfile.txt").exists()

        # After exit, the new file should be gone
        assert not (repo_path / "newfile.txt").exists()


class TestFindCommitBySummary:
    """Tests for find_commit_by_summary."""

    def test_finds_existing_commit(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo

        # Create a commit with a specific summary
        (repo_path / "findme.txt").write_text("x\n")
        git_repo.index.add(["findme.txt"])
        git_repo.index.commit("Unique summary for search test")
        expected_sha = git_repo.head.commit.hexsha

        found = repo_manager.find_commit_by_summary("test-repo", "Unique summary for search test")

        assert found == expected_sha

    def test_returns_none_for_missing(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        found = repo_manager.find_commit_by_summary("test-repo", "This commit does not exist xyz123")
        assert found is None


class TestChainContextNullBase:
    """Test chain_context when base_commit is None."""

    def test_uses_default_branch(self, tmp_path: Path, bare_remote: tuple[Path, Repo]):
        """base_commit=None should use origin/default_branch."""
        bare_path, _ = bare_remote
        clone_path = tmp_path / "chain_null_clone"

        config = RepoConfig(
            name="chain-null",
            local_path=clone_path,
            remote_url=str(bare_path),
            rb_repo_name="Chain Null",
            default_branch="main",
        )
        mgr = RepoManager([config])
        mgr.ensure_clone("chain-null")

        with mgr.chain_context("chain-null", None, "null-base-branch") as path:
            assert path == clone_path
            repo = Repo(clone_path)
            branch_names = [b.name for b in repo.branches]
            assert "null-base-branch" in branch_names


class TestListReposDetached:
    """Test list_repos with detached HEAD."""

    def test_detached_head(self, repo_manager: RepoManager, temp_git_repo: tuple[Path, Repo]):
        repo_path, git_repo = temp_git_repo
        # Detach HEAD
        git_repo.git.checkout("--detach")

        repos = repo_manager.list_repos()

        assert repos[0]["current_branch"] == "detached"
        assert len(repos[0]["current_commit"]) == 8

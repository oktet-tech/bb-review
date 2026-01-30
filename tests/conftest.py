"""Shared test fixtures for BB Review."""

from collections.abc import Generator
import json
from pathlib import Path

from click.testing import CliRunner
from git import Repo
import pytest

from .mocks import MockLLMProvider, MockRBClient
from .mocks.rb_client import MockDiffInfo


# Path to test data directory
TEST_DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI runner for testing commands."""
    return CliRunner()


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    """Create a mock LLM provider with default (empty) response."""
    return MockLLMProvider()


@pytest.fixture
def mock_llm_with_issues() -> MockLLMProvider:
    """Create a mock LLM provider that returns issues."""
    from .mocks.llm_provider import MockLLMProviderWithIssues

    return MockLLMProviderWithIssues()


@pytest.fixture
def mock_rb_client() -> MockRBClient:
    """Create a mock ReviewBoard client."""
    return MockRBClient()


@pytest.fixture
def sample_diff() -> str:
    """Load sample diff from test data."""
    return (TEST_DATA_DIR / "sample_diff.patch").read_text()


@pytest.fixture
def sample_response() -> dict:
    """Load sample LLM response from test data."""
    return json.loads((TEST_DATA_DIR / "sample_response.json").read_text())


@pytest.fixture
def sample_opencode_output() -> str:
    """Load sample OpenCode output from test data."""
    return (TEST_DATA_DIR / "sample_opencode_output.txt").read_text()


@pytest.fixture
def valid_config_path() -> Path:
    """Path to valid test config."""
    return TEST_DATA_DIR / "config_valid.yaml"


@pytest.fixture
def invalid_config_path() -> Path:
    """Path to invalid test config."""
    return TEST_DATA_DIR / "config_invalid.yaml"


@pytest.fixture
def env_config_path() -> Path:
    """Path to config with environment variable references."""
    return TEST_DATA_DIR / "config_with_env.yaml"


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Generator[tuple[Path, Repo], None, None]:
    """Create a temporary git repository for testing.

    Yields:
        Tuple of (repo_path, Repo instance).
    """
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize repo
    repo = Repo.init(repo_path)

    # Configure git user for commits
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Create initial file and commit
    readme = repo_path / "README.md"
    readme.write_text("# Test Repository\n\nThis is a test.\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")

    yield repo_path, repo

    # Cleanup is handled by tmp_path fixture


@pytest.fixture
def temp_git_repo_with_files(temp_git_repo: tuple[Path, Repo]) -> tuple[Path, Repo]:
    """Create a temp git repo with multiple files for testing.

    Returns:
        Tuple of (repo_path, Repo instance).
    """
    repo_path, repo = temp_git_repo

    # Create src directory with files
    src_dir = repo_path / "src"
    src_dir.mkdir()

    main_c = src_dir / "main.c"
    main_c.write_text("""#include <stdio.h>

int main() {
    printf("Hello World\\n");
    return 0;
}
""")

    utils_h = src_dir / "utils.h"
    utils_h.write_text("""#ifndef UTILS_H
#define UTILS_H

int add(int a, int b);

#endif
""")

    repo.index.add(["src/main.c", "src/utils.h"])
    repo.index.commit("Add source files")

    return repo_path, repo


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Create a temporary valid config file.

    Returns:
        Path to the config file.
    """
    config_content = (TEST_DATA_DIR / "config_valid.yaml").read_text()

    # Update paths to use tmp_path
    config_content = config_content.replace("/tmp/test-repo", str(tmp_path / "repo"))
    config_content = config_content.replace("/tmp/test-state.db", str(tmp_path / "state.db"))
    config_content = config_content.replace("/tmp/test.log", str(tmp_path / "test.log"))

    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    return config_path


@pytest.fixture
def mock_rb_with_review(sample_diff: str) -> MockRBClient:
    """Create a mock RB client with a pre-configured review."""
    return MockRBClient(
        reviews={
            42738: {
                "id": 42738,
                "summary": "Add new feature",
                "description": "This adds a new feature to the codebase.",
                "branch": "feature-branch",
                "submitter": {"username": "developer"},
                "links": {
                    "repository": {"href": "/api/repositories/1/"},
                },
            },
        },
        diffs={
            42738: MockDiffInfo(
                diff_revision=1,
                base_commit_id="abc123",
                target_commit_id=None,
                raw_diff=sample_diff,
                files=[
                    {"id": 1, "source_file": "src/main.c", "dest_file": "src/main.c"},
                    {"id": 2, "source_file": "/dev/null", "dest_file": "src/utils.c"},
                ],
            ),
        },
        repositories={
            42738: {
                "id": 1,
                "name": "test-repo",
                "path": "/path/to/repo",
                "tool": "Git",
            },
        },
    )


@pytest.fixture
def env_vars_for_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set environment variables needed for config_with_env.yaml."""
    monkeypatch.setenv("TEST_RB_TOKEN", "env-rb-token-value")
    monkeypatch.setenv("TEST_LLM_KEY", "env-llm-key-value")


@pytest.fixture
def isolated_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Change to an isolated temporary directory.

    Returns:
        Path to the temporary directory.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def sample_review_json(tmp_path: Path) -> Path:
    """Create a sample review JSON file for submit tests.

    Returns:
        Path to the JSON file.
    """
    review_data = {
        "review_request_id": 42738,
        "body_top": "**AI Review Complete**\n\nNo issues found.",
        "comments": [
            {
                "file_path": "src/main.c",
                "line_number": 12,
                "text": "Consider adding a comment here.",
            },
        ],
        "ship_it": False,
    }

    json_path = tmp_path / "review.json"
    json_path.write_text(json.dumps(review_data, indent=2))

    return json_path


# Markers for slow tests
def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")

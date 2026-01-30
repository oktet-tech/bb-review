# BB Review Test Suite

This directory contains the test suite for bb-review. Tests are designed to run
without external dependencies (no LLM API calls, no ReviewBoard server).

## Directory Structure

```text
tests/
├── conftest.py              # Shared pytest fixtures
├── mocks/                   # Mock implementations
│   ├── llm_provider.py      # Mock LLM providers
│   └── rb_client.py         # Mock ReviewBoard client
├── data/                    # Test data files
│   ├── sample_diff.patch    # Sample unified diff
│   ├── sample_response.json # Sample LLM JSON response
│   ├── sample_opencode_output.txt
│   ├── config_valid.yaml    # Valid configuration
│   ├── config_invalid.yaml  # Invalid configuration (for error tests)
│   └── config_with_env.yaml # Config with ${VAR} references
├── unit/                    # Unit tests (pure functions)
│   ├── test_config.py       # Config loading, validation, env vars
│   ├── test_crypto.py       # Encrypt/decrypt functions
│   ├── test_url_parsing.py  # Review ID/URL parsing
│   ├── test_llm_parsing.py  # LLM response parsing, prompt building
│   ├── test_opencode_parsing.py  # OpenCode output parsing
│   ├── test_diff_utils.py   # Diff extraction, path filtering
│   └── test_guidelines.py   # Guidelines loading, validation
├── integration/             # Integration tests (multi-component)
│   ├── test_analyzer.py     # Full analysis pipeline with mock LLM
│   ├── test_repo_manager.py # Git operations with temp repos
│   └── test_providers.py    # LLM provider factory
└── cli/                     # CLI command tests
    ├── test_init.py         # init command
    ├── test_encrypt_password.py  # encrypt-password command
    ├── test_analyze.py      # analyze command
    ├── test_submit.py       # submit command
    ├── test_repos.py        # repos subcommands
    └── test_poll.py         # poll subcommands
```

## Mock Implementations

### MockLLMProvider

Replaces real LLM API calls. Returns configurable JSON responses.

```python
from tests.mocks import MockLLMProvider

# Default: returns empty review (no issues)
mock = MockLLMProvider()

# Custom response
mock = MockLLMProvider({
    "summary": "Found 1 issue",
    "has_critical_issues": False,
    "comments": [{"file_path": "test.c", "line_number": 10, ...}]
})

# Access call history
mock.get_call_count()  # Number of calls
mock.get_last_call()   # {"system": "...", "user": "..."}
```

**Variants:**

- `MockLLMProviderWithIssues` - Returns response with sample issues
- `MockLLMProviderWithCritical` - Returns response with critical issues
- `MockLLMProviderError` - Raises exception on `complete()`

### MockRBClient

Replaces ReviewBoard API client. No network calls.

```python
from tests.mocks import MockRBClient
from tests.mocks.rb_client import MockDiffInfo

# Configure with test data
mock = MockRBClient(
    reviews={42738: {"id": 42738, "summary": "Test"}},
    diffs={42738: MockDiffInfo(raw_diff="...", base_commit_id="abc123")},
    repositories={42738: {"name": "test-repo"}}
)

# Track posted reviews
mock.post_review(42738, "Body", comments=[...])
assert len(mock.posted_reviews) == 1
```

**Variants:**

- `MockRBClientError` - Raises exception on `connect()`
- `MockRBClientAuthError` - Raises auth error on `connect()`

## Key Fixtures

Defined in `conftest.py`, available to all tests:

| Fixture | Description |
|---------|-------------|
| `cli_runner` | Click's `CliRunner` for CLI tests |
| `mock_llm` | `MockLLMProvider` with default response |
| `mock_llm_with_issues` | `MockLLMProvider` returning issues |
| `mock_rb_client` | Empty `MockRBClient` |
| `mock_rb_with_review` | `MockRBClient` with pre-configured review #42738 |
| `sample_diff` | Contents of `data/sample_diff.patch` |
| `sample_response` | Parsed `data/sample_response.json` |
| `sample_opencode_output` | Contents of OpenCode output file |
| `temp_git_repo` | Temporary git repo with initial commit |
| `temp_git_repo_with_files` | Temp git repo with src/main.c, src/utils.h |
| `temp_config_file` | Valid config.yaml in temp directory |
| `valid_config_path` | Path to `data/config_valid.yaml` |
| `invalid_config_path` | Path to `data/config_invalid.yaml` |
| `env_config_path` | Path to `data/config_with_env.yaml` |
| `env_vars_for_config` | Sets `TEST_RB_TOKEN`, `TEST_LLM_KEY` env vars |
| `isolated_filesystem` | Changes cwd to temp directory |
| `sample_review_json` | Creates review JSON file for submit tests |

## Running Tests

```bash
# All tests
task test

# By category
task test:unit
task test:integration
task test:cli

# With coverage
task test:cov

# Quick run (minimal output)
task test:quick

# Specific file
uv run pytest tests/unit/test_config.py -v

# Specific test
uv run pytest tests/unit/test_config.py::TestLoadConfig::test_load_config_from_path -v
```

## Test Categories

### Unit Tests (107 tests)

Test individual functions in isolation. No I/O, no external dependencies.

- **Config**: Loading YAML, env var resolution, validation
- **Crypto**: Password encrypt/decrypt, file operations, permissions
- **Parsing**: Review ID from URLs, LLM JSON responses, OpenCode output
- **Diff Utils**: Extract changed files, filter by path patterns
- **Guidelines**: Load `.ai-review.yaml`, validate focus/severity

### Integration Tests (43 tests)

Test components working together with mocked externals.

- **Analyzer**: Full pipeline from diff to review result using `MockLLMProvider`
- **Repo Manager**: Git clone, checkout, patch with real temp repositories
- **Providers**: LLM provider factory creates correct provider types

### CLI Tests (48 tests)

Test CLI commands using Click's `CliRunner`.

- Commands run in isolated temp directories
- Config files created dynamically
- External services (RB, LLM) mocked or not called (dry-run mode)

## Writing New Tests

### Adding a Unit Test

```python
# tests/unit/test_mymodule.py
import pytest
from bb_review.mymodule import my_function

class TestMyFunction:
    def test_basic_case(self):
        result = my_function("input")
        assert result == "expected"

    def test_error_case(self):
        with pytest.raises(ValueError, match="Invalid"):
            my_function(None)
```

### Adding a CLI Test

```python
# tests/cli/test_mycommand.py
from click.testing import CliRunner
from bb_review.cli import main

class TestMyCommand:
    def test_basic(self, cli_runner: CliRunner, temp_config_file: Path):
        result = cli_runner.invoke(main, ["-c", str(temp_config_file), "mycommand"])
        assert result.exit_code == 0
        assert "Expected output" in result.output
```

### Using Mock LLM in Tests

```python
def test_with_mock_llm(self, sample_diff: str):
    from tests.mocks import MockLLMProvider

    mock = MockLLMProvider({
        "summary": "Test",
        "has_critical_issues": False,
        "comments": []
    })

    analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
    analyzer.llm = mock  # Inject mock

    result = analyzer.analyze(diff=sample_diff, ...)

    # Verify LLM was called correctly
    assert mock.get_call_count() == 1
    assert "sample" in mock.get_last_call()["user"]
```

## Test Markers

```python
@pytest.mark.slow
def test_slow_operation():
    ...

@pytest.mark.integration
def test_requires_multiple_components():
    ...
```

Run excluding slow tests:

```bash
uv run pytest -m "not slow"
```

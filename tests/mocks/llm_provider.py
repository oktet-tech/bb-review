"""Mock LLM provider for testing."""

import json
from typing import Any


class MockLLMProvider:
    """Mock LLM that returns configurable responses.

    This allows testing the analysis pipeline without making real API calls.
    """

    def __init__(self, response: dict | str | None = None):
        """Initialize the mock provider.

        Args:
            response: The response to return. Can be:
                - dict: Will be JSON-encoded
                - str: Returned as-is
                - None: Returns default empty review
        """
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Mock completion that records calls and returns configured response.

        Args:
            system_prompt: System prompt (recorded for assertions).
            user_prompt: User prompt (recorded for assertions).

        Returns:
            Configured response string.
        """
        self.calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
            }
        )

        if self.response is None:
            return json.dumps(
                {
                    "summary": "Test review complete",
                    "has_critical_issues": False,
                    "comments": [],
                }
            )

        if isinstance(self.response, dict):
            return json.dumps(self.response)

        return self.response

    def set_response(self, response: dict | str) -> None:
        """Update the response for subsequent calls.

        Args:
            response: New response to return.
        """
        self.response = response

    def get_call_count(self) -> int:
        """Get number of times complete() was called."""
        return len(self.calls)

    def get_last_call(self) -> dict[str, str] | None:
        """Get the last call's prompts, or None if no calls made."""
        if not self.calls:
            return None
        return self.calls[-1]

    def reset(self) -> None:
        """Clear call history."""
        self.calls = []


class MockLLMProviderWithIssues(MockLLMProvider):
    """Mock LLM that returns a response with issues."""

    def __init__(self):
        super().__init__(
            {
                "summary": "Found issues in the code",
                "has_critical_issues": False,
                "comments": [
                    {
                        "file_path": "src/main.c",
                        "line_number": 42,
                        "severity": "medium",
                        "issue_type": "bugs",
                        "message": "Potential null pointer dereference",
                        "suggestion": "Add null check before dereferencing",
                    },
                    {
                        "file_path": "src/utils.c",
                        "line_number": 15,
                        "severity": "low",
                        "issue_type": "style",
                        "message": "Variable name could be more descriptive",
                    },
                ],
            }
        )


class MockLLMProviderWithCritical(MockLLMProvider):
    """Mock LLM that returns a response with critical issues."""

    def __init__(self):
        super().__init__(
            {
                "summary": "Critical security issue found",
                "has_critical_issues": True,
                "comments": [
                    {
                        "file_path": "src/auth.c",
                        "line_number": 100,
                        "severity": "critical",
                        "issue_type": "security",
                        "message": "SQL injection vulnerability",
                        "suggestion": "Use parameterized queries",
                    },
                ],
            }
        )


class MockLLMProviderError(MockLLMProvider):
    """Mock LLM that raises an error on complete()."""

    def __init__(self, error: Exception | None = None):
        super().__init__()
        self.error = error or RuntimeError("API error")

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
            }
        )
        raise self.error

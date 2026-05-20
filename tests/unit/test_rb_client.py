"""Tests for ReviewBoardClient methods that do not need a live server."""

from bb_review.rr.rb_client import ReviewBoardClient


def test_list_repo_review_requests_merges_statuses(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        status = params["status"]
        if status == "submitted":
            return {
                "review_requests": [
                    {"id": 1, "last_updated": "2026-05-10T00:00:00"},
                    {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                ]
            }
        return {
            "review_requests": [
                {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                {"id": 3, "last_updated": "2026-05-01T00:00:00"},
            ]
        }

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    result = client.list_repo_review_requests(
        repository="test-repo",
        statuses=["submitted", "discarded"],
        limit=10,
    )
    # Deduped by id, sorted by last_updated descending.
    assert [r["id"] for r in result] == [2, 1, 3]


def test_list_repo_review_requests_respects_limit(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        return {
            "review_requests": [
                {"id": 1, "last_updated": "2026-05-10T00:00:00"},
                {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                {"id": 3, "last_updated": "2026-05-20T00:00:00"},
            ]
        }

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    result = client.list_repo_review_requests(
        repository="test-repo",
        statuses=["submitted"],
        limit=2,
    )
    assert [r["id"] for r in result] == [3, 2]

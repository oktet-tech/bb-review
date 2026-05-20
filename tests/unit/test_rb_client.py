"""Tests for ReviewBoardClient methods that do not need a live server."""

import pytest

from bb_review.rr.rb_client import ReviewBoardClient


def test_list_repo_review_requests_merges_statuses(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        if path == "/api/repositories/":
            return {"repositories": [{"id": 42, "name": params["name"]}]}
        status = params["status"]
        assert params["repository"] == "42"  # name resolved to id
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
        if path == "/api/repositories/":
            return {"repositories": [{"id": 7, "name": params["name"]}]}
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


def test_resolve_repository_id_passes_through_numeric(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        raise AssertionError("should not query when input is already numeric")

    monkeypatch.setattr(client, "_api_get", fake_api_get)
    assert client.resolve_repository_id("67") == "67"


def test_resolve_repository_id_looks_up_name(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    captured: dict = {}

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {"repositories": [{"id": 42, "name": "myrepo"}]}

    monkeypatch.setattr(client, "_api_get", fake_api_get)
    assert client.resolve_repository_id("myrepo") == "42"
    assert captured["path"] == "/api/repositories/"
    assert captured["params"] == {"name": "myrepo"}


def test_resolve_repository_id_raises_on_unknown(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    monkeypatch.setattr(client, "_api_get", lambda p, params=None: {"repositories": []})
    with pytest.raises(RuntimeError, match="No Review Board repository"):
        client.resolve_repository_id("nope")

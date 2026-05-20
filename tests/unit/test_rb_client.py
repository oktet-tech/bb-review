"""Tests for ReviewBoardClient methods that do not need a live server."""

import subprocess
from types import SimpleNamespace

import pytest

from bb_review.rr import rb_client
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


def test_split_status_and_body_parses_trailing_status_code():
    assert rb_client._split_status_and_body('{"ok": 1}\n200') == (200, '{"ok": 1}')


def test_split_status_and_body_falls_back_when_status_missing():
    # No trailing newline → no status to parse.
    assert rb_client._split_status_and_body("garbage") == (0, "garbage")


def test_decode_api_response_returns_json_on_success():
    assert rb_client._decode_api_response(200, '{"stat": "ok"}', "/api/x/") == {"stat": "ok"}


def test_decode_api_response_raises_on_html_5xx():
    html = "<html><body><h1>503 Service Unavailable</h1></body></html>"
    with pytest.raises(RuntimeError, match="HTTP 503"):
        rb_client._decode_api_response(503, html, "/api/review-requests/1/reviews/")


def test_decode_api_response_returns_synthetic_fail_on_non_5xx_garbage():
    # 401 with non-JSON body (e.g. an auth redirect) preserves prior soft-fail behavior.
    out = rb_client._decode_api_response(401, "not json", "/api/session/")
    assert out["stat"] == "fail"
    assert "HTTP 401" in out["err"]["msg"]


def test_curl_retries_on_http_503_then_succeeds(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    responses = [
        # First two attempts: 503 with HTML body.
        SimpleNamespace(returncode=0, stdout="<html>503</html>\n503", stderr=""),
        SimpleNamespace(returncode=0, stdout="<html>503</html>\n503", stderr=""),
        # Third attempt: clean JSON 200.
        SimpleNamespace(returncode=0, stdout='{"stat": "ok"}\n200', stderr=""),
    ]
    calls: list = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(rb_client.time, "sleep", lambda _s: None)

    status, body = client._curl("https://rb.example.com/api/")
    assert status == 200
    assert body == '{"stat": "ok"}'
    assert len(calls) == 3


def test_curl_returns_last_5xx_after_exhausting_retries(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def always_503(cmd, capture_output, text):
        return SimpleNamespace(returncode=0, stdout="<html>503</html>\n503", stderr="")

    monkeypatch.setattr(subprocess, "run", always_503)
    monkeypatch.setattr(rb_client.time, "sleep", lambda _s: None)

    status, body = client._curl("https://rb.example.com/api/")
    assert status == 503
    assert "503" in body
    # `_api_get` would then raise via `_decode_api_response`, which the rules
    # fetcher catches and records as a per-RR failure instead of silently
    # caching an empty comment set.

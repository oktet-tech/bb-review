"""Tests for pure functions in bb_review/cli/_review_runner.py."""

import re
from unittest.mock import MagicMock, patch

from bb_review.cli._review_runner import (
    build_submission_data,
    create_mock_review_output,
    generate_branch_name,
    save_to_review_db,
)
from bb_review.models import ReviewFocus, Severity
from bb_review.reviewers.opencode import ParsedIssue, ParsedReview, parse_opencode_output


class TestGenerateBranchName:
    def test_format(self):
        name = generate_branch_name(42762)
        assert name.startswith("bb_review_42762_")
        # Timestamp portion matches YYYYMMDD_HHMMSS
        timestamp = name.split("bb_review_42762_")[1]
        assert re.match(r"\d{8}_\d{6}$", timestamp)

    def test_different_ids(self):
        a = generate_branch_name(100)
        b = generate_branch_name(200)
        assert "bb_review_100_" in a
        assert "bb_review_200_" in b


class TestCreateMockReviewOutput:
    def test_contains_all_severities(self):
        output = create_mock_review_output(999)
        for sev in ("critical", "high", "medium", "low"):
            assert f"**Severity:** {sev}" in output

    def test_contains_review_id(self):
        output = create_mock_review_output(12345)
        assert "r/12345" in output

    def test_parseable(self):
        output = create_mock_review_output(1)
        parsed = parse_opencode_output(output)
        assert len(parsed.issues) > 0
        # Summary may end up in the last issue's suggestion due to parser behavior;
        # the key invariant is that issues are extracted
        assert len(parsed.issues) == 5


class TestBuildSubmissionData:
    @staticmethod
    def _make_parsed(
        issues: list[ParsedIssue] | None = None,
        unparsed_text: str = "",
        summary: str = "",
    ) -> ParsedReview:
        return ParsedReview(
            issues=issues or [],
            unparsed_text=unparsed_text,
            summary=summary,
        )

    def test_inline_comments(self):
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(
                    title="Bug",
                    file_path="a.c",
                    line_number=10,
                    severity="high",
                    comment="bad code",
                    suggestion="fix it",
                ),
            ]
        )
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert len(data["comments"]) == 1
        c = data["comments"][0]
        assert c["file_path"] == "a.c"
        assert c["line_number"] == 10
        assert "Bug" in c["text"]
        assert "Suggestion" in c["text"]

    def test_general_issues_in_body(self):
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(title="General concern", comment="something wrong"),
            ]
        )
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert len(data["comments"]) == 0
        assert "General concern" in data["body_top"]

    def test_unparsed_text_in_body(self):
        parsed = self._make_parsed(unparsed_text="Extra notes here")
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert "Extra notes here" in data["body_top"]

    def test_summary_in_body(self):
        parsed = self._make_parsed(summary="All good")
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert "All good" in data["body_top"]

    def test_rr_summary_passthrough(self):
        parsed = self._make_parsed()
        data = build_submission_data(1, "raw", parsed, "gpt-4", rr_summary="My summary")
        assert data["rr_summary"] == "My summary"

    def test_method_label_in_metadata(self):
        parsed = self._make_parsed()
        data = build_submission_data(1, "raw", parsed, "gpt-4", method_label="Claude Code")
        assert data["metadata"]["claude_code"] is True

    def test_method_label_default(self):
        parsed = self._make_parsed()
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert data["metadata"]["opencode"] is True

    def test_model_in_metadata(self):
        parsed = self._make_parsed()
        data = build_submission_data(1, "raw", parsed, "gpt-4")
        assert data["metadata"]["model"] == "gpt-4"

    def test_model_none_defaults(self):
        parsed = self._make_parsed()
        data = build_submission_data(1, "raw", parsed, None)
        assert data["metadata"]["model"] == "default"


class TestSaveToReviewDB:
    @staticmethod
    def _make_config(tmp_path):
        """Build a minimal mock Config with review_db."""
        config = MagicMock()
        config.review_db.enabled = True
        config.review_db.resolved_path = tmp_path / "reviews.db"
        config.reviewboard.url = "https://rb.example.com"
        return config

    @staticmethod
    def _make_parsed(issues=None, summary="Test summary"):
        return ParsedReview(
            issues=issues or [],
            summary=summary,
        )

    def test_severity_mapping_critical(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(
                    title="Vuln",
                    file_path="a.c",
                    line_number=1,
                    severity="critical",
                    issue_type="security",
                ),
            ]
        )
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
            call_args = mock_db.save_analysis.call_args
            result = call_args.kwargs["result"]
            assert result.comments[0].severity == Severity.CRITICAL
            assert result.has_critical_issues is True

    def test_severity_mapping_high(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(title="X", file_path="a.c", line_number=1, severity="high"),
            ]
        )
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
            result = mock_db.save_analysis.call_args.kwargs["result"]
            assert result.comments[0].severity == Severity.HIGH

    def test_severity_mapping_low(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(title="X", file_path="a.c", line_number=1, severity="low"),
            ]
        )
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
            result = mock_db.save_analysis.call_args.kwargs["result"]
            assert result.comments[0].severity == Severity.LOW

    def test_severity_default_medium(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(title="X", file_path="a.c", line_number=1, severity="unknown"),
            ]
        )
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
            result = mock_db.save_analysis.call_args.kwargs["result"]
            assert result.comments[0].severity == Severity.MEDIUM

    def test_issue_type_mapping(self, tmp_path):
        config = self._make_config(tmp_path)
        type_map = {
            "security": ReviewFocus.SECURITY,
            "performance": ReviewFocus.PERFORMANCE,
            "style": ReviewFocus.STYLE,
            "architecture": ReviewFocus.ARCHITECTURE,
            "bugs": ReviewFocus.BUGS,
            "unknown": ReviewFocus.BUGS,
        }
        for type_str, expected in type_map.items():
            parsed = self._make_parsed(
                issues=[
                    ParsedIssue(
                        title="X",
                        file_path="a.c",
                        line_number=1,
                        issue_type=type_str,
                    ),
                ]
            )
            with patch("bb_review.db.ReviewDatabase") as MockDB:
                mock_db = MockDB.return_value
                save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
                result = mock_db.save_analysis.call_args.kwargs["result"]
                assert result.comments[0].issue_type == expected, f"{type_str} -> {expected}"

    def test_chain_id_creates_chain(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed()
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(
                config,
                1,
                1,
                "repo",
                parsed,
                "gpt-4",
                chain_id="bb_review_1_20260101_120000",
            )
            mock_db.ensure_chain_exists.assert_called_once_with(
                "bb_review_1_20260101_120000",
                "repo",
                branch_name="bb_review_1_20260101_120000",
            )

    def test_db_exception_warns_no_crash(self, tmp_path, caplog):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed()
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            MockDB.side_effect = RuntimeError("DB exploded")
            # Should not raise
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
        assert "Failed to save" in caplog.text

    def test_rr_summary_creates_rr_info(self, tmp_path):
        config = self._make_config(tmp_path)
        parsed = self._make_parsed()
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(
                config,
                42,
                3,
                "repo",
                parsed,
                "gpt-4",
                rr_summary="Fix login bug",
            )
            call_args = mock_db.save_analysis.call_args
            rr_info = call_args.kwargs["rr_info"]
            assert rr_info is not None
            assert rr_info.id == 42
            assert rr_info.summary == "Fix login bug"

    def test_issues_without_file_skipped_in_comments(self, tmp_path):
        """Issues without file_path+line_number are not added as ReviewComments."""
        config = self._make_config(tmp_path)
        parsed = self._make_parsed(
            issues=[
                ParsedIssue(title="General note", comment="hmm"),
            ]
        )
        with patch("bb_review.db.ReviewDatabase") as MockDB:
            mock_db = MockDB.return_value
            save_to_review_db(config, 1, 1, "repo", parsed, "gpt-4")
            result = mock_db.save_analysis.call_args.kwargs["result"]
            assert len(result.comments) == 0

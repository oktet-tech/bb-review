"""Tests for review ID URL parsing."""

from click import BadParameter
import pytest

from bb_review.cli.utils import ReviewIdParamType, parse_review_id


class TestParseReviewId:
    """Tests for parse_review_id function."""

    def test_parse_plain_integer(self):
        """Parse plain integer string."""
        assert parse_review_id("42738") == 42738

    def test_parse_url_with_slash(self):
        """Parse URL with trailing slash."""
        assert parse_review_id("https://rb.example.com/r/42738/") == 42738

    def test_parse_url_without_slash(self):
        """Parse URL without trailing slash."""
        assert parse_review_id("https://rb.example.com/r/42738") == 42738

    def test_parse_url_with_diff(self):
        """Parse URL with /diff/ path."""
        assert parse_review_id("https://rb.example.com/r/42738/diff/") == 42738

    def test_parse_url_with_diff_revision(self):
        """Parse URL with diff revision."""
        assert parse_review_id("https://rb.example.com/r/42738/diff/2/") == 42738

    def test_parse_http_url(self):
        """Parse http (non-https) URL."""
        assert parse_review_id("http://rb.example.com/r/12345/") == 12345

    def test_parse_different_domains(self):
        """Parse URLs from different domains."""
        assert parse_review_id("https://reviews.company.org/r/99999/") == 99999

    def test_parse_invalid_string(self):
        """Error on invalid string."""
        with pytest.raises(BadParameter, match="Cannot parse review ID"):
            parse_review_id("not-a-valid-id")

    def test_parse_empty_string(self):
        """Error on empty string."""
        with pytest.raises(BadParameter, match="Cannot parse review ID"):
            parse_review_id("")

    def test_parse_negative_number(self):
        """Negative number is parsed (validation happens elsewhere)."""
        # Note: The parser accepts negative numbers; validation is done elsewhere
        result = parse_review_id("-123")
        assert result == -123

    def test_parse_float_string(self):
        """Error on float string."""
        with pytest.raises(BadParameter, match="Cannot parse review ID"):
            parse_review_id("123.45")

    def test_parse_url_without_r(self):
        """Error on URL without /r/ path."""
        with pytest.raises(BadParameter, match="Cannot parse review ID"):
            parse_review_id("https://rb.example.com/reviews/42738/")


class TestReviewIdParamType:
    """Tests for Click parameter type."""

    def test_convert_integer(self):
        """Convert plain integer string."""
        param_type = ReviewIdParamType()
        assert param_type.convert("42738", None, None) == 42738

    def test_convert_url(self):
        """Convert URL string."""
        param_type = ReviewIdParamType()
        assert param_type.convert("https://rb.example.com/r/42738/", None, None) == 42738

    def test_convert_none(self):
        """Return None for None input."""
        param_type = ReviewIdParamType()
        assert param_type.convert(None, None, None) is None

    def test_convert_already_int(self):
        """Return int if already int."""
        param_type = ReviewIdParamType()
        assert param_type.convert(42738, None, None) == 42738

    def test_param_type_name(self):
        """Check parameter type name."""
        param_type = ReviewIdParamType()
        assert param_type.name == "review_id"

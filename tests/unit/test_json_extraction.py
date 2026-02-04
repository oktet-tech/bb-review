"""Tests for _extract_json_object from bb_review/reviewers/llm.py."""

from bb_review.reviewers.llm import _extract_json_object


class TestExtractJsonObject:
    """Test brace-balanced JSON extraction."""

    def test_plain_json(self):
        text = '{"key": "value", "num": 42}'
        assert _extract_json_object(text) == text

    def test_json_in_markdown_fences(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json_object(text) == '{"key": "value"}'

    def test_json_with_trailing_text(self):
        text = 'Here is the result: {"a": 1} and some explanation after.'
        assert _extract_json_object(text) == '{"a": 1}'

    def test_json_with_leading_text(self):
        text = 'Some preamble\n{"a": 1}'
        assert _extract_json_object(text) == '{"a": 1}'

    def test_nested_objects(self):
        text = '{"a": {"b": 1, "c": {"d": 2}}}'
        assert _extract_json_object(text) == text

    def test_strings_containing_braces(self):
        text = '{"msg": "use {x} here"}'
        assert _extract_json_object(text) == text

    def test_escaped_quotes(self):
        text = '{"msg": "say \\"hello\\"", "n": 1}'
        assert _extract_json_object(text) == text

    def test_no_json(self):
        assert _extract_json_object("no json here") is None

    def test_empty_string(self):
        assert _extract_json_object("") is None

    def test_unbalanced_braces_missing_close(self):
        assert _extract_json_object('{"key": "val"') is None

    def test_multiple_top_level_objects_returns_first(self):
        text = '{"a": 1} {"b": 2}'
        assert _extract_json_object(text) == '{"a": 1}'

    def test_nested_arrays_and_objects(self):
        text = '{"items": [{"id": 1}, {"id": 2}]}'
        assert _extract_json_object(text) == text

    def test_empty_object(self):
        assert _extract_json_object("{}") == "{}"

    def test_string_with_backslash_not_before_quote(self):
        text = '{"path": "C:\\\\Users\\\\test"}'
        assert _extract_json_object(text) == text

    def test_braces_only_inside_strings_no_top_level(self):
        # Opening brace starts, but strings with braces don't close the depth
        text = '{"val": "}"}'
        result = _extract_json_object(text)
        assert result == '{"val": "}"}'

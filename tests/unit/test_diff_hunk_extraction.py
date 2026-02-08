"""Tests for diff hunk extraction utility."""

from bb_review.reviewers.diff_utils import extract_diff_hunk


SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@ def main():
     config = load_config()
     logger = setup_logger()

+    # Initialize the database
+    db = Database(config.db_path)
     app = Application(config, logger)
     app.run()

@@ -30,4 +32,7 @@ def cleanup():
     logger.info("Cleaning up...")
     shutil.rmtree(tmp_dir)

+def new_function():
+    pass
+
 # end of file
diff --git a/src/utils.py b/src/utils.py
index 1234567..89abcde 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,7 +5,7 @@ import os

 def helper():
-    return old_value
+    return new_value


 def another():
"""


class TestExtractDiffHunk:
    """Tests for extract_diff_hunk function."""

    def test_first_hunk(self):
        """Finds the correct hunk when line is in the first hunk."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 11)
        assert result is not None
        assert "@@ -10,6 +10,8 @@" in result
        assert "+    # Initialize the database" in result

    def test_second_hunk(self):
        """Finds the correct hunk when line is in the second hunk."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 34)
        assert result is not None
        assert "@@ -30,4 +32,7 @@" in result
        assert "+def new_function():" in result

    def test_different_file(self):
        """Finds hunk in a different file."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/utils.py", 8)
        assert result is not None
        assert "+    return new_value" in result
        assert "-    return old_value" in result

    def test_no_match_wrong_file(self):
        """Returns None when file is not in the diff."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/missing.py", 10)
        assert result is None

    def test_no_match_wrong_line(self):
        """Returns None when line is outside all hunks."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 25)
        assert result is None

    def test_empty_inputs(self):
        """Returns None for empty/None inputs."""
        assert extract_diff_hunk("", "src/main.py", 10) is None
        assert extract_diff_hunk(SAMPLE_DIFF, "", 10) is None
        assert extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 0) is None

    def test_suffix_match(self):
        """Matches file path by suffix (no leading directory)."""
        result = extract_diff_hunk(SAMPLE_DIFF, "main.py", 11)
        assert result is not None
        assert "@@ -10,6 +10,8 @@" in result

    def test_single_line_count(self):
        """Handles @@ header with no count (implies count=1)."""
        diff = """\
diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-old
+new
"""
        result = extract_diff_hunk(diff, "file.py", 1)
        assert result is not None
        assert "+new" in result

    def test_preserves_full_hunk(self):
        """Returns complete hunk including context lines."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 12)
        assert result is not None
        lines = result.split("\n")
        assert lines[0].startswith("@@")
        # Should contain context lines (starting with space)
        assert any(line.startswith(" ") for line in lines if line)

    def test_boundary_line_start(self):
        """Matches when line equals hunk start."""
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 10)
        assert result is not None
        assert "@@ -10,6 +10,8 @@" in result

    def test_boundary_line_end(self):
        """Matches when line equals hunk end."""
        # First hunk: +10,8 -> lines 10-17
        result = extract_diff_hunk(SAMPLE_DIFF, "src/main.py", 17)
        assert result is not None
        assert "@@ -10,6 +10,8 @@" in result

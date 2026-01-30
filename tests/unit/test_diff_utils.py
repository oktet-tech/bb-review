"""Tests for diff utility functions."""

from bb_review.reviewers.llm import extract_changed_files, filter_diff_by_paths


class TestExtractChangedFiles:
    """Tests for extract_changed_files function."""

    def test_extract_from_unified_diff(self):
        """Extract files from unified diff format."""
        diff = """diff --git a/src/main.c b/src/main.c
index abc123..def456 100644
--- a/src/main.c
+++ b/src/main.c
@@ -10,6 +12,8 @@ int main() {
     printf("Hello");
+    int x = 42;
+    printf("x = %d", x);
     return 0;
 }
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert files[0]["path"] == "src/main.c"
        assert 12 in files[0]["lines"]  # Start line from hunk header

    def test_extract_multiple_files(self, sample_diff: str):
        """Extract multiple files from diff."""
        files = extract_changed_files(sample_diff)

        assert len(files) == 2
        paths = [f["path"] for f in files]
        assert "src/main.c" in paths
        assert "src/utils.c" in paths

    def test_extract_new_file(self):
        """Extract info for newly added file."""
        diff = """diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..abc123
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,5 @@
+def main():
+    print("Hello")
+
+if __name__ == "__main__":
+    main()
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert files[0]["path"] == "new_file.py"

    def test_extract_deleted_file(self):
        """Extract info for deleted file."""
        diff = """diff --git a/old_file.py b/old_file.py
deleted file mode 100644
index abc123..0000000
--- a/old_file.py
+++ /dev/null
@@ -1,5 +0,0 @@
-def main():
-    print("Goodbye")
-
-if __name__ == "__main__":
-    main()
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert files[0]["path"] == "old_file.py"

    def test_extract_empty_diff(self):
        """Handle empty diff."""
        files = extract_changed_files("")
        assert files == []

    def test_extract_multiple_hunks(self):
        """Extract from diff with multiple hunks."""
        diff = """diff --git a/file.c b/file.c
index abc..def 100644
--- a/file.c
+++ b/file.c
@@ -5,3 +5,4 @@ void func1() {
     line1();
+    new_line();
 }
@@ -20,3 +21,4 @@ void func2() {
     line2();
+    another_line();
 }
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert 5 in files[0]["lines"]
        assert 21 in files[0]["lines"]

    def test_extract_binary_file(self):
        """Handle binary file diff."""
        diff = """diff --git a/image.png b/image.png
new file mode 100644
index 0000000..abc123
Binary files /dev/null and b/image.png differ
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert files[0]["path"] == "image.png"
        # Binary files don't have line info
        assert files[0]["lines"] == []


class TestFilterDiffByPaths:
    """Tests for filter_diff_by_paths function."""

    def test_filter_exact_path(self):
        """Filter by exact path."""
        diff = """diff --git a/keep.c b/keep.c
--- a/keep.c
+++ b/keep.c
@@ -1,1 +1,2 @@
 line1
+line2

diff --git a/remove.c b/remove.c
--- a/remove.c
+++ b/remove.c
@@ -1,1 +1,2 @@
 line1
+line2
"""
        filtered = filter_diff_by_paths(diff, ["remove.c"])

        assert "keep.c" in filtered
        assert "remove.c" not in filtered

    def test_filter_glob_star(self):
        """Filter using * glob."""
        diff = """diff --git a/src/main.c b/src/main.c
--- a/src/main.c
+++ b/src/main.c
@@ -1,1 +1,1 @@
-old
+new

diff --git a/tests/test_main.c b/tests/test_main.c
--- a/tests/test_main.c
+++ b/tests/test_main.c
@@ -1,1 +1,1 @@
-old test
+new test
"""
        # Filter out test files
        filtered = filter_diff_by_paths(diff, ["tests/*"])

        assert "src/main.c" in filtered
        assert "tests/test_main.c" not in filtered

    def test_filter_extension_glob(self, sample_diff: str):
        """Filter by file extension."""
        # Filter all .c files
        filtered = filter_diff_by_paths(sample_diff, ["*.c"])

        # Should be empty (all files are .c)
        assert "diff --git" not in filtered or filtered.strip() == ""

    def test_filter_preserves_diff_format(self, sample_diff: str):
        """Filtered diff maintains valid format."""
        filtered = filter_diff_by_paths(sample_diff, ["src/utils.c"])

        # Should still have diff header for remaining file
        assert "diff --git a/src/main.c b/src/main.c" in filtered
        # Should have hunk headers
        assert "@@" in filtered

    def test_filter_multiple_patterns(self):
        """Filter with multiple patterns."""
        diff = """diff --git a/src/main.c b/src/main.c
--- a/src/main.c
+++ b/src/main.c
@@ -1,1 +1,1 @@
-old
+new

diff --git a/tests/test.py b/tests/test.py
--- a/tests/test.py
+++ b/tests/test.py
@@ -1,1 +1,1 @@
-old
+new

diff --git a/docs/readme.md b/docs/readme.md
--- a/docs/readme.md
+++ b/docs/readme.md
@@ -1,1 +1,1 @@
-old
+new
"""
        filtered = filter_diff_by_paths(diff, ["tests/*", "docs/*"])

        assert "src/main.c" in filtered
        assert "tests/test.py" not in filtered
        assert "docs/readme.md" not in filtered

    def test_filter_no_patterns(self, sample_diff: str):
        """Empty patterns filter nothing."""
        filtered = filter_diff_by_paths(sample_diff, [])

        # The filtered content should contain all original files
        # (may differ in whitespace at end)
        assert "src/main.c" in filtered
        assert "src/utils.c" in filtered

    def test_filter_no_match(self, sample_diff: str):
        """Non-matching patterns filter nothing."""
        filtered = filter_diff_by_paths(sample_diff, ["*.nonexistent"])

        assert "src/main.c" in filtered
        assert "src/utils.c" in filtered

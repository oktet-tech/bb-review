"""Utilities for extracting diff hunks around specific lines."""

import re


def extract_diff_hunk(raw_diff: str, file_path: str, line_number: int) -> str | None:
    """Extract the unified diff hunk containing a specific line.

    Finds the file section in the diff (suffix-matching the path) and returns
    the hunk whose new-file line range covers the given line_number.

    Args:
        raw_diff: Full unified diff text.
        file_path: Path of the file to find (suffix-matched).
        line_number: Line number (new-file side) to locate.

    Returns:
        The hunk text (from @@ header through end of hunk), or None if not found.
    """
    if not raw_diff or not file_path or not line_number:
        return None

    # Split diff into per-file sections on "diff --git" boundaries
    sections = re.split(r"(?=^diff --git )", raw_diff, flags=re.MULTILINE)

    file_section = None
    for section in sections:
        if not section.startswith("diff --git"):
            continue
        # Match file path by suffix (handles a/path b/path format)
        first_line = section.split("\n", 1)[0]
        parts = first_line.split()
        if len(parts) >= 4:
            b_path = parts[3].lstrip("b/")
            if b_path == file_path or file_path.endswith(b_path) or b_path.endswith(file_path):
                file_section = section
                break

    if file_section is None:
        return None

    # Parse hunks from the file section
    lines = file_section.split("\n")
    hunks: list[tuple[int, int, list[str]]] = []
    current_hunk_lines: list[str] = []
    hunk_start = 0
    hunk_count = 0

    for line in lines:
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match:
            # Save previous hunk if any
            if current_hunk_lines:
                hunks.append((hunk_start, hunk_start + hunk_count - 1, current_hunk_lines))
            hunk_start = int(hunk_match.group(1))
            hunk_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            current_hunk_lines = [line]
        elif current_hunk_lines:
            # Inside a hunk -- only content lines (context, add, remove)
            if line.startswith("+") or line.startswith("-") or line.startswith(" ") or line == "":
                current_hunk_lines.append(line)
            elif line.startswith("\\"):
                # "\ No newline at end of file"
                current_hunk_lines.append(line)

    # Save last hunk
    if current_hunk_lines:
        hunks.append((hunk_start, hunk_start + hunk_count - 1, current_hunk_lines))

    # Find the hunk covering line_number
    for start, end, hunk_lines in hunks:
        if start <= line_number <= end:
            return "\n".join(hunk_lines)

    return None

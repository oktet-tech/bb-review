"""Shared UI utilities."""

import re


def extract_file_diff(raw_diff: str, file_path: str) -> str | None:
    """Extract the diff section for a single file from a unified diff."""
    if not raw_diff or not file_path:
        return None

    sections = re.split(r"(?=^diff --git )", raw_diff, flags=re.MULTILINE)
    for section in sections:
        if file_path in section.split("\n", 1)[0]:
            return section.strip()
    return None

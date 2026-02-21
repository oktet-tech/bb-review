"""Shared UI utilities."""

import os
import re


def extract_file_diff(raw_diff: str, file_path: str) -> str | None:
    """Extract the diff section for a single file from a unified diff.

    Tries exact match first, then falls back to basename matching to handle
    prefix mismatches between RB filediff paths and diff headers (e.g.
    ``a/src/foo.py`` vs ``src/foo.py``).
    """
    if not raw_diff or not file_path:
        return None

    sections = re.split(r"(?=^diff --git )", raw_diff, flags=re.MULTILINE)

    # Exact match
    for section in sections:
        if file_path in section.split("\n", 1)[0]:
            return section.strip()

    # Basename fallback -- file_path may lack the a/ b/ prefix or vice versa
    basename = os.path.basename(file_path)
    for section in sections:
        header = section.split("\n", 1)[0]
        if basename in header:
            return section.strip()

    return None

"""BB Review - AI-powered code review system for Review Board."""

__version__ = "0.1.0"

# Re-export commonly used classes for backward compatibility
from .git import RepoManager, RepoManagerError
from .indexing import CodebaseIndexer, IndexConfig, IndexResult
from .reviewers import Analyzer, extract_changed_files, filter_diff_by_paths
from .rr import Commenter, DiffInfo, ReviewBoardClient, ReviewFormatter


__all__ = [
    "__version__",
    # Reviewers
    "Analyzer",
    "extract_changed_files",
    "filter_diff_by_paths",
    # Review Board
    "ReviewBoardClient",
    "DiffInfo",
    "Commenter",
    "ReviewFormatter",
    # Git
    "RepoManager",
    "RepoManagerError",
    # Indexing
    "CodebaseIndexer",
    "IndexConfig",
    "IndexResult",
]

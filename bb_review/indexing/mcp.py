"""MCP server for semantic code search using CocoIndex.

This provides an MCP server that OpenCode can use to search indexed codebases.
Uses local sentence-transformers embeddings (no API calls).

Usage:
    bb-review mcp serve <repo-name>

    # Or directly:
    python -m bb_review.mcp_server <repo-name>
"""

import logging
import os
from pathlib import Path
import sys

from fastmcp import FastMCP


logger = logging.getLogger(__name__)

# Will be initialized when server starts
_indexer = None
_repo_name = None
_embedding_model = None


def get_indexer():
    """Get or create the indexer instance."""
    global _indexer
    if _indexer is None:
        from .indexer import CodebaseIndexer

        db_url = os.environ.get(
            "COCOINDEX_DATABASE_URL", "postgresql://cocoindex:cocoindex@localhost:5432/cocoindex"
        )
        _indexer = CodebaseIndexer(db_url)
    return _indexer


# Create the MCP server
mcp = FastMCP(
    name="cocoindex-search",
    instructions="""
    Semantic code search server powered by CocoIndex.

    Use codebase_search to find relevant code by meaning, not just keywords.
    For example: "authentication logic", "database connection handling",
    "error handling for HTTP requests".
    """,
)


@mcp.tool()
def codebase_search(query: str, top_k: int = 10) -> dict:
    """Search the codebase for code matching the query.

    Uses semantic search to find code by meaning, not just keywords.
    Returns relevant code snippets with their file locations.

    Args:
        query: Natural language description of what you're looking for.
               Examples: "authentication logic", "database queries",
               "error handling", "API endpoints"
        top_k: Number of results to return (default: 10, max: 50)

    Returns:
        Dictionary with:
        - results: List of matching code snippets with filename and score
        - query: The original query
        - repo: Repository that was searched
    """
    global _repo_name, _embedding_model

    logger.info(f"[MCP REQUEST] codebase_search(query={query!r}, top_k={top_k})")

    if not _repo_name:
        return {"error": "No repository configured. Start server with a repo name.", "results": []}

    # Clamp top_k
    top_k = max(1, min(50, top_k))

    try:
        indexer = get_indexer()
        results = indexer.search(
            repo_name=_repo_name,
            query=query,
            top_k=top_k,
            embedding_model=_embedding_model or "sentence-transformers/all-MiniLM-L6-v2",
        )

        response = {
            "query": query,
            "repo": _repo_name,
            "count": len(results),
            "results": [
                {"filename": r["filename"], "code": r["code"], "score": round(r["score"], 4)} for r in results
            ],
        }
        logger.info(f"[MCP RESPONSE] codebase_search: {len(results)} results")
        for r in results[:3]:  # Log top 3 results
            logger.info(f"  - {r['filename']} (score: {r['score']:.4f})")
        return response
    except Exception as e:
        logger.error(f"[MCP ERROR] Search failed: {e}")
        return {"error": str(e), "query": query, "results": []}


@mcp.tool()
def codebase_status() -> dict:
    """Get the status of indexed repositories.

    Returns information about what repositories are indexed and their
    file/chunk counts.
    """
    logger.info("[MCP REQUEST] codebase_status()")

    try:
        indexer = get_indexer()
        status = indexer.get_status()

        response = {
            "repositories": [
                {"name": s["repo"], "files": s["file_count"], "chunks": s["chunk_count"]} for s in status
            ],
            "current_repo": _repo_name,
        }
        logger.info(f"[MCP RESPONSE] codebase_status: {len(status)} repos indexed")
        return response
    except Exception as e:
        logger.error(f"[MCP ERROR] Status check failed: {e}")
        return {"error": str(e), "repositories": []}


def run_server(repo_name: str, embedding_model: str = None, log_file: str = None):
    """Run the MCP server for a specific repository.

    Args:
        repo_name: Name of the repository to search
        embedding_model: HuggingFace model for query embeddings
        log_file: File path to write logs. Defaults to ~/.bb_review/mcp-{repo_name}.log
                  Set to empty string "" to disable file logging.
    """
    global _repo_name, _embedding_model
    _repo_name = repo_name
    _embedding_model = embedding_model

    # Suppress all logging that might go to stdout
    # FastMCP and other libraries may log to stdout which breaks MCP protocol
    logging.getLogger("fastmcp").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("docket").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Default log file is ~/.bb_review/mcp-{repo_name}.log
    if log_file is None:
        log_file = f"~/.bb_review/mcp-{repo_name}.log"

    # Add file handler (unless explicitly disabled with empty string)
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_path}")

    # Log to stderr only
    logger.info(f"Starting MCP server for repository: {repo_name}")
    logger.info(f"Embedding model: {embedding_model or 'sentence-transformers/all-MiniLM-L6-v2'}")

    # Run the server (stdio transport for MCP)
    # show_banner=False is critical - the banner breaks JSON-RPC protocol
    mcp.run(show_banner=False)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="CocoIndex MCP Server")
    parser.add_argument("repo_name", help="Repository name to search")
    parser.add_argument(
        "--model",
        "-m",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Embedding model (default: sentence-transformers/all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--database-url",
        "-d",
        default=None,
        help="PostgreSQL database URL (default: from COCOINDEX_DATABASE_URL or localhost)",
    )
    parser.add_argument("--log-file", "-l", default=None, help="Log file path (logs also go to stderr)")

    args = parser.parse_args()

    if args.database_url:
        os.environ["COCOINDEX_DATABASE_URL"] = args.database_url

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # Log to stderr, keep stdout for MCP protocol
    )

    run_server(args.repo_name, args.model, args.log_file)


if __name__ == "__main__":
    main()

"""CocoIndex commands for BB Review CLI."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import click

from ..git import RepoManager, RepoManagerError
from . import get_config, main


@main.group()
def cocoindex():
    """CocoIndex semantic code indexing commands."""
    pass


@cocoindex.command("start")
@click.argument("repo_name")
@click.option("--rescan", is_flag=True, help="Force re-index from scratch")
@click.pass_context
def cocoindex_start(ctx: click.Context, repo_name: str, rescan: bool) -> None:
    """Run cocode-mcp interactively for testing/debugging.

    NOTE: cocode-mcp is stdio-based - normally the MCP client (OpenCode)
    spawns it directly. This command runs it interactively for testing.

    Requires JINA_API_KEY or OPENAI_API_KEY for embeddings.

    Example:
        bb-review cocoindex start te-dev
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    # Verify repo exists in config
    repo_config = config.get_repo_config_by_name(repo_name)
    if repo_config is None:
        click.echo(f"Error: Repository '{repo_name}' not found in config", err=True)
        click.echo("Available repositories:")
        for repo in config.repositories:
            click.echo(f"  - {repo.name}")
        sys.exit(1)

    # Check if CocoIndex is enabled
    if not repo_config.is_cocoindex_enabled(config.cocoindex.enabled):
        click.echo(f"Warning: CocoIndex is not enabled for '{repo_name}' in config")
        click.echo("Enable it by adding 'cocoindex.enabled: true' to the repo config")

    # Run the server script (for testing - cocode-mcp is normally spawned by MCP client)
    script_path = Path(__file__).parent.parent.parent / "scripts" / "cocoindex-server.sh"
    if not script_path.exists():
        click.echo(f"Error: Server script not found: {script_path}", err=True)
        sys.exit(1)

    cmd = [str(script_path), "start", repo_name]
    if rescan:
        cmd.append("--rescan")

    # Set environment
    env = dict(os.environ)
    env["COCOINDEX_DATABASE_URL"] = config.cocoindex.database_url

    # Get embedding API key from config (or use llm.api_key for openrouter)
    embedding_key = getattr(config.cocoindex, "embedding_api_key", None)
    embedding_provider = getattr(config.cocoindex, "embedding_provider", None)
    if not embedding_key and embedding_provider == "openrouter":
        # Reuse the LLM API key for OpenRouter embeddings
        embedding_key = config.llm.api_key

    if embedding_key:
        env["COCOINDEX_EMBEDDING_API_KEY"] = embedding_key
        env["COCOINDEX_EMBEDDING_PROVIDER"] = embedding_provider or ""
        env["COCOINDEX_EMBEDDING_MODEL"] = config.cocoindex.embedding_model
        if embedding_provider == "openrouter":
            env["COCOINDEX_EMBEDDING_BASE_URL"] = "https://openrouter.ai/api/v1"

    try:
        result = subprocess.run(cmd, env=env)
        sys.exit(result.returncode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cocoindex.command("stop")
@click.argument("repo_name")
@click.pass_context
def cocoindex_stop(ctx: click.Context, repo_name: str) -> None:
    """Stop CocoIndex MCP server for a repository.

    Example:
        bb-review cocoindex stop te-dev
    """
    script_path = Path(__file__).parent.parent.parent / "scripts" / "cocoindex-server.sh"
    if not script_path.exists():
        click.echo(f"Error: Server script not found: {script_path}", err=True)
        sys.exit(1)

    try:
        result = subprocess.run([str(script_path), "stop", repo_name])
        sys.exit(result.returncode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cocoindex.command("status")
@click.argument("repo_name", required=False)
@click.pass_context
def cocoindex_status(ctx: click.Context, repo_name: str | None) -> None:
    """Show CocoIndex server status.

    Shows status for a specific repository, or all repositories if none specified.

    Example:
        bb-review cocoindex status         # Show all
        bb-review cocoindex status te-dev  # Show specific repo
    """
    try:
        get_config(ctx)
    except FileNotFoundError:
        # Can still show status without config
        pass

    script_path = Path(__file__).parent.parent.parent / "scripts" / "cocoindex-server.sh"
    if not script_path.exists():
        click.echo(f"Error: Server script not found: {script_path}", err=True)
        sys.exit(1)

    cmd = [str(script_path), "status"]
    if repo_name:
        cmd.append(repo_name)

    try:
        result = subprocess.run(cmd)
        sys.exit(result.returncode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cocoindex.command("logs")
@click.argument("repo_name")
@click.pass_context
def cocoindex_logs(ctx: click.Context, repo_name: str) -> None:
    """Follow CocoIndex server logs for a repository.

    Press Ctrl+C to stop following.

    Example:
        bb-review cocoindex logs te-dev
    """
    script_path = Path(__file__).parent.parent.parent / "scripts" / "cocoindex-server.sh"
    if not script_path.exists():
        click.echo(f"Error: Server script not found: {script_path}", err=True)
        sys.exit(1)

    try:
        result = subprocess.run([str(script_path), "logs", repo_name])
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cocoindex.command("setup")
@click.argument("repo_name")
@click.option("--force", is_flag=True, help="Overwrite existing config file")
@click.option(
    "--template", type=click.Choice(["local", "filesystem"]), default="local", help="MCP template to use"
)
@click.option(
    "--tool",
    type=click.Choice(["opencode", "claude"]),
    default="opencode",
    help="Target tool: opencode (opencode.json) or claude (.mcp.json)",
)
@click.pass_context
def cocoindex_setup(ctx: click.Context, repo_name: str, force: bool, template: str, tool: str) -> None:
    """Setup MCP config for OpenCode or Claude Code in a repository.

    Generates opencode.json (default) or .mcp.json (--tool claude) for
    semantic code search via CocoIndex MCP server.

    Templates:
        local       - Use bb-review MCP server with local embeddings (default)
        filesystem  - Basic filesystem MCP (no semantic search)

    Prerequisites for 'local' template:
        1. Index the repo first: bb-review cocoindex index <repo-name>
        2. PostgreSQL running: bb-review cocoindex db start

    Example:
        bb-review cocoindex setup te-dev
        bb-review cocoindex setup te-dev --tool claude
        bb-review cocoindex setup te-dev --template filesystem
    """

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())

    try:
        repo_path = repo_manager.get_local_path(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not repo_path.exists():
        click.echo(f"Error: Repository not cloned. Run 'bb-review repos sync {repo_name}' first.", err=True)
        sys.exit(1)

    # Determine target file based on tool
    if tool == "claude":
        target_file = repo_path / ".mcp.json"
    else:
        target_file = repo_path / "opencode.json"

    if target_file.exists() and not force:
        click.echo(f"Error: {target_file} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)

    # Find bb-review installation directory (for uv run --directory)
    bb_review_dir = str(Path(__file__).parent.parent.parent)

    # Find bb-review binary path (for opencode)
    bb_review_bin = shutil.which("bb-review")
    if not bb_review_bin:
        venv_bb = Path(__file__).parent.parent.parent / ".venv" / "bin" / "bb-review"
        if venv_bb.exists():
            bb_review_bin = str(venv_bb)
        else:
            bb_review_bin = "bb-review"  # Hope it's in PATH

    if tool == "claude":
        output_config = _build_claude_mcp_config(repo_name, bb_review_dir, config, template, repo_path)
    else:
        output_config = _build_opencode_config(repo_name, bb_review_bin, config, template, repo_path)

    # Write config
    target_file.write_text(json.dumps(output_config, indent=2) + "\n")
    click.echo(f"Created: {target_file}")
    click.echo(f"Template: {template}")

    if tool == "claude":
        click.echo("\nUse with Claude Code:")
        click.echo(f"  bb-review claude <rr-id> --mcp-config {target_file} -O")
    else:
        click.echo("\nNow run OpenCode in the repo:")
        click.echo(f"  cd {repo_path} && opencode")


def _build_claude_mcp_config(
    repo_name: str,
    bb_review_dir: str,
    config,
    template: str,
    repo_path: Path,
) -> dict:
    """Build .mcp.json content for Claude Code."""
    if template == "local":
        click.echo("Using local CocoIndex MCP server")
        click.echo("Make sure you've indexed the repo: bb-review cocoindex index " + repo_name)
        return {
            "mcpServers": {
                f"cocode-search-{repo_name}": {
                    "command": "uv",
                    "args": [
                        "run",
                        "--directory",
                        bb_review_dir,
                        "bb-review",
                        "cocoindex",
                        "serve",
                        repo_name,
                    ],
                    "env": {
                        "COCOINDEX_DATABASE_URL": config.cocoindex.database_url,
                    },
                }
            }
        }
    else:  # filesystem
        click.echo("Using filesystem MCP (no semantic search)")
        return {
            "mcpServers": {
                f"filesystem-{repo_name}": {
                    "command": "npx",
                    "args": ["-y", "@anthropic-ai/mcp-filesystem", str(repo_path)],
                }
            }
        }


def _build_opencode_config(
    repo_name: str,
    bb_review_bin: str,
    config,
    template: str,
    repo_path: Path,
) -> dict:
    """Build opencode.json content for OpenCode."""
    if template == "local":
        click.echo("Using local CocoIndex MCP server")
        click.echo("Make sure you've indexed the repo: bb-review cocoindex index " + repo_name)
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": "openrouter/google/gemini-3-pro-preview",
            "permission": {"edit": "deny", "bash": "deny"},
            "mcp": {
                repo_name: {
                    "type": "local",
                    "command": [bb_review_bin, "cocoindex", "serve", repo_name],
                    "environment": {
                        "COCOINDEX_DATABASE_URL": config.cocoindex.database_url,
                    },
                    "enabled": True,
                }
            },
        }
    else:  # filesystem
        click.echo("Using filesystem MCP (no semantic search)")
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": "openrouter/google/gemini-3-pro-preview",
            "permission": {"edit": "deny", "bash": "deny"},
            "mcp": {
                repo_name: {
                    "type": "local",
                    "command": ["npx", "-y", "@anthropic-ai/mcp-filesystem", str(repo_path)],
                    "enabled": True,
                }
            },
        }


@cocoindex.command("db")
@click.argument("action", type=click.Choice(["start", "stop", "status"]))
@click.pass_context
def cocoindex_db(ctx: click.Context, action: str) -> None:
    """Manage CocoIndex PostgreSQL database container.

    Commands:
        start  - Start the PostgreSQL+pgvector container
        stop   - Stop the container
        status - Show container status

    Example:
        bb-review cocoindex db start
        bb-review cocoindex db status
    """
    script_path = Path(__file__).parent.parent.parent / "scripts" / "setup-cocoindex-db.sh"
    if not script_path.exists():
        click.echo(f"Error: Database script not found: {script_path}", err=True)
        sys.exit(1)

    try:
        result = subprocess.run([str(script_path), action])
        sys.exit(result.returncode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cocoindex.command("index")
@click.argument("repo_name")
@click.option("--timeout", default=3600, help="Timeout in seconds (default: 3600)")
@click.option("--clear", is_flag=True, help="Clear existing index before re-indexing")
@click.pass_context
def cocoindex_index(ctx: click.Context, repo_name: str, timeout: int, clear: bool) -> None:
    """Index a repository for semantic code search.

    Uses local sentence-transformers for embeddings (no API calls, no rate limits).
    First run downloads the model, subsequent runs are fast.

    Requires:
        - PostgreSQL with pgvector running (bb-review cocoindex db start)

    Example:
        bb-review cocoindex index te-dev
        bb-review cocoindex index te-dev --clear  # Re-index from scratch
    """
    from ..indexing import CodebaseIndexer, IndexConfig

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    # Get repo config
    repo_config = config.get_repo_config_by_name(repo_name)
    if repo_config is None:
        click.echo(f"Error: Repository '{repo_name}' not found in config", err=True)
        click.echo("Available repositories:")
        for repo in config.repositories:
            click.echo(f"  - {repo.name}")
        sys.exit(1)

    repo_path = Path(repo_config.local_path).expanduser()
    if not repo_path.exists():
        click.echo(f"Error: Repository not cloned at {repo_path}", err=True)
        click.echo(f"Run: bb-review repos sync {repo_name}")
        sys.exit(1)

    # Get embedding model from config
    embedding_model = config.cocoindex.embedding_model

    click.echo(f"Indexing repository: {repo_name}")
    click.echo(f"Path: {repo_path}")
    click.echo(f"Database: {config.cocoindex.database_url}")
    click.echo(f"Embedding model: {embedding_model}")
    click.echo()

    click.echo("Using local sentence-transformers (no API calls, no rate limits)")
    click.echo("First run may download the model (~90MB for MiniLM)")
    click.echo()

    click.echo("Starting indexing (this may take several minutes for large repos)...")
    click.echo()

    start_time = time.time()

    try:
        # Create indexer
        indexer = CodebaseIndexer(config.cocoindex.database_url)

        # Create index config
        index_config = IndexConfig(
            repo_name=repo_name,
            repo_path=str(repo_path),
            embedding_model=embedding_model,
            chunk_size=config.cocoindex.chunk_size,
            chunk_overlap=config.cocoindex.chunk_overlap,
            included_patterns=config.cocoindex.included_patterns,
            excluded_patterns=config.cocoindex.excluded_patterns,
        )

        # Run indexing
        result = indexer.index_repo(index_config, clear=clear)

        elapsed = time.time() - start_time

        click.echo()
        click.echo(f"Indexing completed in {elapsed:.1f} seconds")
        click.echo(f"  Status: {result.status}")
        click.echo(f"  Files indexed: {result.file_count}")
        click.echo(f"  Chunks created: {result.chunk_count}")
        if result.message:
            click.echo(f"  Message: {result.message}")
        click.echo()
        click.echo("Check full status with: bb-review cocoindex status-db")

        indexer.close()

    except ImportError as e:
        click.echo(f"Error: Missing dependencies: {e}", err=True)
        click.echo("Install with: uv pip install cocoindex sentence-transformers")
        sys.exit(1)
    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        click.echo()
        click.echo(f"Indexing interrupted after {elapsed:.1f} seconds")
        click.echo("Partial progress may have been saved. Resume by running again.")
        sys.exit(130)
    except Exception as e:
        elapsed = time.time() - start_time
        click.echo()
        click.echo(f"Error during indexing after {elapsed:.1f} seconds: {e}", err=True)
        click.echo()
        click.echo("Common issues:")
        click.echo("  - Database connection: Ensure PostgreSQL is running (bb-review cocoindex db start)")
        click.echo("  - Model download: Ensure internet connection for first run")
        sys.exit(1)


@cocoindex.command("status-db")
@click.pass_context
def cocoindex_status_db(ctx: click.Context) -> None:
    """Show indexing status from the database.

    Displays the status of all indexed repositories including
    file count and chunk count.

    Example:
        bb-review cocoindex status-db
    """
    from ..indexing import CodebaseIndexer

    try:
        config = get_config(ctx)
        db_url = config.cocoindex.database_url
    except FileNotFoundError:
        db_url = "postgresql://cocoindex:cocoindex@localhost:5432/cocoindex"

    try:
        indexer = CodebaseIndexer(db_url)
        status = indexer.get_status()

        if not status:
            click.echo("No indexed repositories found.")
            click.echo()
            click.echo("To index a repository, run:")
            click.echo("  bb-review cocoindex index <repo-name>")
        else:
            click.echo("CocoIndex Repository Status:")
            click.echo()
            click.echo(f"{'Repository':<20} {'Files':>10} {'Chunks':>10}")
            click.echo("-" * 42)
            for item in status:
                click.echo(f"{item['repo']:<20} {item['file_count']:>10} {item['chunk_count']:>10}")
            click.echo()

        indexer.close()

    except Exception as e:
        click.echo(f"Error: Could not connect to database: {e}", err=True)
        click.echo()
        click.echo("Make sure PostgreSQL is running:")
        click.echo("  bb-review cocoindex db start")
        sys.exit(1)


@cocoindex.command("serve")
@click.argument("repo_name")
@click.option("--model", "-m", default=None, help="Embedding model (default: all-MiniLM-L6-v2)")
@click.option(
    "--log-file",
    "-l",
    default=None,
    help="Log file path (default: ~/.bb_review/mcp-{repo}.log, '' to disable)",
)
@click.pass_context
def cocoindex_serve(ctx: click.Context, repo_name: str, model: str, log_file: str) -> None:
    """Start an MCP server for semantic code search.

    This starts an MCP server that OpenCode can connect to for
    semantic code search using the indexed repository.

    The server uses stdio transport (standard for MCP).
    Logs go to stderr and to ~/.bb_review/mcp-{repo_name}.log by default.

    Environment variables:
        COCOINDEX_DATABASE_URL - PostgreSQL connection URL (required if no config)

    Example:
        bb-review cocoindex serve te-dev

        # Watch logs in another terminal:
        tail -f ~/.bb_review/mcp-te-dev.log

    To use with OpenCode, add to opencode.json:
        {
          "mcp": {
            "te-dev": {
              "type": "local",
              "command": ["bb-review", "cocoindex", "serve", "te-dev"],
              "environment": {
                "COCOINDEX_DATABASE_URL": "postgresql://..."
              }
            }
          }
        }
    """
    # Try to get config, but don't require it
    embedding_model = model or "sentence-transformers/all-MiniLM-L6-v2"
    db_url = os.environ.get("COCOINDEX_DATABASE_URL")

    try:
        config = get_config(ctx)
        if not db_url:
            db_url = config.cocoindex.database_url
        if not model:
            embedding_model = config.cocoindex.embedding_model
    except FileNotFoundError:
        # Config not found - use env vars and defaults
        if not db_url:
            # Try default
            db_url = "postgresql://cocoindex:cocoindex@localhost:5432/cocoindex"

    # Set database URL in environment for the MCP server
    os.environ["COCOINDEX_DATABASE_URL"] = db_url

    # Run the MCP server
    from ..indexing import run_server

    run_server(repo_name=repo_name, embedding_model=embedding_model, log_file=log_file)

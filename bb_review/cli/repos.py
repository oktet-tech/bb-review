"""Repository management commands for BB Review CLI."""

import shutil
import sys
from pathlib import Path
from typing import Optional

import click

from ..guidelines import create_example_guidelines
from ..git import RepoManager, RepoManagerError
from . import main, get_config


@main.group()
def repos():
    """Repository management commands."""
    pass


@repos.command("list")
@click.pass_context
def repos_list(ctx: click.Context) -> None:
    """List configured repositories."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    if not config.repositories:
        click.echo("No repositories configured")
        return

    repo_manager = RepoManager(config.get_all_repos())
    repos = repo_manager.list_repos()

    click.echo("Configured Repositories")
    click.echo("=" * 60)
    
    for repo in repos:
        status = "✓ cloned" if repo["exists"] else "✗ not cloned"
        click.echo(f"\n{repo['name']} ({status})")
        click.echo(f"  RB name: {repo['rb_name']}")
        click.echo(f"  Path: {repo['local_path']}")
        click.echo(f"  Remote: {repo['remote_url']}")
        if repo["exists"]:
            click.echo(f"  Branch: {repo.get('current_branch', 'unknown')}")
            click.echo(f"  Commit: {repo.get('current_commit', 'unknown')}")


@repos.command("sync")
@click.argument("repo_name", required=False)
@click.pass_context
def repos_sync(ctx: click.Context, repo_name: Optional[str]) -> None:
    """Fetch/sync repositories."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())

    if repo_name:
        click.echo(f"Syncing {repo_name}...")
        try:
            repo_manager.ensure_clone(repo_name)
            repo_manager.fetch_all(repo_name)
            click.echo(f"  ✓ {repo_name} synced")
        except RepoManagerError as e:
            click.echo(f"  ✗ {repo_name}: {e}", err=True)
            sys.exit(1)
    else:
        click.echo("Syncing all repositories...")
        results = repo_manager.fetch_all_repos()
        
        for name, success in results.items():
            status = "✓" if success else "✗"
            click.echo(f"  {status} {name}")


@repos.command("init-guidelines")
@click.argument("repo_name")
@click.option("--force", is_flag=True, help="Overwrite existing file")
@click.pass_context
def repos_init_guidelines(ctx: click.Context, repo_name: str, force: bool) -> None:
    """Copy guidelines from guides/ folder to repository cache.
    
    Looks for guides/{repo_name}.ai-review.yaml and copies it to the
    repository's local path. Creates a generic template if no guide exists.
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
    
    target_path = repo_path / ".ai-review.yaml"
    
    # Check if target already exists
    if target_path.exists() and not force:
        click.echo(
            f"Guidelines file already exists: {target_path}\n"
            "Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)
    
    # Look for repo-specific guide in guides/ folder
    guides_dir = Path(__file__).parent.parent.parent / "guides"
    guide_file = guides_dir / f"{repo_name}.ai-review.yaml"
    
    if guide_file.exists():
        # Copy from guides/
        shutil.copy(guide_file, target_path)
        click.echo(f"Copied guide from: {guide_file}")
        click.echo(f"Created: {target_path}")
    else:
        # Create generic template
        path = create_example_guidelines(repo_path, overwrite=force)
        click.echo(f"No guide found for '{repo_name}' in {guides_dir}")
        click.echo(f"Created generic template: {path}")


@repos.command("mcp-setup")
@click.argument("repo_name")
@click.option("--force", is_flag=True, help="Overwrite existing files")
@click.pass_context
def repos_mcp_setup(ctx: click.Context, repo_name: str, force: bool) -> None:
    """Setup OpenCode MCP configuration for te-test-suite repositories.
    
    For repositories with repo_type: te-test-suite, this command copies:
    - opencode/te-ts-reviewer -> {repo}/.opencode/agent/api-reviewer.md
    - opencode/te-review-command -> {repo}/.opencode/command/review-api.md
    - opencode/ts-te-mcp -> {repo}/opencode.json
    
    This enables OpenCode to use the ol-te-dev MCP server for API review.
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())
    
    # Get repo config to check type
    repo_config = repo_manager.get_repo(repo_name)
    
    if repo_config.repo_type != "te-test-suite":
        click.echo(
            f"Error: Repository '{repo_name}' has type '{repo_config.repo_type}', "
            f"expected 'te-test-suite'.\n"
            f"Set repo_type: te-test-suite in config.yaml to enable MCP setup.",
            err=True,
        )
        sys.exit(1)
    
    try:
        repo_path = repo_manager.get_local_path(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    
    if not repo_path.exists():
        click.echo(f"Error: Repository not cloned. Run 'bb-review repos sync {repo_name}' first.", err=True)
        sys.exit(1)
    
    # Source files from opencode/ directory
    opencode_src_dir = Path(__file__).parent.parent.parent / "opencode"
    reviewer_src = opencode_src_dir / "te-ts-reviewer"
    command_src = opencode_src_dir / "te-review-command"
    mcp_config_src = opencode_src_dir / "ts-te-mcp"
    
    # Check source files exist
    if not reviewer_src.exists():
        click.echo(f"Error: Source file not found: {reviewer_src}", err=True)
        sys.exit(1)
    if not command_src.exists():
        click.echo(f"Error: Source file not found: {command_src}", err=True)
        sys.exit(1)
    if not mcp_config_src.exists():
        click.echo(f"Error: Source file not found: {mcp_config_src}", err=True)
        sys.exit(1)
    
    # Target paths
    reviewer_target = repo_path / ".opencode" / "agent" / "api-reviewer.md"
    command_target = repo_path / ".opencode" / "command" / "review-api.md"
    mcp_config_target = repo_path / "opencode.json"
    
    # Check if targets exist
    if reviewer_target.exists() and not force:
        click.echo(f"Error: {reviewer_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    if command_target.exists() and not force:
        click.echo(f"Error: {command_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    if mcp_config_target.exists() and not force:
        click.echo(f"Error: {mcp_config_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    
    # Create directories and copy files
    reviewer_target.parent.mkdir(parents=True, exist_ok=True)
    command_target.parent.mkdir(parents=True, exist_ok=True)
    
    shutil.copy(reviewer_src, reviewer_target)
    click.echo(f"Created: {reviewer_target}")
    
    shutil.copy(command_src, command_target)
    click.echo(f"Created: {command_target}")
    
    shutil.copy(mcp_config_src, mcp_config_target)
    click.echo(f"Created: {mcp_config_target}")
    
    click.echo(f"\nMCP setup complete for '{repo_name}'")
    click.echo("OpenCode can now use the ol-te-dev MCP server for API review.")

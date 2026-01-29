"""Utility commands for BB Review CLI."""

import sys
from pathlib import Path
from typing import Optional

import click

from . import main, get_config


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize BB Review configuration."""
    config_path = Path.cwd() / "config.yaml"
    example_path = Path(__file__).parent.parent.parent / "config.example.yaml"
    
    if config_path.exists():
        click.echo(f"Config file already exists: {config_path}")
        if not click.confirm("Overwrite?"):
            return

    # Copy example config
    if example_path.exists():
        config_path.write_text(example_path.read_text())
    else:
        # Inline minimal config
        config_path.write_text("""\
# BB Review Configuration
reviewboard:
  url: "https://your-reviewboard-server.com"
  api_token: "${RB_API_TOKEN}"
  bot_username: "ai-reviewer"

llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "${ANTHROPIC_API_KEY}"

repositories: []

polling:
  interval_seconds: 300
  max_reviews_per_cycle: 10

database:
  path: "~/.bb_review/state.db"

defaults:
  focus:
    - bugs
    - security
  severity_threshold: "medium"
  auto_ship_it: false
""")

    click.echo(f"Created config file: {config_path}")
    click.echo("\nNext steps:")
    click.echo("1. Edit config.yaml with your Review Board URL and API token")
    click.echo("2. Add your repositories to the 'repositories' section")
    click.echo("3. Set ANTHROPIC_API_KEY environment variable")
    click.echo("4. Run 'bb-review repos sync' to clone repositories")


@main.command("encrypt-password")
@click.option("--token", "-t", help="Token to use as encryption key (defaults to api_token from config)")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output file path (defaults to password_file from config or ~/.bb_review/password.enc)")
@click.pass_context
def encrypt_password_cmd(ctx: click.Context, token: Optional[str], output: Optional[Path]) -> None:
    """Encrypt your Review Board password for secure storage.
    
    The password will be encrypted using the api_token from your config
    (or --token if specified). The encrypted file is saved to password_file
    from config (or --output if specified).
    
    Example:
    
        # Uses api_token and password_file from config.yaml
        bb-review encrypt-password
        
        # Or specify explicitly
        bb-review encrypt-password --token "your-token" --output ~/.bb_review/password.enc
    """
    from ..crypto import encrypt_password_to_file
    
    # Try to get token from config if not provided
    if not token:
        try:
            config = get_config(ctx)
            token = config.reviewboard.api_token or config.reviewboard.encryption_token
            if not token:
                click.echo("Error: No --token provided and no api_token in config", err=True)
                sys.exit(1)
            click.echo(f"Using api_token from config ({token[:20]}...)")
        except (FileNotFoundError, Exception) as e:
            click.echo(f"Error: No --token provided and couldn't load config: {e}", err=True)
            sys.exit(1)
    
    # Try to get output path from config if not provided
    if not output:
        try:
            config = get_config(ctx)
            if config.reviewboard.password_file:
                output = Path(config.reviewboard.password_file)
                click.echo(f"Using password_file from config: {output}")
            else:
                output = Path("~/.bb_review/password.enc")
        except (FileNotFoundError, Exception):
            output = Path("~/.bb_review/password.enc")
    
    password = click.prompt("Enter your Review Board password", hide_input=True)
    confirm = click.prompt("Confirm password", hide_input=True)
    
    if password != confirm:
        click.echo("Passwords don't match!", err=True)
        sys.exit(1)
    
    output_path = output.expanduser()
    
    try:
        encrypt_password_to_file(password, token, output_path)
        click.echo(f"\nPassword encrypted and saved to: {output_path}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

"""Command-line interface for BB Review."""

import logging
from pathlib import Path

import click

from .. import __version__
from ..config import Config, ensure_directories, load_config, set_config


logger = logging.getLogger(__name__)


def setup_logging(level: str, log_file: Path | None = None) -> None:
    """Configure logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config file",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """BB Review - AI-powered code review for Review Board."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose

    # Don't load config here - let commands that need it load it themselves
    # This allows commands like encrypt-password to work without a config
    setup_logging("DEBUG" if verbose else "INFO")
    ctx.obj["config"] = None


def get_config(ctx: click.Context) -> Config:
    """Load and return config, caching it in context."""
    if ctx.obj.get("config") is not None:
        return ctx.obj["config"]

    config_path = ctx.obj.get("config_path")
    verbose = ctx.obj.get("verbose", False)

    cfg = load_config(config_path)
    set_config(cfg)
    ensure_directories(cfg)

    log_level = "DEBUG" if verbose else cfg.logging.level
    setup_logging(log_level, cfg.logging.resolved_file)

    ctx.obj["config"] = cfg
    return cfg


# Import and register subcommands
from . import (
    analyze,  # noqa: E402, F401
    cocoindex,  # noqa: E402, F401
    db,  # noqa: E402, F401
    interactive,  # noqa: E402, F401
    opencode,  # noqa: E402, F401
    poll,  # noqa: E402, F401
    repos,  # noqa: E402, F401
    submit,  # noqa: E402, F401
    utils,  # noqa: E402, F401
)

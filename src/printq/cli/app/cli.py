"""Application control commands."""

from logging import DEBUG, getLogger
from pathlib import Path

import click
from omegaconf import OmegaConf
from rich.panel import Panel

from printq.cli import console
from printq.utils.hydra import load_hydra_config

logger = getLogger(__name__)


@click.group(name="app")
@click.pass_context
def app_commands(ctx):
    """Application control utilities"""
    ctx.ensure_object(dict)


@app_commands.command(name="start")
@click.option("--config-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path), default="./configs")
@click.option("--config-name", type=str, default="config")
@click.argument("config_overrides", nargs=-1)
def start(config_dir: Path, config_name: str, config_overrides: list[str]):
    """Start the application"""
    logger.info("Starting the application")

    config_file = config_dir / f"{config_name}.yaml"
    logger.info(
        "Reading config from %s (overrides: %s)",
        config_file.resolve().as_posix(),
        config_overrides,
    )

    config = load_hydra_config(config_dir, config_name=config_name, config_overrides=config_overrides)

    if logger.isEnabledFor(DEBUG):
        console.print(OmegaConf.to_yaml(config))

    console.print(
        Panel.fit(
            "[bold magenta]Converting Demonstrations to LeRobot Format[/bold magenta]\n"
            f"Config File: {config_file.resolve().as_posix()}\n"
            f"Config Overrides: {' '.join(config_overrides) if config_overrides else 'none'}",
            border_style="magenta",
        )
    )

import logging

import click

from printq import __version__


class ClickHandler(logging.Handler):
    """Logging handler that emits records via ``click.echo`` with colored levels."""

    _level_colors = {
        logging.DEBUG: "cyan",
        logging.INFO: "green",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "bright_red",
    }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            color = self._level_colors.get(record.levelno)
            click.echo(
                click.style(msg, fg=color),
                err=record.levelno >= logging.WARNING,
            )
        except Exception:
            self.handleError(record)


def setup_logging(log_level: str) -> None:
    """Setup structured logging for CLI.

    Args:
        log_level: Logging level (debug, info, warning, error)
    """
    handler = ClickHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level.upper())



@click.group()
@click.option(
    "--log-level",
    type=click.Choice(["debug", "info", "warning", "error"], case_sensitive=False),
    default="info",
    help="Set logging level",
)
@click.version_option(__version__, prog_name="nimble-brain")
@click.pass_context
def cli(ctx, log_level):
    """Nimble Brain"""
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level
    setup_logging(log_level)


def register_commands(group: click.Group) -> None:
    """Register all commands under the given group."""
    from printq.cli.app import app_commands
    from printq.cli.arm import arm_commands
    from printq.cli.camera import camera_commands
    group.add_command(arm_commands)
    group.add_command(app_commands)
    group.add_command(camera_commands)


register_commands(cli)

if __name__ == "__main__":
    cli(obj={})
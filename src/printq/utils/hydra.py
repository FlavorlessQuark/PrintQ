"""Hydra utilities."""

from pathlib import Path

from hydra import compose, initialize_config_dir
from hydra.core.hydra_config import HydraConfig
from hydra.errors import HydraException
from omegaconf import DictConfig
from omegaconf.errors import OmegaConfBaseException


def load_hydra_config(
    config_dir: Path, config_name: str = "config", config_overrides: list[str] | None = None
) -> DictConfig:
    """Load Hydra configuration from config files.

    Args:
        config_dir: Path to config directory
        config_name: Name of the config file (default: "config")
        config_overrides: List of config overrides

    Returns:
        Hydra configuration

    Raises:
        SystemExit: If error loading config
    """

    try:
        initialize_config_dir(version_base=None, config_dir=config_dir.resolve().as_posix())
        cfg: DictConfig = compose(config_name=config_name, return_hydra_config=True, overrides=config_overrides)
        HydraConfig.instance().set_config(cfg)
        return cfg
    except (HydraException, OmegaConfBaseException) as ex:
        raise SystemExit(f"Failed to load Hydra configuration: {ex}") from ex

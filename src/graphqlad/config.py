"""Configuration resolution for graphqlAD.

The effective :class:`msad.DomainConfig` is built in two layers:

1. Optionally load an msad TOML config file (``~/.msad.toml`` by default, or
   the path in ``MSAD_CONFIG_FILE``) and pick a domain.
2. Apply ``MSAD_*`` environment-variable overrides on top. Environment values
   always win, so credentials (and any connection parameter) can be injected
   at runtime without ever being written to the TOML file.

Supported environment variables
--------------------------------
=========================  =================================================
Variable                   Effect
=========================  =================================================
``MSAD_CONFIG_FILE``       Path to the msad TOML file (default ``~/.msad.toml``).
``MSAD_DOMAIN``            Which ``[domains.<name>]`` section to use.
``MSAD_HOST``              Override ``host``.
``MSAD_BASE``              Override ``base`` (search base DN).
``MSAD_PORT``              Override ``port`` (int).
``MSAD_USE_SSL``           Override ``use_ssl`` (true/false/1/0/yes/no).
``MSAD_USER``              Override bind ``user`` (enables user/password bind).
``MSAD_PASSWORD``          Override bind ``password``.
``MSAD_QUERY_TIMEOUT``     Override ``query_timeout`` (int seconds).
=========================  =================================================

If no TOML file exists, the configuration is built entirely from the
environment: at minimum ``MSAD_HOST`` and ``MSAD_BASE`` must be set.
"""

from __future__ import annotations

import os
from pathlib import Path

from msad import DomainConfig, MsadConfigError, load_config

#: Env var holding the path to the msad TOML config file.
ENV_CONFIG_FILE = "MSAD_CONFIG_FILE"
#: Env var selecting which domain section to use.
ENV_DOMAIN = "MSAD_DOMAIN"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Raised when the effective configuration cannot be resolved."""


def _parse_bool(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name}: expected a boolean, got {value!r}")


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name}: expected an integer, got {value!r}") from exc


def _base_from_toml(env: dict[str, str]) -> dict[str, object]:
    """Load the TOML file (if any) and return the chosen domain as a dict.

    Returns an empty dict when no config file is present, so the configuration
    can be built purely from environment variables.
    """
    config_file = env.get(ENV_CONFIG_FILE)
    domain = env.get(ENV_DOMAIN)

    path = Path(config_file) if config_file else None
    # Only attempt to load when a file is explicitly given or the default
    # exists; otherwise fall back to a pure-env configuration.
    from msad import DEFAULT_CONFIG_PATH

    if path is None and DEFAULT_CONFIG_PATH.is_file():
        path = DEFAULT_CONFIG_PATH

    if path is None:
        return {}

    try:
        config = load_config(path)
        domain_config = config.get_domain(domain)
    except MsadConfigError as exc:
        raise ConfigError(str(exc)) from exc

    return domain_config.model_dump()


def _apply_env_overrides(data: dict[str, object], env: dict[str, str]) -> dict[str, object]:
    """Overlay ``MSAD_*`` environment overrides onto a config dict."""
    if (host := env.get("MSAD_HOST")) is not None:
        data["host"] = host
    if (base := env.get("MSAD_BASE")) is not None:
        data["base"] = base
    if (port := env.get("MSAD_PORT")) is not None:
        data["port"] = _parse_int(port, "MSAD_PORT")
    if (use_ssl := env.get("MSAD_USE_SSL")) is not None:
        data["use_ssl"] = _parse_bool(use_ssl, "MSAD_USE_SSL")
    if (user := env.get("MSAD_USER")) is not None:
        data["user"] = user
    if (password := env.get("MSAD_PASSWORD")) is not None:
        data["password"] = password
    if (timeout := env.get("MSAD_QUERY_TIMEOUT")) is not None:
        data["query_timeout"] = _parse_int(timeout, "MSAD_QUERY_TIMEOUT")
    return data


def resolve_domain_config(env: dict[str, str] | None = None) -> DomainConfig:
    """Resolve the effective :class:`msad.DomainConfig`.

    Args:
        env: environment mapping to read from (defaults to ``os.environ``).
            Injectable for testing.

    Returns:
        A validated :class:`msad.DomainConfig`.

    Raises:
        ConfigError: if required fields are missing or values are invalid.
    """
    environ = dict(os.environ if env is None else env)

    data = _base_from_toml(environ)
    data = _apply_env_overrides(data, environ)

    missing = [k for k in ("host", "base") if not data.get(k)]
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Provide them via the msad TOML file or the MSAD_HOST / MSAD_BASE "
            "environment variables."
        )

    try:
        return DomainConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"Invalid configuration: {exc}") from exc

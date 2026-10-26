"""Configuration resolution for graphqlAD.

This is a thin re-export: the actual TOML + ``MSAD_*`` environment-variable
resolution logic lives in ``msad.resolve_domain_config()`` so it is shared
with restAD and mcpAD instead of being reimplemented in each repo. See
msad's own documentation for the full list of supported ``MSAD_*``
variables.
"""

from __future__ import annotations

from msad import MsadConfigError as ConfigError
from msad import resolve_domain_config

__all__ = ["ConfigError", "resolve_domain_config"]

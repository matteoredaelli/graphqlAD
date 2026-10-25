"""graphqlAD: a read-only GraphQL interface over the msad library."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Generated at build time by hatch-vcs from the Git tag.
    from ._version import __version__  # type: ignore[import-not-found,unused-ignore]
except ImportError:  # pragma: no cover - only when running from an unbuilt tree
    try:
        __version__ = version("graphqlad")
    except PackageNotFoundError:
        __version__ = "0.0.0.dev0"

__all__ = ["__version__"]

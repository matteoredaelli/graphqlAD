# graphqlAD - Read-only GraphQL interface over the msad Active Directory library
# Copyright (C) 2026 - matteo.redaelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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

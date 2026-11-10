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

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

"""Console entry point: run the graphqlAD API with uvicorn.

Usage:
    graphqlad                      # listen on 127.0.0.1:8000
    GRAPHQLAD_HOST=0.0.0.0 GRAPHQLAD_PORT=9000 graphqlad

Host/port are read from GRAPHQLAD_HOST / GRAPHQLAD_PORT (not the MSAD_* vars,
which configure the directory connection).
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("GRAPHQLAD_HOST", "127.0.0.1")
    port = int(os.environ.get("GRAPHQLAD_PORT", "8000"))
    uvicorn.run("graphqlad.app:app", host=host, port=port, factory=False)


if __name__ == "__main__":
    main()

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

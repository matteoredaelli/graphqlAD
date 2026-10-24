"""GraphQL execution context.

The LDAP connection and resolved configuration are created once during the
application lifespan (see :mod:`graphqlad.app`) and stored on ``app.state``.
The context getter exposes them to resolvers, mirroring restAD's FastAPI
dependencies.

The context also owns a **per-request** DataLoader for resolving DNs via
``msad.get_by_dn``. It provides batching (concurrent ``load`` calls in the same
event-loop tick are grouped into one ``load_fn`` invocation) and within-request
caching (the same DN is resolved only once per request). It is intentionally
*not* shared across requests, so results never go stale between requests.
"""

from __future__ import annotations

import asyncio
from typing import Any

from msad import DomainConfig
from msad.types import LdapConnection
from strawberry.dataloader import DataLoader
from strawberry.fastapi import BaseContext


class ConnectionUnavailableError(RuntimeError):
    """Raised by resolvers when no bound LDAP connection is available.

    Happens when the directory was unreachable at startup and lazy-connect is
    enabled; GraphQL surfaces this as an error entry in the response.
    """


class Context(BaseContext):
    """Per-request GraphQL context.

    Inherits from strawberry's :class:`BaseContext` (required by
    ``GraphQLRouter``). ``conn`` is ``None`` when the directory could not be
    reached at startup (lazy mode); ``config`` is always present once the
    lifespan has run.
    """

    def __init__(
        self,
        config: DomainConfig | None = None,
        conn: LdapConnection | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.conn = conn
        self._dn_loader: DataLoader[str, dict[str, Any] | None] | None = None

    def require_conn(self) -> LdapConnection:
        """Return the bound connection or raise if it is unavailable."""
        if self.conn is None:
            raise ConnectionUnavailableError(
                "LDAP connection is not available (directory unreachable at startup)."
            )
        return self.conn

    def require_config(self) -> DomainConfig:
        """Return the resolved config or raise if it is missing."""
        if self.config is None:  # pragma: no cover - should not happen post-lifespan
            raise ConnectionUnavailableError("Configuration not initialized.")
        return self.config

    @property
    def base(self) -> str:
        """The configured search base DN."""
        return self.require_config().base

    @property
    def dn_loader(self) -> DataLoader[str, dict[str, Any] | None]:
        """A per-request DataLoader resolving DNs to msad entries.

        Lazily created on first use (so a request that never expands a DN pays
        nothing). Deduplicates and caches resolved DNs for the lifetime of this
        request only.
        """
        if self._dn_loader is None:
            self._dn_loader = DataLoader(load_fn=self._load_dns, cache=True)
        return self._dn_loader

    async def _load_dns(self, dns: list[str]) -> list[dict[str, Any] | None]:
        """Batch function: resolve each DN via msad.get_by_dn (one per DN).

        ``msad.get_by_dn`` resolves a single DN (using it as the search base),
        so this issues one lookup per *distinct* DN requested in the batch
        (the loader already deduplicates repeats). The synchronous msad calls
        run in a worker thread so the event loop is not blocked. The returned
        list matches the input order, as the DataLoader contract requires.
        """
        from msad import get_by_dn

        conn = self.require_conn()

        def _resolve_all() -> list[dict[str, Any] | None]:
            return [get_by_dn(conn, dn) for dn in dns]

        return await asyncio.to_thread(_resolve_all)

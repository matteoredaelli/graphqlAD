"""GraphQL execution context.

The LDAP connection and resolved configuration are created once during the
application lifespan (see :mod:`graphqlad.app`) and stored on ``app.state``.
The context getter exposes them to resolvers, mirroring restAD's FastAPI
dependencies.
"""

from __future__ import annotations

from msad import DomainConfig
from msad.types import LdapConnection
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

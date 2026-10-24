"""FastAPI application factory and lifespan for graphqlAD.

At startup the lifespan resolves the effective :class:`msad.DomainConfig`
(TOML + ``MSAD_*`` env overrides), opens a single bound LDAP connection and
stores both on ``app.state``. At shutdown it unbinds the connection.

By default a failure to connect at startup is fatal. Set
``GRAPHQLAD_LAZY_CONNECT=true`` to start anyway (the ``health`` query still
works and reports the error; data queries return a GraphQL error until a
connection is available).

The GraphiQL in-browser IDE is served at ``/graphql`` by default; disable it
with ``GRAPHQLAD_GRAPHIQL=false``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from msad import connect
from strawberry.fastapi import GraphQLRouter

from .context import Context
from .schema import schema

logger = logging.getLogger("graphqlad")

_TRUE = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve config, open the LDAP connection, and tear it down on exit."""
    # Imported lazily so importing the module never triggers config resolution.
    from .config import resolve_domain_config

    lazy = _env_flag("GRAPHQLAD_LAZY_CONNECT")

    config = resolve_domain_config()
    app.state.config = config
    app.state.conn = None

    try:
        app.state.conn = connect(config)
        auth = "kerberos" if config.uses_kerberos else config.user
        logger.info("Connected to the directory as %s", auth)
    except Exception as exc:
        # MsadConnectionError on bind/connect failure, but also e.g. environment
        # issues raised deeper in ldap3. In lazy mode we start anyway; otherwise
        # startup fails loudly.
        if not lazy:
            raise
        logger.warning("Startup connection failed (lazy mode, continuing): %s", exc)

    try:
        yield
    finally:
        conn = app.state.conn
        if conn is not None:
            try:
                conn.unbind()
            except Exception:  # best-effort teardown
                logger.debug("unbind on shutdown failed", exc_info=True)


async def get_context(request: Request) -> Context:
    """Build the per-request GraphQL context from app.state."""
    return Context(
        config=getattr(request.app.state, "config", None),
        conn=getattr(request.app.state, "conn", None),
    )


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    GraphiQL (the in-browser GraphQL IDE at ``/graphql``) is enabled by
    default. Set ``GRAPHQLAD_GRAPHIQL=false`` to disable it (the GraphQL
    endpoint itself keeps serving POST queries).
    """
    graphiql = _env_flag("GRAPHQLAD_GRAPHIQL", default=True)

    app = FastAPI(
        title="graphqlAD",
        version="0.1.0",
        summary="Read-only GraphQL interface over Active Directory (msad).",
        lifespan=lifespan,
    )

    graphql_app: GraphQLRouter = GraphQLRouter(
        schema,
        context_getter=get_context,
        graphql_ide="graphiql" if graphiql else None,
    )
    app.include_router(graphql_app, prefix="/graphql")

    @app.get("/", tags=["meta"], summary="Service metadata")
    def root() -> dict[str, str]:
        return {
            "service": "graphqlad",
            "version": "0.1.0",
            "graphql": "/graphql",
            "graphiql": "/graphql" if graphiql else "disabled",
        }

    return app


# Module-level app for `uvicorn graphqlad.app:app` and ASGI servers.
app = create_app()


__all__ = ["app", "create_app", "lifespan", "get_context"]

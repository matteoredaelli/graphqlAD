"""Smoke tests for graphqlAD that do not require a live Active Directory.

They exercise configuration resolution, the GraphQL schema surface (read-only:
no Mutation type, introspection works) and the lazy-connect startup path where
the directory is unreachable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graphqlad.app import create_app
from graphqlad.config import ConfigError, resolve_domain_config
from graphqlad.schema import schema

# A config that is valid to *build* but points at an unresolvable host, so the
# connection fails fast without contacting a real directory.
_FAKE_ENV = {
    "MSAD_HOST": "dc.invalid.example",
    "MSAD_BASE": "dc=example,dc=com",
    "MSAD_PORT": "636",
    "MSAD_USE_SSL": "true",
    "MSAD_USER": "svc_reader",
    "MSAD_PASSWORD": "dummy",
    "GRAPHQLAD_LAZY_CONNECT": "true",
}


# ----- config -----------------------------------------------------------
def test_env_overrides_win() -> None:
    cfg = resolve_domain_config(_FAKE_ENV)
    assert cfg.host == "dc.invalid.example"
    assert cfg.base == "dc=example,dc=com"
    assert cfg.port == 636
    assert cfg.use_ssl is True
    assert cfg.user == "svc_reader"
    assert cfg.uses_kerberos is False


def test_missing_required_fields_raise() -> None:
    with pytest.raises(ConfigError):
        resolve_domain_config({"MSAD_CONFIG_FILE": "/nonexistent/none.toml"})


def test_kerberos_when_no_credentials() -> None:
    cfg = resolve_domain_config({"MSAD_HOST": "dc.x", "MSAD_BASE": "dc=x"})
    assert cfg.uses_kerberos is True


# ----- schema -----------------------------------------------------------
def test_schema_builds_and_is_read_only() -> None:
    # A Query type exists; there is no Mutation / Subscription type.
    assert schema.query is not None
    assert schema.mutation is None
    assert schema.subscription is None


def test_introspection_lists_expected_fields() -> None:
    result = schema.execute_sync(
        "{ __schema { queryType { fields { name } } } }",
    )
    assert result.errors is None
    names = {f["name"] for f in result.data["__schema"]["queryType"]["fields"]}
    # A representative sample across every domain area.
    assert {
        "health",
        "users",
        "user",
        "groups",
        "computers",
        "ous",
        "domainInfo",
        "privilegedGroups",
    } <= names


# ----- app / HTTP -------------------------------------------------------
def test_graphiql_served_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GRAPHQLAD_GRAPHIQL", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/").json()["graphiql"] == "/graphql"
        # GraphiQL responds to an HTML GET on the GraphQL endpoint.
        resp = client.get("/graphql", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


def test_graphiql_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GRAPHQLAD_GRAPHIQL", "false")
    with TestClient(create_app()) as client:
        assert client.get("/").json()["graphiql"] == "disabled"
        resp = client.get("/graphql", headers={"Accept": "text/html"})
        assert resp.status_code in (404, 406)


def test_health_query_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """In lazy mode the health query returns ok=false, not a transport error."""
    for key, value in _FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app()) as client:
        resp = client.post(
            "/graphql",
            json={"query": "{ health { ok target auth error } }"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("errors") is None, body
        health = body["data"]["health"]
        assert health["ok"] is False
        assert health["error"]
        assert health["target"] == "ldaps://dc.invalid.example:636"


def test_data_query_errors_without_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Data resolvers surface a GraphQL error when no connection is available."""
    for key, value in _FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app()) as client:
        resp = client.post(
            "/graphql",
            json={"query": "{ users(limit: 1) { count } }"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("errors"), "expected a GraphQL error without a connection"
        assert body["data"] is None

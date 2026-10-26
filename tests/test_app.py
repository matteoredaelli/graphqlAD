"""Smoke tests for graphqlAD that do not require a live Active Directory.

They exercise configuration resolution, the GraphQL schema surface (read-only:
no Mutation type, introspection works) and the lazy-connect startup path where
the directory is unreachable.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from graphqlad.app import create_app
from graphqlad.config import ConfigError, resolve_domain_config
from graphqlad.schema import schema


def run_query(query: str, ctx: object):
    """Execute a query through the async schema path.

    Needed because virtual-field resolvers are async and drive a DataLoader,
    which requires a running event loop (execute_sync cannot). Works for sync
    resolvers too.
    """
    return asyncio.run(schema.execute(query, context_value=ctx))


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
    cfg = resolve_domain_config(env=_FAKE_ENV)
    assert cfg.host == "dc.invalid.example"
    assert cfg.base == "dc=example,dc=com"
    assert cfg.port == 636
    assert cfg.use_ssl is True
    assert cfg.user == "svc_reader"
    assert cfg.uses_kerberos is False


def test_missing_required_fields_raise() -> None:
    with pytest.raises(ConfigError):
        resolve_domain_config(env={"MSAD_CONFIG_FILE": "/nonexistent/none.toml"})


def test_kerberos_when_no_credentials() -> None:
    cfg = resolve_domain_config(env={"MSAD_HOST": "dc.x", "MSAD_BASE": "dc=x"})
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


# ----- User type / virtual fields --------------------------------------
def test_user_type_has_virtual_manager_field() -> None:
    result = schema.execute_sync(
        '{ __type(name: "User") { fields { name type { kind name ofType { name } } } } }'
    )
    assert result.errors is None
    fields = {f["name"]: f for f in result.data["__type"]["fields"]}
    # Raw LDAP passthrough plus a few typed identity fields.
    assert "raw" in fields
    assert {"dn", "samAccountName", "displayName", "mail"} <= set(fields)
    # The virtual field exists and resolves to a User (recursive expansion).
    assert "managerUser" in fields
    assert fields["managerUser"]["type"]["name"] == "User"


def test_manager_user_resolves_dn_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """managerUser expands raw.manager via get_by_dn, only when selected."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    boss_dn = "CN=Anna Bianchi,OU=Staff,DC=example,DC=com"
    employee = {"sAMAccountName": "mrossi", "displayName": "Mario Rossi", "manager": boss_dn}
    boss = {"sAMAccountName": "abianchi", "displayName": "Anna Bianchi"}

    calls: list[str] = []

    def fake_get_user(conn, base, identifier):  # noqa: ANN001
        return employee

    def fake_get_by_dn(conn, dn, attributes=None):  # noqa: ANN001
        calls.append(dn)
        return boss if dn == boss_dn else None

    monkeypatch.setattr(schema_mod, "get_user", fake_get_user)
    # get_by_dn is imported lazily inside the resolver, so patch it on msad.
    import msad

    monkeypatch.setattr(msad, "get_by_dn", fake_get_by_dn)

    # A context with a non-None (dummy) connection so require_conn() passes.
    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())

    query = """
    {
      user(identifier: "mrossi") {
        samAccountName
        raw
        managerUser { samAccountName displayName }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    user = result.data["user"]
    assert user["samAccountName"] == "mrossi"
    # Raw DN is preserved untouched.
    assert user["raw"]["manager"] == boss_dn
    # Virtual field expanded into an object.
    assert user["managerUser"]["samAccountName"] == "abianchi"
    assert user["managerUser"]["displayName"] == "Anna Bianchi"
    # get_by_dn was called exactly once, with the manager DN.
    assert calls == [boss_dn]


def test_manager_user_not_queried_when_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting only raw fields must not trigger a get_by_dn lookup."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    employee = {"sAMAccountName": "mrossi", "manager": "CN=Boss,DC=x"}

    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: employee)

    import msad

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("get_by_dn must not be called")

    monkeypatch.setattr(msad, "get_by_dn", boom)

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ user(identifier: "mrossi") { samAccountName } }', ctx)
    assert result.errors is None, result.errors
    assert result.data["user"]["samAccountName"] == "mrossi"


def test_manager_user_null_when_no_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    employee = {"sAMAccountName": "ceo"}  # no manager attribute
    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: employee)

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ user(identifier: "ceo") { managerUser { samAccountName } } }', ctx)
    assert result.errors is None, result.errors
    assert result.data["user"]["managerUser"] is None


# ----- Group type / user-typed queries ---------------------------------
def test_user_returning_queries_use_user_type() -> None:
    """Queries that return users expose the typed User/Users, not raw JSON."""
    result = schema.execute_sync(
        "{ __schema { queryType { fields { name type { name ofType { name } } } } } }"
    )
    assert result.errors is None
    qf = {f["name"]: f["type"] for f in result.data["__schema"]["queryType"]["fields"]}

    def tn(t: dict) -> str | None:
        return t.get("name") or (t.get("ofType") or {}).get("name")

    assert tn(qf["user"]) == "User"
    for q in ("users", "inactiveUsers", "groupMembers", "passwordPolicyViolations"):
        assert tn(qf[q]) == "Users", q
    assert tn(qf["group"]) == "Group"
    for q in ("groups", "emptyGroups", "userGroups", "privilegedGroups"):
        assert tn(qf[q]) == "Groups", q


def test_group_type_has_virtual_managed_by() -> None:
    result = schema.execute_sync('{ __type(name: "Group") { fields { name type { name } } } }')
    assert result.errors is None
    fields = {f["name"]: f for f in result.data["__type"]["fields"]}
    assert "raw" in fields
    assert {"dn", "samAccountName", "cn", "displayName"} <= set(fields)
    assert "managedByUser" in fields
    assert fields["managedByUser"]["type"]["name"] == "User"


def test_group_managed_by_user_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Group.managedByUser expands the managedBy DN into a User."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    owner_dn = "CN=Owner,OU=IT,DC=example,DC=com"
    grp = {"cn": "qlik_users", "managedBy": owner_dn}
    owner = {"sAMAccountName": "owner", "displayName": "The Owner"}

    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)
    import msad

    monkeypatch.setattr(
        msad, "get_by_dn", lambda c, dn, attributes=None: owner if dn == owner_dn else None
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      group(identifier: "qlik_users") {
        cn
        raw
        managedByUser { samAccountName displayName }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    g = result.data["group"]
    assert g["cn"] == "qlik_users"
    assert g["raw"]["managedBy"] == owner_dn
    assert g["managedByUser"]["samAccountName"] == "owner"
    assert g["managedByUser"]["displayName"] == "The Owner"


def test_computer_type_has_virtual_managed_by() -> None:
    result = schema.execute_sync('{ __type(name: "Computer") { fields { name type { name } } } }')
    assert result.errors is None
    fields = {f["name"]: f for f in result.data["__type"]["fields"]}
    assert "raw" in fields
    assert {"dn", "samAccountName", "cn", "dnsHostName", "operatingSystem"} <= set(fields)
    assert "managedByUser" in fields
    assert fields["managedByUser"]["type"]["name"] == "User"


def test_computer_managed_by_user_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Computer.managedByUser expands the managedBy DN into a User."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    owner_dn = "CN=Owner,OU=IT,DC=example,DC=com"
    pc = {"sAMAccountName": "PC001$", "managedBy": owner_dn}
    owner = {"sAMAccountName": "owner", "displayName": "The Owner"}

    monkeypatch.setattr(schema_mod, "get_computer", lambda c, b, i: pc)
    import msad

    monkeypatch.setattr(
        msad, "get_by_dn", lambda c, dn, attributes=None: owner if dn == owner_dn else None
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      computer(identifier: "PC001") {
        samAccountName
        raw
        managedByUser { samAccountName displayName }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    c = result.data["computer"]
    assert c["samAccountName"] == "PC001$"
    assert c["raw"]["managedBy"] == owner_dn
    assert c["managedByUser"]["samAccountName"] == "owner"
    assert c["managedByUser"]["displayName"] == "The Owner"


def test_ou_type_has_virtual_managed_by() -> None:
    result = schema.execute_sync('{ __type(name: "Ou") { fields { name type { name } } } }')
    assert result.errors is None
    fields = {f["name"]: f for f in result.data["__type"]["fields"]}
    assert "raw" in fields
    assert {"dn", "ou", "description"} <= set(fields)
    assert "managedByUser" in fields
    assert fields["managedByUser"]["type"]["name"] == "User"


def test_ou_managed_by_user_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ou.managedByUser expands the managedBy DN into a User."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    owner_dn = "CN=Owner,OU=IT,DC=example,DC=com"
    ou_entry = {"ou": "Sales", "managedBy": owner_dn}
    owner = {"sAMAccountName": "owner", "displayName": "The Owner"}

    monkeypatch.setattr(schema_mod, "get_ou", lambda c, b, i: ou_entry)
    import msad

    monkeypatch.setattr(
        msad, "get_by_dn", lambda c, dn, attributes=None: owner if dn == owner_dn else None
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      ou(identifier: "Sales") {
        ou
        raw
        managedByUser { samAccountName displayName }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    o = result.data["ou"]
    assert o["ou"] == "Sales"
    assert o["raw"]["managedBy"] == owner_dn
    assert o["managedByUser"]["samAccountName"] == "owner"
    assert o["managedByUser"]["displayName"] == "The Owner"


def test_group_members_return_users_with_manager_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """groupMembers returns Users, so managerUser expansion is available on each."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    boss_dn = "CN=Boss,DC=x"
    members = [
        {"sAMAccountName": "m1", "manager": boss_dn},
        {"sAMAccountName": "m2"},
    ]
    # schema imported group_members by name; patch the schema-module reference.
    monkeypatch.setattr(schema_mod, "group_members", lambda *a, **k: members)
    import msad

    monkeypatch.setattr(
        msad,
        "get_by_dn",
        lambda c, dn, attributes=None: {"sAMAccountName": "boss"} if dn == boss_dn else None,
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      groupMembers(identifier: "g") {
        count
        users { samAccountName managerUser { samAccountName } }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    data = result.data["groupMembers"]
    assert data["count"] == 2
    by_sam = {u["samAccountName"]: u for u in data["users"]}
    assert by_sam["m1"]["managerUser"]["samAccountName"] == "boss"
    assert by_sam["m2"]["managerUser"] is None


# ----- list-valued virtual fields --------------------------------------
def _dn_db(mapping: dict[str, dict]):
    """Build a fake get_by_dn over a {dn: entry} mapping (unknown -> None)."""

    def _get(conn, dn, attributes=None):  # noqa: ANN001
        return mapping.get(dn)

    return _get


def test_user_member_of_groups_expands_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    g1, g2, missing = "CN=G1,DC=x", "CN=G2,DC=x", "CN=Gone,DC=x"
    employee = {"sAMAccountName": "mrossi", "memberOf": [g1, g2, missing]}
    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: employee)

    import msad

    monkeypatch.setattr(
        msad,
        "get_by_dn",
        _dn_db({g1: {"cn": "G1"}, g2: {"cn": "G2"}}),  # missing -> None, skipped
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ user(identifier: "mrossi") { memberOfGroups { cn } } }', ctx)
    assert result.errors is None, result.errors
    cns = [g["cn"] for g in result.data["user"]["memberOfGroups"]]
    assert cns == ["G1", "G2"]  # unresolvable DN skipped


def test_user_direct_reports_expands_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    r1, r2 = "CN=R1,DC=x", "CN=R2,DC=x"
    boss = {"sAMAccountName": "boss", "directReports": [r1, r2]}
    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: boss)

    import msad

    monkeypatch.setattr(
        msad,
        "get_by_dn",
        _dn_db({r1: {"sAMAccountName": "r1"}, r2: {"sAMAccountName": "r2"}}),
    )

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query(
        '{ user(identifier: "boss") { directReportsUsers { samAccountName } } }', ctx
    )
    assert result.errors is None, result.errors
    sams = sorted(u["samAccountName"] for u in result.data["user"]["directReportsUsers"])
    assert sams == ["r1", "r2"]


def test_group_member_users_expands_single_dn(monkeypatch: pytest.MonkeyPatch) -> None:
    """member may be a single scalar DN (not a list); it must still expand."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    only = "CN=Solo,DC=x"
    grp = {"cn": "g", "member": only}  # single-valued
    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)

    import msad

    monkeypatch.setattr(msad, "get_by_dn", _dn_db({only: {"sAMAccountName": "solo"}}))

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ group(identifier: "g") { memberUsers { samAccountName } } }', ctx)
    assert result.errors is None, result.errors
    members = result.data["group"]["memberUsers"]
    assert [u["samAccountName"] for u in members] == ["solo"]


def test_list_virtual_fields_empty_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: {"sAMAccountName": "x"})

    import msad

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("get_by_dn must not run for empty lists")

    monkeypatch.setattr(msad, "get_by_dn", boom)

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query(
        '{ user(identifier: "x") { memberOfGroups { cn } directReportsUsers { dn } } }', ctx
    )
    assert result.errors is None, result.errors
    assert result.data["user"]["memberOfGroups"] == []
    assert result.data["user"]["directReportsUsers"] == []


# ----- DataLoader dedup / within-request caching -----------------------
def test_dataloader_dedups_repeated_dn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same DN resolved from multiple places runs get_by_dn only once.

    A manager who is also one of the user's direct reports appears twice in the
    query, but the per-request DataLoader must resolve that DN a single time.
    """
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    shared_dn = "CN=Shared,DC=x"
    employee = {
        "sAMAccountName": "mrossi",
        "manager": shared_dn,
        "directReports": [shared_dn],  # same DN as the manager
    }
    monkeypatch.setattr(schema_mod, "get_user", lambda c, b, i: employee)

    calls: list[str] = []

    def counting_get_by_dn(conn, dn, attributes=None):  # noqa: ANN001
        calls.append(dn)
        return {"sAMAccountName": "shared"} if dn == shared_dn else None

    import msad

    monkeypatch.setattr(msad, "get_by_dn", counting_get_by_dn)

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      user(identifier: "mrossi") {
        managerUser { samAccountName }
        directReportsUsers { samAccountName }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    assert result.data["user"]["managerUser"]["samAccountName"] == "shared"
    assert [u["samAccountName"] for u in result.data["user"]["directReportsUsers"]] == ["shared"]
    # Resolved once despite being requested from two fields.
    assert calls == [shared_dn], calls


def test_dataloader_is_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh context (new request) does not reuse another request's cache."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    dn = "CN=Boss,DC=x"
    monkeypatch.setattr(
        schema_mod, "get_user", lambda c, b, i: {"sAMAccountName": "u", "manager": dn}
    )
    calls: list[str] = []

    def counting(conn, d, attributes=None):  # noqa: ANN001
        calls.append(d)
        return {"sAMAccountName": "boss"}

    import msad

    monkeypatch.setattr(msad, "get_by_dn", counting)

    q = '{ user(identifier: "u") { managerUser { samAccountName } } }'
    # Two separate requests -> two separate contexts -> two lookups total.
    run_query(q, Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object()))
    run_query(q, Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object()))
    assert calls == [dn, dn], calls


# ----- GroupMember union (User | Group | Computer) ---------------------
def test_group_member_union_in_schema() -> None:
    result = schema.execute_sync('{ __type(name: "GroupMember") { kind possibleTypes { name } } }')
    assert result.errors is None
    t = result.data["__type"]
    assert t["kind"] == "UNION"
    assert sorted(p["name"] for p in t["possibleTypes"]) == ["Computer", "Group", "User"]


def test_group_members_resolve_to_concrete_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Group.members discriminates each member by objectClass."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    u_dn, g_dn, c_dn, other_dn, gone_dn = (
        "CN=U,DC=x",
        "CN=G,DC=x",
        "CN=C,DC=x",
        "CN=O,DC=x",
        "CN=Gone,DC=x",
    )
    grp = {"cn": "parent", "member": [u_dn, g_dn, c_dn, other_dn, gone_dn]}
    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)

    db = {
        u_dn: {"objectClass": ["top", "person", "user"], "sAMAccountName": "alice"},
        # computer has BOTH computer and user classes -> must classify as Computer
        g_dn: {"objectClass": ["top", "group"], "cn": "subgroup"},
        c_dn: {"objectClass": ["top", "person", "user", "computer"], "dNSHostName": "pc1.x"},
        other_dn: {"objectClass": ["top", "contact"], "cn": "a-contact"},  # skipped
    }

    import msad

    monkeypatch.setattr(msad, "get_by_dn", lambda conn, dn, attributes=None: db.get(dn))

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    query = """
    {
      group(identifier: "parent") {
        members {
          __typename
          ... on User { samAccountName }
          ... on Group { cn }
          ... on Computer { dnsHostName }
        }
      }
    }
    """
    result = run_query(query, ctx)
    assert result.errors is None, result.errors
    members = result.data["group"]["members"]
    by_type = {m["__typename"]: m for m in members}
    # contact (other) and unresolvable (gone) are skipped -> 3 members
    assert set(by_type) == {"User", "Group", "Computer"}
    assert by_type["User"]["samAccountName"] == "alice"
    assert by_type["Group"]["cn"] == "subgroup"
    assert by_type["Computer"]["dnsHostName"] == "pc1.x"


def test_member_users_still_only_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing memberUsers field keeps returning only User members."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    u_dn, g_dn = "CN=U,DC=x", "CN=G,DC=x"
    grp = {"cn": "parent", "member": [u_dn, g_dn]}
    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)

    db = {
        u_dn: {"objectClass": ["user"], "sAMAccountName": "alice"},
        g_dn: {"objectClass": ["group"], "cn": "subgroup"},
    }
    import msad

    monkeypatch.setattr(msad, "get_by_dn", lambda conn, dn, attributes=None: db.get(dn))

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    # memberUsers wraps every entry as User (no objectClass filtering), so the
    # group entry would appear too; assert the User is present and typed.
    result = run_query('{ group(identifier: "parent") { memberUsers { samAccountName } } }', ctx)
    assert result.errors is None, result.errors
    sams = [u["samAccountName"] for u in result.data["group"]["memberUsers"]]
    assert "alice" in sams


# ----- group nesting (memberGroups / memberOfGroups) -------------------
def test_group_member_groups_filters_to_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """memberGroups keeps only members whose objectClass is group."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    u_dn, g1_dn, g2_dn, c_dn = "CN=U,DC=x", "CN=G1,DC=x", "CN=G2,DC=x", "CN=C,DC=x"
    grp = {"cn": "parent", "member": [u_dn, g1_dn, g2_dn, c_dn]}
    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)

    db = {
        u_dn: {"objectClass": ["user"], "sAMAccountName": "alice"},
        g1_dn: {"objectClass": ["group"], "cn": "sub1"},
        g2_dn: {"objectClass": ["top", "group"], "cn": "sub2"},
        c_dn: {"objectClass": ["computer", "user"], "cn": "pc"},
    }
    import msad

    monkeypatch.setattr(msad, "get_by_dn", lambda conn, dn, attributes=None: db.get(dn))

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ group(identifier: "parent") { memberGroups { cn } } }', ctx)
    assert result.errors is None, result.errors
    cns = sorted(g["cn"] for g in result.data["group"]["memberGroups"])
    assert cns == ["sub1", "sub2"]  # user and computer excluded


def test_group_member_of_groups_expands_upward(monkeypatch: pytest.MonkeyPatch) -> None:
    """memberOfGroups expands the group's own memberOf into Group objects."""
    from graphqlad import schema as schema_mod
    from graphqlad.context import Context

    p1, p2 = "CN=Parent1,DC=x", "CN=Parent2,DC=x"
    grp = {"cn": "child", "memberOf": [p1, p2]}
    monkeypatch.setattr(schema_mod, "get_group", lambda c, b, i: grp)

    db = {p1: {"objectClass": ["group"], "cn": "P1"}, p2: {"objectClass": ["group"], "cn": "P2"}}
    import msad

    monkeypatch.setattr(msad, "get_by_dn", lambda conn, dn, attributes=None: db.get(dn))

    ctx = Context(config=resolve_domain_config(env=_FAKE_ENV), conn=object())
    result = run_query('{ group(identifier: "child") { memberOfGroups { cn } } }', ctx)
    assert result.errors is None, result.errors
    cns = sorted(g["cn"] for g in result.data["group"]["memberOfGroups"])
    assert cns == ["P1", "P2"]

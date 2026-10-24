# graphqlAD

A **read-only** GraphQL interface over the [`msad`](https://pypi.org/project/msad/)
Active Directory library, built with **Strawberry** + **FastAPI**, managed with
**uv** on **Python 3.14**.

Only LDAP *search* operations are exposed. The schema has a single `Query` type
and **no `Mutation` type**, so no write operation (create / modify / delete /
password reset / group membership changes) is reachable through the API.

It is a GraphQL sibling of [restAD](https://pypi.org/project/restad/) (REST) and
[mcpAD](https://pypi.org/project/mcpad/) (MCP): all three are thin, read-only
front ends that call the same typed `msad` API directly.

## How configuration is resolved

The effective connection config (an `msad.DomainConfig`) is built in two layers,
with the environment always winning:

1. **msad TOML file** (optional) — `~/.msad.toml` by default, or the path in
   `MSAD_CONFIG_FILE`. The `MSAD_DOMAIN` variable selects which
   `[domains.<name>]` section to use (otherwise the `[defaults] domain`).
2. **`MSAD_*` environment overrides** — applied on top of the TOML values.

This lets you keep non-secret settings in the TOML file (or omit it entirely)
and inject **credentials and any connection parameter at runtime** via the
environment, so secrets never need to live on disk.

### Environment variables

| Variable             | Overrides / effect                                             |
| -------------------- | -------------------------------------------------------------- |
| `MSAD_CONFIG_FILE`   | Path to the msad TOML file (default `~/.msad.toml`).           |
| `MSAD_DOMAIN`        | Which `[domains.<name>]` section to use.                       |
| `MSAD_HOST`          | Directory host (e.g. `dc.example.com`).                        |
| `MSAD_BASE`          | Search base DN (e.g. `dc=example,dc=com`).                     |
| `MSAD_PORT`          | Port (int, e.g. `636`).                                        |
| `MSAD_USE_SSL`       | LDAPS on/off (`true`/`false`/`1`/`0`/`yes`/`no`).              |
| `MSAD_USER`          | Bind user. Setting user **and** password enables simple bind. |
| `MSAD_PASSWORD`      | Bind password.                                                 |
| `MSAD_QUERY_TIMEOUT` | Per-operation receive timeout in seconds (int).               |

`host` and `base` are required (from either the TOML file or the environment).

Authentication:
- Omit `MSAD_USER`/`MSAD_PASSWORD` **and** leave them unset in the TOML →
  Kerberos/SASL bind (run `kinit` first).
- Set both `MSAD_USER` and `MSAD_PASSWORD` → simple user/password bind.

### Server variables

| Variable                | Effect                                                     |
| ----------------------- | ---------------------------------------------------------- |
| `GRAPHQLAD_HOST`        | HTTP bind host for the `graphqlad` command (default `127.0.0.1`). |
| `GRAPHQLAD_PORT`        | HTTP bind port (default `8000`).                           |
| `GRAPHQLAD_LAZY_CONNECT`| If true, start even if the directory is unreachable at boot. |
| `GRAPHQLAD_GRAPHIQL`    | Serve the in-browser GraphiQL IDE at `/graphql` (default `true`). Set to `false` to disable it. |

## Install

```bash
uv sync
```

The `msad` dependency is pulled from PyPI (`msad>=0.8.2`). Because `msad`
depends on `gssapi`, installing it builds a C extension against the system
Kerberos libraries, so you need the krb5 development headers available (e.g.
`libkrb5-dev` + `krb5-config` on Debian/Ubuntu, `krb5` via Homebrew on macOS).

Common developer tasks run through the `Makefile`:

```bash
make sync     # install/refresh the dev environment
make lint     # ruff check + format check
make test     # run the test suite
make build    # build wheel + sdist into dist/
make publish  # upload to PyPI (needs credentials)
```

## Run

Credentials are passed via the environment:

```bash
export MSAD_HOST=dc.example.com
export MSAD_BASE='dc=example,dc=com'
export MSAD_PORT=636
export MSAD_USE_SSL=true
export MSAD_USER='svc_reader'
export MSAD_PASSWORD='s3cr3t'

uv run graphqlad
# or:
uv run uvicorn graphqlad.app:app --host 0.0.0.0 --port 8000
```

Then open the GraphiQL IDE at <http://127.0.0.1:8000/graphql> (the same URL
serves GraphQL `POST` queries).

### Run with uvx

`uvx` runs graphqlAD in a throwaway, isolated environment — no clone or manual
install needed. The connection is still configured entirely through the
environment.

```bash
# From PyPI (once graphqlAD is published):
MSAD_HOST=dc.example.com MSAD_BASE='dc=example,dc=com' \
MSAD_USER=svc_reader MSAD_PASSWORD='s3cr3t' \
  uvx graphqlad

# From a local checkout:
uvx --from . graphqlad

# Straight from the Git repository (no clone):
uvx --from git+https://github.com/matteoredaelli/graphqlAD graphqlad
```

All `MSAD_*` and `GRAPHQLAD_*` variables work exactly as above. Because `uvx`
builds `msad` (and its `gssapi` C extension) in a fresh environment, the krb5
development headers must be available on the host (see Install).

## The schema

A single `Query` type exposes every read operation. Example query:

```graphql
{
  health { ok target auth error }

  users(surname: "Rossi", department: "IT", limit: 10) {
    count
    entries          # dynamic LDAP entries, as a JSON scalar
  }

  user(identifier: "mrossi") { entry }

  userStatus(identifier: "mrossi") {
    isDisabled
    isLocked
    hasExpiredPassword
    hasNeverExpiresPassword
  }

  osSummary { count items { operatingSystem count } }
}
```

### Available queries

- `health`
- **Users** — `users`, `inactiveUsers`, `user`, `userGroups`, `userStatus`.
- **Computers** — `computers`, `staleComputers`, `domainControllers`,
  `osSummary`, `computer`.
- **Groups** — `groups`, `emptyGroups`, `group`, `groupMembers`, `isMember`.
- **OUs** — `ous`, `ouContents`, `ou`.
- **Audit** — `domainInfo`, `passwordPolicy`, `passwordPolicyViolations`,
  `privilegedGroups`, `fsmoRoles`, `trusts`, `sites`, `gpos`, `gpo`,
  `fineGrainedPasswordPolicies`.

## Notes

- LDAP entries are dynamic (attributes depend on what the directory returns),
  so each entry is exposed as an open `JSON` scalar inside typed envelopes
  (`count` + `entries`, or `entry`). Non-JSON-native values (datetimes, bytes)
  are coerced to strings for transport.
- A single LDAP connection is opened during the app lifespan and shared across
  requests; it is unbound at shutdown.

## License

GPL-3.0-or-later. Copyright © Matteo Redaelli.

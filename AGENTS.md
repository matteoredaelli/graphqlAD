# AGENTS.md — notes for coding agents working on graphqlAD

This file is for AI coding agents. It covers things that aren't in the
README (install/usage/query list) but matter when *changing code* here.

## What this repo is

A thin, read-only Strawberry GraphQL wrapper around the `msad` library:
`schema.py` defines a single `Query` type with one field per `msad`
function; `types.py` defines the typed GraphQL objects (`User`, `Group`,
`Computer`, `Ou`, plus the generic `Entries`/`Entry` JSON envelope for
everything else) and their virtual DN-expansion fields. There is **no
Mutation type** — this is enforced by
`tests/test_app.py::test_schema_builds_and_is_read_only`.

This is a sibling project to `restAD` and `mcpAD`: all three wrap the same
`msad` functions through a different protocol. When `msad` gains a new
function, it is expected to show up here too (a new `Query` field), and in
the other two repos.

## Typed objects vs. generic JSON — when to add a new type

Not every query needs its own GraphQL type. The rule actually followed:

- If the entry has **DN-valued attributes worth expanding** into another
  typed object (`manager`, `memberOf`, `managedBy`, `member`,
  `directReports`, ...), give it a real type with `raw: JSON` plus a few
  identity scalars plus the virtual `*User`/`*Groups` resolver fields (see
  `User`, `Group`, `Computer`, `Ou` in `types.py` for the pattern — they all
  resolve DNs lazily through `info.context.dn_loader`, a per-request
  DataLoader; never call `msad.get_by_dn` directly in a resolver).
- If the result mixes object types (e.g. `ouContents`) or has no DN worth
  expanding (e.g. `contacts`), just return the generic `Entries`/`Entry`.

If you add a DN-valued attribute to one of the typed objects, also add the
matching virtual field — don't let `raw.someDn` sit unexploited. Grep the
other three repos' recent history for "managedByUser" additions for the
shape of this kind of change.

## GroupMember union

`Group.members` resolves to the `GroupMember = User | Group | Computer`
union via `_classify()` in `types.py`, which switches on `objectClass`
(computer checked before user, since AD computer accounts carry both). If
you add a new typed object that can be a group member, extend `_classify`
and the union, not just the type itself.

## Config resolution is centralized in msad — don't reimplement it

`src/graphqlad/config.py` is a two-line re-export (`ConfigError =
msad.MsadConfigError`, `resolve_domain_config = msad.resolve_domain_config`).
The actual TOML + `MSAD_*` env-override logic lives in msad's
`resolve_domain_config()` (shared with restAD and mcpAD — see msad's
AGENTS.md). If you need a new overridable connection field, add it in msad,
not here. `app.py` calls `resolve_domain_config()` with no args (reads the
real `os.environ`); tests pass a dict via the `env=` keyword (positional
calls bind to `domain`, not `env` — don't drop the keyword).

## Kerberos is an optional extra

`[project.optional-dependencies] kerberos = ["msad[kerberos]>=..."]` mirrors
msad's own extra. Don't add `gssapi` to the base `dependencies`.

## Versioning — do not hand-edit

Version comes from the Git tag via `hatch-vcs`, same setup as `msad` (see
`msad`'s AGENTS.md for the mechanics and the `fetch-depth: 0` CI gotcha).
`__version__` lives in `src/graphqlad/__init__.py` and is referenced by
`app.py` (FastAPI `version=` and the `GET /` metadata) — never hardcode a
version string anywhere else.

## Keeping `msad` in sync

When bumping the `msad>=` pin in `pyproject.toml`, run:

```bash
uv lock --upgrade-package msad --refresh-package msad
```

Plain `--upgrade-package` without `--refresh-package` can silently resolve
against a stale cached version of msad's metadata right after a new release.

## Testing conventions

`tests/test_app.py` uses `schema.execute_sync` for pure-schema checks and a
`run_query()` helper (wraps `schema.execute` in `asyncio.run`) for anything
touching a virtual field — virtual-field resolvers are `async` and drive the
DataLoader, which needs a running event loop (`execute_sync` can't do that).
Mock at the `msad` function level with `monkeypatch` (e.g.
`monkeypatch.setattr(schema_mod, "get_user", ...)` and
`monkeypatch.setattr(msad, "get_by_dn", ...)`), not against a real
directory. For a new typed object with a virtual field, add both: a
`__type` introspection check and a resolution test (see
`test_group_managed_by_user_resolves` for the shape).

Run `make lint` and `make test` before considering a change done.

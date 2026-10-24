"""Strawberry GraphQL types.

LDAP entries are dynamic (their attributes depend on what the directory
returns), which does not map onto GraphQL's rigid type system. Each entry is
therefore exposed as a custom ``JSON`` scalar (an open object), wrapped in
small typed envelopes that carry predictable fields such as ``count``.
"""

from __future__ import annotations

from typing import Any, NewType

import strawberry

#: A single LDAP entry serialized as an open JSON object. Attribute values are
#: whatever ldap3 decoded them to (str, int, datetime, list, ...); datetimes
#: and other non-JSON-native values are coerced to strings for transport.
JSON = strawberry.scalar(
    NewType("JSON", object),
    description="An arbitrary JSON value (used for dynamic LDAP entries).",
    serialize=lambda v: _json_safe(v),
    parse_value=lambda v: v,
)


def _json_safe(value: Any) -> Any:
    """Best-effort conversion of ldap3-decoded values to JSON-native types."""
    import datetime as _dt

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return str(value)


@strawberry.type(description="A list of LDAP entries plus a count.")
class Entries:
    count: int
    entries: list[JSON]  # type: ignore[valid-type]

    @classmethod
    def of(cls, items: list[dict[str, Any]] | None) -> Entries:
        items = items or []
        return cls(count=len(items), entries=list(items))


@strawberry.type(description="A single LDAP entry (null when not found).")
class Entry:
    entry: JSON | None = None  # type: ignore[valid-type]


def _first(value: Any) -> Any:
    """Return the first element if value is a list/tuple, else the value.

    LDAP attributes may be single- or multi-valued; ldap3 can return either a
    scalar or a list. For identity fields we want a single value.
    """
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


@strawberry.type(
    description=(
        "An Active Directory user. Raw LDAP attributes are available under "
        "`raw`; a few identity fields are surfaced as typed scalars. Virtual "
        "fields (suffixed to mark them as derived, not raw LDAP attributes) "
        "expand DN-valued attributes into objects, e.g. `managerUser` resolves "
        "the `manager` DN into a full `User`."
    )
)
class User:
    #: The complete, dynamic LDAP entry (nothing is lost by the typed fields).
    raw: JSON  # type: ignore[valid-type]

    @strawberry.field(description="distinguishedName of the user.")
    def dn(self) -> str | None:
        return _first(self._data.get("dn") or self._data.get("distinguishedName"))

    @strawberry.field(description="sAMAccountName (login).")
    def sam_account_name(self) -> str | None:
        return _first(self._data.get("sAMAccountName"))

    @strawberry.field(description="displayName.")
    def display_name(self) -> str | None:
        return _first(self._data.get("displayName"))

    @strawberry.field(description="Primary mail address.")
    def mail(self) -> str | None:
        return _first(self._data.get("mail"))

    @strawberry.field(
        description=(
            "VIRTUAL: the user's manager expanded from the `manager` DN into a "
            "full User (null if absent or unresolvable). The raw DN remains at "
            "`raw.manager`. Resolved lazily: only queried when this field is "
            "selected."
        )
    )
    def manager_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("manager"))
        if not dn:
            return None
        # Imported here to keep this module free of schema-level imports.
        from msad import get_by_dn

        ctx = info.context
        conn = ctx.require_conn()
        entry = get_by_dn(conn, str(dn))
        return User.from_entry(entry)

    # ----- internals ----------------------------------------------------
    #: Backing dict for the resolvers above. Not exposed as a GraphQL field.
    _data: strawberry.Private[dict[str, Any]]

    @classmethod
    def from_entry(cls, entry: dict[str, Any] | None) -> User | None:
        """Wrap an msad user entry (or None) into a ``User`` instance."""
        if entry is None:
            return None
        return cls(raw=entry, _data=entry)


@strawberry.type(description="A list of users plus a count.")
class Users:
    count: int
    users: list[User]

    @classmethod
    def of(cls, items: list[dict[str, Any]] | None) -> Users:
        items = items or []
        wrapped = [u for u in (User.from_entry(it) for it in items) if u is not None]
        return cls(count=len(wrapped), users=wrapped)


@strawberry.type(
    description=(
        "An Active Directory group. Raw LDAP attributes are available under "
        "`raw`; a few identity fields are typed scalars. Virtual field "
        "`managedByUser` expands the `managedBy` DN into the owning `User`."
    )
)
class Group:
    #: The complete, dynamic LDAP entry.
    raw: JSON  # type: ignore[valid-type]

    @strawberry.field(description="distinguishedName of the group.")
    def dn(self) -> str | None:
        return _first(self._data.get("dn") or self._data.get("distinguishedName"))

    @strawberry.field(description="sAMAccountName of the group.")
    def sam_account_name(self) -> str | None:
        return _first(self._data.get("sAMAccountName"))

    @strawberry.field(description="Common name (cn).")
    def cn(self) -> str | None:
        return _first(self._data.get("cn"))

    @strawberry.field(description="displayName.")
    def display_name(self) -> str | None:
        return _first(self._data.get("displayName"))

    @strawberry.field(
        description=(
            "VIRTUAL: the group's owner expanded from the `managedBy` DN into a "
            "full User (null if absent or unresolvable). The raw DN remains at "
            "`raw.managedBy`. Resolved lazily: only queried when selected."
        )
    )
    def managed_by_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("managedBy"))
        if not dn:
            return None
        from msad import get_by_dn

        entry = get_by_dn(info.context.require_conn(), str(dn))
        return User.from_entry(entry)

    #: Backing dict for the resolvers above. Not exposed as a GraphQL field.
    _data: strawberry.Private[dict[str, Any]]

    @classmethod
    def from_entry(cls, entry: dict[str, Any] | None) -> Group | None:
        """Wrap an msad group entry (or None) into a ``Group`` instance."""
        if entry is None:
            return None
        return cls(raw=entry, _data=entry)


@strawberry.type(description="A list of groups plus a count.")
class Groups:
    count: int
    groups: list[Group]

    @classmethod
    def of(cls, items: list[dict[str, Any]] | None) -> Groups:
        items = items or []
        wrapped = [g for g in (Group.from_entry(it) for it in items) if g is not None]
        return cls(count=len(wrapped), groups=wrapped)


@strawberry.type(description="One row of the computer-OS summary.")
class OsSummaryItem:
    operating_system: str
    count: int


@strawberry.type(description="Computer accounts grouped and counted by OS.")
class OsSummary:
    count: int
    items: list[OsSummaryItem]


@strawberry.type(description="Bundle of boolean account-status checks (null = unknown).")
class UserStatus:
    is_disabled: bool | None
    is_locked: bool | None
    has_never_expires_password: bool | None
    has_expired_password: bool | None


@strawberry.type(description="Result of the LDAP connectivity health check.")
class Health:
    ok: bool
    target: str
    auth: str
    error: str | None = None

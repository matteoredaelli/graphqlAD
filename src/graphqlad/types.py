"""Strawberry GraphQL types.

LDAP entries are dynamic (their attributes depend on what the directory
returns), which does not map onto GraphQL's rigid type system. Each entry is
therefore exposed as a custom ``JSON`` scalar (an open object), wrapped in
small typed envelopes that carry predictable fields such as ``count``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, NewType

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

    if isinstance(value, Mapping):
        # Covers plain dicts *and* ldap3's own CaseInsensitiveDict, which is
        # what every msad entry actually is at runtime. isinstance(value,
        # dict) is False for it, so checking only `dict` here would skip
        # this branch entirely and fall through to the str(value) catch-all
        # below — i.e. the whole entry serialized as one opaque string
        # instead of a proper nested JSON object (this hit every `raw`
        # field, since that's a direct copy of the msad entry).
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
    if isinstance(value, _dt.timedelta):
        # e.g. maxPwdAge / lockoutDuration from passwordPolicy / domainInfo:
        # ldap3 decodes these AD "interval" attributes into timedelta.
        return str(value)
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


def _as_list(value: Any) -> list[Any]:
    """Normalize a possibly single- or multi-valued attribute to a list.

    ``None`` becomes ``[]``; a scalar becomes a one-element list; a list/tuple
    is returned as a list (empty/false-y elements dropped).
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v]
    return [value]


async def _expand_dns(info: strawberry.Info, dns: list[Any]) -> list[dict[str, Any]]:
    """Resolve a list of DNs into entries via the per-request DataLoader.

    Uses ``load_many`` so all DNs in this field are batched together and
    deduplicated/cached for the request. Unresolvable DNs (loader returns
    ``None``) are skipped. Only runs when the owning virtual field is selected.
    """
    if not dns:
        return []
    loader = info.context.dn_loader
    results = await loader.load_many([str(dn) for dn in dns])
    return [e for e in results if e is not None]


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
    async def manager_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("manager"))
        if not dn:
            return None
        entry = await info.context.dn_loader.load(str(dn))
        return User.from_entry(entry)

    @strawberry.field(
        description=(
            "VIRTUAL: the groups this user is a direct member of, expanded from "
            "the `memberOf` DNs into full Group objects. Raw DNs remain at "
            "`raw.memberOf`. Resolved lazily; unresolvable DNs are skipped."
        )
    )
    async def member_of_groups(self, info: strawberry.Info) -> list[Group]:
        dns = _as_list(self._data.get("memberOf"))
        entries = await _expand_dns(info, dns)
        return [g for g in (Group.from_entry(e) for e in entries) if g is not None]

    @strawberry.field(
        description=(
            "VIRTUAL: the user's direct reports, expanded from the "
            "`directReports` DNs into full User objects. Raw DNs remain at "
            "`raw.directReports`. Resolved lazily; unresolvable DNs are skipped."
        )
    )
    async def direct_reports_users(self, info: strawberry.Info) -> list[User]:
        dns = _as_list(self._data.get("directReports"))
        entries = await _expand_dns(info, dns)
        return [u for u in (User.from_entry(e) for e in entries) if u is not None]

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
        "An Active Directory computer account. Raw LDAP attributes are "
        "available under `raw`; a few identity fields are typed scalars."
    )
)
class Computer:
    #: The complete, dynamic LDAP entry.
    raw: JSON  # type: ignore[valid-type]

    @strawberry.field(description="distinguishedName of the computer.")
    def dn(self) -> str | None:
        return _first(self._data.get("dn") or self._data.get("distinguishedName"))

    @strawberry.field(description="sAMAccountName (usually ends with '$').")
    def sam_account_name(self) -> str | None:
        return _first(self._data.get("sAMAccountName"))

    @strawberry.field(description="Common name (cn).")
    def cn(self) -> str | None:
        return _first(self._data.get("cn"))

    @strawberry.field(description="dNSHostName (FQDN).")
    def dns_host_name(self) -> str | None:
        return _first(self._data.get("dNSHostName"))

    @strawberry.field(description="operatingSystem.")
    def operating_system(self) -> str | None:
        return _first(self._data.get("operatingSystem"))

    @strawberry.field(
        description=(
            "VIRTUAL: the computer's owner expanded from the `managedBy` DN into "
            "a full User (null if absent or unresolvable). The raw DN remains at "
            "`raw.managedBy`. Resolved lazily: only queried when selected."
        )
    )
    async def managed_by_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("managedBy"))
        if not dn:
            return None
        entry = await info.context.dn_loader.load(str(dn))
        return User.from_entry(entry)

    #: Backing dict for the resolvers above. Not exposed as a GraphQL field.
    _data: strawberry.Private[dict[str, Any]]

    @classmethod
    def from_entry(cls, entry: dict[str, Any] | None) -> Computer | None:
        """Wrap an msad computer entry (or None) into a ``Computer`` instance."""
        if entry is None:
            return None
        return cls(raw=entry, _data=entry)


@strawberry.type(description="A list of computers plus a count.")
class Computers:
    count: int
    computers: list[Computer]

    @classmethod
    def of(cls, items: list[dict[str, Any]] | None) -> Computers:
        items = items or []
        wrapped = [c for c in (Computer.from_entry(it) for it in items) if c is not None]
        return cls(count=len(wrapped), computers=wrapped)


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
    async def managed_by_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("managedBy"))
        if not dn:
            return None
        entry = await info.context.dn_loader.load(str(dn))
        return User.from_entry(entry)

    @strawberry.field(
        description=(
            "VIRTUAL: the group's direct members, expanded from the `member` "
            "DNs into full User objects. Raw DNs remain at `raw.member`. Note: "
            "members may include nested groups/computers; those are skipped "
            "here (use `groupMembers` for msad's person-oriented listing). "
            "Resolved lazily; unresolvable DNs are skipped."
        )
    )
    async def member_users(self, info: strawberry.Info) -> list[User]:
        dns = _as_list(self._data.get("member"))
        entries = await _expand_dns(info, dns)
        return [u for u in (User.from_entry(e) for e in entries) if u is not None]

    @strawberry.field(
        description=(
            "VIRTUAL: all direct members of the group, each resolved to its "
            "concrete type (User, Group or Computer) based on objectClass. "
            "Unlike `memberUsers`, nested groups and computers are kept. Use "
            "inline fragments to select fields per type: "
            "`members { ... on User { samAccountName } ... on Group { cn } "
            "... on Computer { dnsHostName } }`. Unresolvable members, and "
            "members of any other object class, are skipped. Resolved lazily."
        )
    )
    async def members(self, info: strawberry.Info) -> list[GroupMember]:
        dns = _as_list(self._data.get("member"))
        entries = await _expand_dns(info, dns)
        out: list[GroupMember] = []
        for e in entries:
            obj = _classify(e)
            if obj is not None:
                out.append(obj)
        return out

    @strawberry.field(
        description=(
            "VIRTUAL: the nested groups that are direct members of this group, "
            "i.e. the `member` DNs whose objectClass is `group` (users and "
            "computers are excluded). Raw DNs remain at `raw.member`. Resolved "
            "lazily; unresolvable DNs are skipped."
        )
    )
    async def member_groups(self, info: strawberry.Info) -> list[Group]:
        dns = _as_list(self._data.get("member"))
        entries = await _expand_dns(info, dns)
        out: list[Group] = []
        for e in entries:
            if "group" in _object_classes(e):
                g = Group.from_entry(e)
                if g is not None:
                    out.append(g)
        return out

    @strawberry.field(
        description=(
            "VIRTUAL: the groups this group is itself a member of, expanded "
            "from the `memberOf` DNs into full Group objects (group nesting, "
            "upward). Raw DNs remain at `raw.memberOf`. Resolved lazily; "
            "unresolvable DNs are skipped."
        )
    )
    async def member_of_groups(self, info: strawberry.Info) -> list[Group]:
        dns = _as_list(self._data.get("memberOf"))
        entries = await _expand_dns(info, dns)
        return [g for g in (Group.from_entry(e) for e in entries) if g is not None]

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


@strawberry.type(
    description=(
        "An Active Directory organizational unit. Raw LDAP attributes are "
        "available under `raw`; a few identity fields are typed scalars. "
        "Virtual field `managedByUser` expands the `managedBy` DN into the "
        "owning `User`."
    )
)
class Ou:
    #: The complete, dynamic LDAP entry.
    raw: JSON  # type: ignore[valid-type]

    @strawberry.field(description="distinguishedName of the OU.")
    def dn(self) -> str | None:
        return _first(self._data.get("dn") or self._data.get("distinguishedName"))

    @strawberry.field(description="The ou (name) attribute.")
    def ou(self) -> str | None:
        return _first(self._data.get("ou"))

    @strawberry.field(description="description.")
    def description(self) -> str | None:
        return _first(self._data.get("description"))

    @strawberry.field(
        description=(
            "VIRTUAL: the OU's owner expanded from the `managedBy` DN into a "
            "full User (null if absent or unresolvable). The raw DN remains at "
            "`raw.managedBy`. Resolved lazily: only queried when selected."
        )
    )
    async def managed_by_user(self, info: strawberry.Info) -> User | None:
        dn = _first(self._data.get("managedBy"))
        if not dn:
            return None
        entry = await info.context.dn_loader.load(str(dn))
        return User.from_entry(entry)

    #: Backing dict for the resolvers above. Not exposed as a GraphQL field.
    _data: strawberry.Private[dict[str, Any]]

    @classmethod
    def from_entry(cls, entry: dict[str, Any] | None) -> Ou | None:
        """Wrap an msad OU entry (or None) into an ``Ou`` instance."""
        if entry is None:
            return None
        return cls(raw=entry, _data=entry)


@strawberry.type(description="A list of OUs plus a count.")
class Ous:
    count: int
    ous: list[Ou]

    @classmethod
    def of(cls, items: list[dict[str, Any]] | None) -> Ous:
        items = items or []
        wrapped = [o for o in (Ou.from_entry(it) for it in items) if o is not None]
        return cls(count=len(wrapped), ous=wrapped)


#: A group member resolved to its concrete directory type. Clients select
#: per-type fields with inline fragments (``... on User`` / ``... on Group`` /
#: ``... on Computer``).
GroupMember = Annotated[
    User | Group | Computer,
    strawberry.union(
        "GroupMember",
        description="A group member: a User, a Group, or a Computer.",
    ),
]


def _object_classes(entry: dict[str, Any]) -> set[str]:
    """Return the lower-cased objectClass values of an entry."""
    raw = entry.get("objectClass") or entry.get("objectclass")
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return {str(v).lower() for v in values if v}


def _classify(entry: dict[str, Any] | None) -> User | Group | Computer | None:
    """Map an LDAP entry to User / Group / Computer by objectClass.

    Order matters: in AD a computer's objectClass contains both ``computer``
    and ``user``, so ``computer`` is checked first, then ``group``, then
    ``user``/``person``. Entries of any other class return ``None`` (skipped).
    """
    if entry is None:
        return None
    classes = _object_classes(entry)
    if "computer" in classes:
        return Computer.from_entry(entry)
    if "group" in classes:
        return Group.from_entry(entry)
    if classes & {"user", "person", "organizationalperson", "inetorgperson"}:
        return User.from_entry(entry)
    return None


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


@strawberry.type(description='Result of the RFC 4532 "Who Am I?" extended operation.')
class WhoAmI:
    authzid: str | None = None

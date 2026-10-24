"""GraphQL schema: a single read-only ``Query`` type.

Every field maps to an msad read function. There is intentionally **no
Mutation type**: all msad write operations (add/remove member, change
password, unlock account) are out of scope, so the API is read-only by
construction.

The shared LDAP connection and search base come from the request
:class:`~graphqlad.context.Context`.
"""

from __future__ import annotations

import strawberry
from msad import (
    check_connection,
    find_computers,
    find_groups,
    find_inactive_users,
    find_ous,
    find_stale_computers,
    find_users,
    get_computer,
    get_domain_info,
    get_gpo,
    get_group,
    get_ou,
    get_ou_contents,
    get_password_policy,
    get_password_policy_violations,
    get_privileged_groups,
    get_user,
    is_member,
    list_domain_controllers,
    list_empty_groups,
    list_fine_grained_password_policies,
    list_fsmo_roles,
    list_gpos,
    list_sites,
    list_trusts,
    summarize_computer_os,
    user_groups,
)
from msad.exceptions import MsadNotFoundError
from msad.group import group_members
from msad.user import (
    has_expired_password,
    has_never_expires_password,
    is_disabled,
    is_locked,
)
from strawberry.types import Info

from .context import Context
from .types import Entries, Entry, Health, OsSummary, OsSummaryItem, User, Users, UserStatus


def _ctx(info: Info) -> Context:
    return info.context


@strawberry.type
class Query:
    """Read-only queries over Active Directory via msad."""

    # ----- health -------------------------------------------------------
    @strawberry.field(description="Test connectivity/bind without raising.")
    def health(self, info: Info) -> Health:
        ctx = _ctx(info)
        config = ctx.require_config()
        scheme = "ldaps" if config.use_ssl else "ldap"
        target = f"{scheme}://{config.host}:{config.port}"
        auth = "kerberos" if config.uses_kerberos else f"user {config.user!r}"
        try:
            status = check_connection(config)
        except Exception as exc:  # defensive: check_connection should not raise
            return Health(ok=False, target=target, auth=auth, error=str(exc))
        return Health(
            ok=bool(status.get("ok")),
            target=str(status.get("target", target)),
            auth=str(status.get("auth", auth)),
            error=status.get("error"),
        )

    # ----- users --------------------------------------------------------
    @strawberry.field(description="Find users by field (all ANDed; * wildcards ok).")
    def users(
        self,
        info: Info,
        name: str | None = None,
        surname: str | None = None,
        mail: str | None = None,
        sam: str | None = None,
        department: str | None = None,
        limit: int = 0,
    ) -> Users:
        ctx = _ctx(info)
        return Users.of(
            find_users(
                ctx.require_conn(),
                ctx.base,
                name=name,
                surname=surname,
                mail=mail,
                sam=sam,
                department=department,
                limit=limit,
            )
        )

    @strawberry.field(description="Enabled users whose last logon is older than `days`.")
    def inactive_users(
        self, info: Info, days: int = 90, include_never: bool = False, limit: int = 0
    ) -> Users:
        ctx = _ctx(info)
        return Users.of(
            find_inactive_users(
                ctx.require_conn(), ctx.base, days=days, include_never=include_never, limit=limit
            )
        )

    @strawberry.field(description="Get a single user (sAMAccountName/UPN/mail/cn).")
    def user(self, info: Info, identifier: str) -> User | None:
        ctx = _ctx(info)
        return User.from_entry(get_user(ctx.require_conn(), ctx.base, identifier))

    @strawberry.field(description="Groups a user belongs to (nested by default).")
    def user_groups(
        self, info: Info, identifier: str, nested: bool = True, limit: int = 0
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            user_groups(ctx.require_conn(), ctx.base, limit, identifier, nested=nested)
        )

    @strawberry.field(description="Bundle of boolean status checks for a user.")
    def user_status(self, info: Info, identifier: str) -> UserStatus:
        ctx = _ctx(info)
        conn, base = ctx.require_conn(), ctx.base
        return UserStatus(
            is_disabled=is_disabled(conn, base, identifier),
            is_locked=is_locked(conn, base, identifier),
            has_never_expires_password=has_never_expires_password(conn, base, identifier),
            has_expired_password=has_expired_password(conn, base, identifier),
        )

    # ----- groups -------------------------------------------------------
    @strawberry.field(description="Search groups by cn/name/sAMAccountName/displayName.")
    def groups(self, info: Info, q: str = "*", limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(find_groups(ctx.require_conn(), ctx.base, q, limit=limit))

    @strawberry.field(description="Groups with no direct members.")
    def empty_groups(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_empty_groups(ctx.require_conn(), ctx.base, limit=limit))

    @strawberry.field(description="Get a single group (sAMAccountName/cn).")
    def group(self, info: Info, identifier: str) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_group(ctx.require_conn(), ctx.base, identifier))

    @strawberry.field(description="Members of a group (direct, or nested).")
    def group_members(
        self, info: Info, identifier: str, nested: bool = False, limit: int = 0
    ) -> Entries:
        ctx = _ctx(info)
        try:
            members = group_members(
                ctx.require_conn(), ctx.base, identifier, nested=nested, limit=limit
            )
        except MsadNotFoundError:
            members = []
        return Entries.of(members)

    @strawberry.field(description="Is `user` a (nested) member of `group`?")
    def is_member(self, info: Info, group: str, user: str) -> bool | None:
        ctx = _ctx(info)
        try:
            return is_member(ctx.require_conn(), ctx.base, group=group, user=user)
        except MsadNotFoundError:
            return None

    # ----- computers ----------------------------------------------------
    @strawberry.field(description="Find computers by name/dns/os (* wildcards ok).")
    def computers(
        self,
        info: Info,
        name: str | None = None,
        dns: str | None = None,
        os: str | None = None,
        limit: int = 0,
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            find_computers(ctx.require_conn(), ctx.base, name=name, dns=dns, os=os, limit=limit)
        )

    @strawberry.field(description="Computers whose last logon is older than `days`.")
    def stale_computers(
        self, info: Info, days: int = 90, include_never: bool = False, limit: int = 0
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            find_stale_computers(
                ctx.require_conn(), ctx.base, days=days, include_never=include_never, limit=limit
            )
        )

    @strawberry.field(description="List domain controllers (DC computer accounts).")
    def domain_controllers(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_domain_controllers(ctx.require_conn(), ctx.base, limit=limit))

    @strawberry.field(description="Computer accounts grouped by operatingSystem.")
    def os_summary(self, info: Info, limit: int = 0) -> OsSummary:
        ctx = _ctx(info)
        items = summarize_computer_os(ctx.require_conn(), ctx.base, limit=limit) or []
        rows = [
            OsSummaryItem(
                operating_system=str(it.get("operatingSystem", "")),
                count=int(it.get("count", 0)),
            )
            for it in items
        ]
        return OsSummary(count=len(rows), items=rows)

    @strawberry.field(description="Get a single computer (sAMAccountName/cn/dNSHostName).")
    def computer(self, info: Info, identifier: str) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_computer(ctx.require_conn(), ctx.base, identifier))

    # ----- OUs ----------------------------------------------------------
    @strawberry.field(description="Find organizational units by name (* wildcards ok).")
    def ous(self, info: Info, name: str | None = None, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(find_ous(ctx.require_conn(), ctx.base, name=name, limit=limit))

    @strawberry.field(description="List objects directly under an OU (by its DN).")
    def ou_contents(
        self, info: Info, ou_dn: str, object_class: str | None = None, limit: int = 0
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            get_ou_contents(ctx.require_conn(), ou_dn, object_class=object_class, limit=limit)
        )

    @strawberry.field(description="Get a single OU (ou name or full DN).")
    def ou(self, info: Info, identifier: str) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_ou(ctx.require_conn(), ctx.base, identifier))

    # ----- audit --------------------------------------------------------
    @strawberry.field(description="Domain object: security-relevant settings and metadata.")
    def domain_info(self, info: Info) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_domain_info(ctx.require_conn(), ctx.base))

    @strawberry.field(description="Default domain password policy.")
    def password_policy(self, info: Info) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_password_policy(ctx.require_conn(), ctx.base))

    @strawberry.field(description="Enabled users whose password violates the policy.")
    def password_policy_violations(
        self, info: Info, include_never_set: bool = True, limit: int = 0
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            get_password_policy_violations(
                ctx.require_conn(), ctx.base, include_never_set=include_never_set, limit=limit
            )
        )

    @strawberry.field(description="Well-known privileged groups with member counts.")
    def privileged_groups(
        self, info: Info, with_members: bool = False, nested: bool = False
    ) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            get_privileged_groups(
                ctx.require_conn(), ctx.base, with_members=with_members, nested=nested
            )
        )

    @strawberry.field(description="Owner of each of the five FSMO roles.")
    def fsmo_roles(self, info: Info) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_fsmo_roles(ctx.require_conn(), ctx.base))

    @strawberry.field(description="Domain trust relationships.")
    def trusts(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_trusts(ctx.require_conn(), ctx.base, limit=limit))

    @strawberry.field(description="AD sites from the Configuration NC.")
    def sites(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_sites(ctx.require_conn(), ctx.base, limit=limit))

    @strawberry.field(description="Group Policy Objects.")
    def gpos(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(list_gpos(ctx.require_conn(), ctx.base, limit=limit))

    @strawberry.field(description="Get a single GPO (cn GUID or displayName).")
    def gpo(self, info: Info, identifier: str) -> Entry:
        ctx = _ctx(info)
        return Entry(entry=get_gpo(ctx.require_conn(), ctx.base, identifier))

    @strawberry.field(description="Fine-grained password policies (PSOs).")
    def fine_grained_password_policies(self, info: Info, limit: int = 0) -> Entries:
        ctx = _ctx(info)
        return Entries.of(
            list_fine_grained_password_policies(ctx.require_conn(), ctx.base, limit=limit)
        )


schema = strawberry.Schema(query=Query)

__all__ = ["Query", "schema"]

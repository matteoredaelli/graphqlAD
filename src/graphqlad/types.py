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

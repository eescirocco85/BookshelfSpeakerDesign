"""REW measurement group helpers."""

from __future__ import annotations

from typing import Any

from .client import RewClient
from .logging_utils import write_debug


def find_or_create_group(client: RewClient, group_name: str, notes: str = "") -> dict[str, Any]:
    """Return a REW group info object, creating the group if needed."""
    existing = _find_group(client, group_name)
    if existing is not None:
        write_debug(f"Using existing REW group {group_name}: {existing}")
        return existing

    body = {"name": group_name, "notes": notes}
    response = client.post("/groups", body)
    write_debug(f"/groups POST: {response}")

    created = _find_group(client, group_name)
    if created is None:
        raise RuntimeError(f"REW did not return a created group named {group_name!r}")
    write_debug(f"Created REW group {group_name}: {created}")
    return created


def _find_group(client: RewClient, group_name: str) -> dict[str, Any] | None:
    groups = client.get("/groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        if isinstance(group, dict) and group.get("name") == group_name:
            return group
    return None

"""Role-based access control.

Design notes
------------
We follow the pattern used by Discord moderation bots (MEE6, Carl-bot) and
common Telegram community bots: an *explicit* permission set per role rather
than a strict role hierarchy. This makes it easy to:

- Add new roles later without rebalancing a ladder.
- Grant overlapping capabilities (e.g. both `admin` and `coacher` can review
  KB entries, but only `admin` can manage roles).
- Audit "who can do X?" by reading one matrix.

Roles
-----
- `admin`    — full power: user lifecycle, role management, broadcast, deletes
- `coacher`  — handles users: escalations, KB review, transcripts, DM, settask
- `service`  — read-only / automation: debug, view users (no writes)
- `user`     — regular end user; can only manage their own resources

The role lives in `users.role`. `SUPERVISOR_CHAT_ID` is *always* coerced to
`admin` at boot — they're the bootstrap admin and cannot be demoted.
"""

from __future__ import annotations

import logging
from typing import Iterable

from config import settings
from db import conn

log = logging.getLogger(__name__)

# Order matters for display only (admin first, user last).
ROLES = ("admin", "coacher", "service", "user")

ROLE_EMOJI = {
    "admin": "👑",
    "coacher": "🎓",
    "service": "⚙️",
    "user": "👤",
}

ROLE_LABEL_VI = {
    "admin": "Quản trị viên",
    "coacher": "Coach",
    "service": "Service",
    "user": "Người dùng",
}

# --- Permission matrix --------------------------------------------------
# Each permission maps to the SET of roles that hold it. To add a permission:
# add a key here and check it via has_perm() in the handler.

PERMISSIONS: dict[str, frozenset[str]] = {
    # User-resource visibility / management
    "view_users":        frozenset({"admin", "coacher", "service"}),
    "manage_users":      frozenset({"admin"}),  # approve/reject/revoke/block/freeze/delete/reonboard
    "manage_roles":      frozenset({"admin"}),  # promote/demote
    "broadcast":         frozenset({"admin"}),
    "dm_user":           frozenset({"admin", "coacher"}),

    # User content
    "view_transcripts":  frozenset({"admin", "coacher"}),

    # Escalation
    "handle_escalation": frozenset({"admin", "coacher"}),

    # Tasks
    "assign_task":       frozenset({"admin", "coacher"}),

    # Knowledge base
    "manage_kb":         frozenset({"admin", "coacher"}),
    "review_kb_pending": frozenset({"admin", "coacher"}),

    # System
    "view_debug":        frozenset({"admin", "service"}),
    "view_reports":      frozenset({"admin", "coacher"}),
}


# --- Lookup helpers -----------------------------------------------------

def get_role(user_id: int) -> str:
    """Return the user's role. Bootstrap admin (SUPERVISOR_CHAT_ID) is always 'admin'."""
    s = settings()
    if user_id == s.supervisor_chat_id:
        return "admin"
    row = conn().execute(
        "SELECT role FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    return row["role"] if row else "user"


def has_perm(user_id: int, perm: str) -> bool:
    """True if the user's role includes the given permission."""
    role = get_role(user_id)
    return role in PERMISSIONS.get(perm, frozenset())


class AdminCapReached(Exception):
    """Raised when trying to promote past MAX_ADMINS."""


def count_admins() -> int:
    """Count current admin role-holders (excludes supervisor double-count)."""
    s = settings()
    row = conn().execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
    ).fetchone()
    n = int(row["n"]) if row else 0
    # Ensure supervisor is counted at least once even if their row hasn't synced.
    sup_row = conn().execute(
        "SELECT role FROM users WHERE tg_id = ?", (s.supervisor_chat_id,)
    ).fetchone()
    if not sup_row:
        n += 1
    return n


def set_role(user_id: int, new_role: str) -> bool:
    """Set a user's role. Returns False if the role is invalid or user doesn't exist.

    Refuses to demote the bootstrap supervisor. Enforces MAX_ADMINS cap when
    promoting to admin.
    """
    if new_role not in ROLES:
        return False
    s = settings()
    if user_id == s.supervisor_chat_id and new_role != "admin":
        log.warning("Refusing to demote bootstrap supervisor %s to %r", user_id, new_role)
        return False

    # Cap check: only when ADDING a new admin (target not already admin)
    if new_role == "admin":
        current_role = get_role(user_id)
        if current_role != "admin":
            if count_admins() >= s.max_admins:
                log.warning(
                    "Refused promotion of %s to admin: cap %d reached",
                    user_id, s.max_admins,
                )
                raise AdminCapReached(
                    f"Đã đạt giới hạn {s.max_admins} admin. "
                    f"/demote một admin hiện tại trước nếu muốn thêm người mới."
                )

    from db import transaction
    with transaction() as cx:
        cur = cx.execute(
            "UPDATE users SET role = ? WHERE tg_id = ?", (new_role, user_id)
        )
        return cur.rowcount > 0


def list_staff() -> dict[str, list[dict]]:
    """Group non-user accounts by role for the /roles command."""
    rows = conn().execute(
        "SELECT tg_id, name, role FROM users WHERE role != 'user' ORDER BY role, joined_at"
    ).fetchall()
    grouped: dict[str, list[dict]] = {r: [] for r in ROLES if r != "user"}
    for r in rows:
        grouped.setdefault(r["role"], []).append({"tg_id": r["tg_id"], "name": r["name"]})
    return grouped


def get_ids_with_perm(perm: str) -> list[int]:
    """All registered user IDs whose role holds the given permission.

    Used by internal notification fan-out (e.g. notify all coachers+admins of
    a new escalation). Always includes SUPERVISOR_CHAT_ID for admin perms.
    """
    s = settings()
    eligible_roles = PERMISSIONS.get(perm, frozenset())
    if not eligible_roles:
        return []
    placeholders = ",".join("?" * len(eligible_roles))
    rows = conn().execute(
        f"SELECT tg_id FROM users WHERE role IN ({placeholders}) "
        f"AND access_status = 'approved' AND status = 'active'",
        tuple(eligible_roles),
    ).fetchall()
    ids = [r["tg_id"] for r in rows]
    # Always include supervisor for admin-tier perms (even if their row isn't
    # ready yet on first boot).
    if "admin" in eligible_roles and s.supervisor_chat_id not in ids:
        ids.append(s.supervisor_chat_id)
    return ids


# --- Convenience for handlers ------------------------------------------

def require_perm(update, perm: str) -> bool:
    """Return True iff update.effective_user has the perm. Silent False otherwise."""
    user = update.effective_user
    if user is None:
        return False
    return has_perm(user.id, perm)

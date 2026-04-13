from __future__ import annotations

import os
import re
from typing import Any

from swingmusic.config import UserConfig
from swingmusic.db.production import InviteTokenTable, UserRootDirOwnershipTable
from swingmusic.db.userdata import PluginTable, UserTable
from swingmusic.services.library_projection import get_owner_user, sync_owner_projection
from swingmusic.store.homepage import HomepageStore
from swingmusic.utils.auth import hash_password


def get_bootstrap_status() -> dict[str, Any]:
    users = list(UserTable.get_all())
    owner = next((u for u in users if "owner" in u.roles), None)

    return {
        "required": len(users) == 0,
        "has_users": len(users) > 0,
        "user_count": len(users),
        "owner_exists": owner is not None,
        "owner_username": owner.username if owner else None,
    }


def _normalize_root_dirs(root_dirs: list[str] | None) -> list[str] | None:
    if root_dirs is None:
        return None

    cleaned = [item.strip() for item in root_dirs if item and item.strip()]
    return list(dict.fromkeys(cleaned))


def default_user_root_dir(username: str) -> str:
    config = UserConfig()
    if config.rootDirs:
        base = config.rootDirs[0]
        if base == "$home":
            root = os.path.join(os.path.expanduser("~"), "Music")
        else:
            root = os.path.expanduser(base)
    else:
        root = os.path.join(os.path.expanduser("~"), "Music")

    safe_username = re.sub(r"[^\w\-. ]", "", username).strip() or "user"
    return os.path.join(root, "SwingMusic Users", safe_username)


def bootstrap_owner_user(
    *,
    username: str,
    password: str,
    root_dirs: list[str] | None = None,
) -> Any:
    status = get_bootstrap_status()
    if not status["required"]:
        raise ValueError("Bootstrap is only available when no users exist")

    if not username.strip() or not password:
        raise ValueError("Username and password are required")

    if UserTable.get_by_username(username):
        raise ValueError("Username already exists")

    UserTable.insert_one(
        {
            "username": username.strip(),
            "password": hash_password(password),
            "roles": ["owner", "admin", "user"],
        }
    )

    owner = UserTable.get_by_username(username.strip())
    if not owner:
        raise ValueError("Failed to create owner")

    if root_dirs is not None:
        config = UserConfig()
        config.rootDirs = _normalize_root_dirs(root_dirs) or []

    # Ensure in-memory homepage structures include the new user.
    HomepageStore.entries["recently_played"].add_new_user(owner.id)
    sync_owner_projection(owner.id)

    return owner


def create_invite_token(
    *,
    created_by: int,
    roles: list[str] | None = None,
    expires_in_seconds: int = 7 * 24 * 3600,
) -> Any:
    return InviteTokenTable.create_token(
        created_by=created_by,
        roles=roles or ["user"],
        expires_in_seconds=expires_in_seconds,
        extra={"purpose": "user_onboarding"},
    )


def accept_invite_token(
    *,
    token: str,
    username: str,
    password: str,
) -> Any:
    if not username.strip() or not password:
        raise ValueError("Username and password are required")

    invite = InviteTokenTable.get_valid_token(token)
    if not invite:
        raise ValueError("Invite token is invalid or expired")

    if UserTable.get_by_username(username.strip()):
        raise ValueError("Username already exists")

    UserTable.insert_one(
        {
            "username": username.strip(),
            "password": hash_password(password),
            "roles": invite.roles or ["user"],
        }
    )

    user = UserTable.get_by_username(username.strip())
    if not user:
        raise ValueError("Failed to create user from invite")

    InviteTokenTable.consume_token(token, used_by=user.id)
    user_root = default_user_root_dir(user.username)
    os.makedirs(user_root, exist_ok=True)
    UserRootDirOwnershipTable.assign_paths(user.id, [user_root])
    HomepageStore.entries["recently_played"].add_new_user(user.id)
    return user


def ensure_owner_and_projection() -> None:
    """
    Startup safety net for existing installs:
    - Guarantees there is exactly one logical owner role holder.
    - Projects existing indexed tracks to owner ownership without data loss.
    """
    status = get_bootstrap_status()
    if status["required"]:
        return

    owner = get_owner_user()
    if not owner:
        return

    # Keep per-user homepage recents maps initialized.
    for user in UserTable.get_all():
        HomepageStore.entries["recently_played"].items.setdefault(user.id, [])
        if UserRootDirOwnershipTable.get_paths(user.id):
            continue

        # Existing owner/admin users can continue to use configured roots.
        if "owner" in user.roles or "admin" in user.roles:
            UserRootDirOwnershipTable.assign_paths(user.id, UserConfig().rootDirs or [])
            continue

        user_root = default_user_root_dir(user.username)
        os.makedirs(user_root, exist_ok=True)
        UserRootDirOwnershipTable.assign_paths(user.id, [user_root])

    sync_owner_projection(owner.id)


def ensure_lyrics_defaults() -> None:
    """
    Force lyrics auto retrieval defaults to enabled in production mode.
    """
    plugin = PluginTable.get_by_name("lyrics_finder")
    if not plugin:
        return

    settings = plugin.settings or {}
    settings["auto_download"] = True
    settings["overide_unsynced"] = True

    PluginTable.activate("lyrics_finder", True)
    PluginTable.update_settings("lyrics_finder", settings)

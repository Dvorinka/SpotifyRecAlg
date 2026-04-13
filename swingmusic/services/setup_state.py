from __future__ import annotations

import threading
from typing import Any

from swingmusic.config import UserConfig
from swingmusic.db.production import SetupStateTable, UserRootDirOwnershipTable
from swingmusic.db.userdata import UserTable
from swingmusic.lib.index import run_index_pipeline
from swingmusic.services.production_readiness import bootstrap_owner_user

_index_lock = threading.RLock()
_index_thread: threading.Thread | None = None


def _normalize_root_dirs(root_dirs: list[str] | None) -> list[str]:
    if not root_dirs:
        return []
    cleaned = [item.strip() for item in root_dirs if item and item.strip()]
    return list(dict.fromkeys(cleaned))


def _owner_user():
    users = list(UserTable.get_all())
    owners = [user for user in users if "owner" in user.roles]
    if owners:
        return owners[0]

    admins = [user for user in users if "admin" in user.roles]
    if admins:
        return admins[0]

    return users[0] if users else None


def _primary_music_dir() -> str | None:
    root_dirs = UserConfig().rootDirs or []
    if not root_dirs:
        return None
    return root_dirs[0]


def _reconcile_legacy_ready_state() -> Any:
    row = SetupStateTable.ensure_singleton()
    owner = _owner_user()
    primary_dir = _primary_music_dir()

    owner_created = owner is not None
    directory_configured = bool(primary_dir)
    legacy_ready = owner_created and directory_configured and row.index_state == "idle"

    if legacy_ready and not row.setup_completed:
        row = SetupStateTable.update_state(
            {
                "setup_completed": True,
                "owner_userid": owner.id if owner else None,
                "primary_music_dir": primary_dir,
                "index_state": "completed",
                "index_progress": 100.0,
                "index_message": "Setup inferred from existing installation",
            }
        )

    return row


def get_setup_status() -> dict[str, Any]:
    row = _reconcile_legacy_ready_state()
    users = list(UserTable.get_all())
    owner = _owner_user()
    primary_dir = row.primary_music_dir or _primary_music_dir()

    owner_created = owner is not None
    directory_configured = bool(primary_dir)
    initial_index_completed = row.index_state == "completed"
    setup_completed = bool(
        row.setup_completed
        and owner_created
        and directory_configured
        and initial_index_completed
    )
    required = not setup_completed

    if not owner_created:
        stage = "owner"
    elif not directory_configured:
        stage = "directory"
    elif not initial_index_completed:
        stage = "indexing"
    else:
        stage = "completed"

    return {
        "required": required,
        "setup_completed": setup_completed,
        "stage": stage,
        "needs_owner": stage == "owner",
        "needs_directory": stage == "directory",
        "needs_index": stage == "indexing",
        "owner_created": owner_created,
        "owner_username": owner.username if owner else None,
        "owner_userid": owner.id if owner else None,
        "directory_configured": directory_configured,
        "primary_music_dir": primary_dir,
        "index_state": row.index_state,
        "index_progress": float(row.index_progress or 0.0),
        "index_message": row.index_message,
        "initial_index_completed": initial_index_completed,
        "has_users": len(users) > 0,
        "user_count": len(users),
    }


def _set_index_state(
    state: str, progress: float, message: str, extra: dict[str, Any] | None = None
):
    SetupStateTable.mark_index_progress(
        state=state,
        progress=progress,
        message=message,
        extra=extra,
    )


def _run_initial_index():
    try:
        _set_index_state("running", 1.0, "Starting initial index")

        def _progress(state: str, progress: float, message: str):
            _set_index_state(state, progress, message)

        run_index_pipeline(progress_callback=_progress)

        status = get_setup_status()
        SetupStateTable.update_state(
            {
                "setup_completed": bool(
                    status["owner_created"] and status["directory_configured"]
                ),
                "index_state": "completed",
                "index_progress": 100.0,
                "index_message": "Initial index completed",
                "owner_userid": status.get("owner_userid"),
                "primary_music_dir": status.get("primary_music_dir"),
            }
        )
    except Exception as error:
        SetupStateTable.update_state(
            {
                "setup_completed": False,
                "index_state": "failed",
                "index_message": str(error),
            }
        )
    finally:
        global _index_thread
        with _index_lock:
            _index_thread = None


def trigger_initial_index(force: bool = False) -> bool:
    global _index_thread
    with _index_lock:
        if _index_thread and _index_thread.is_alive():
            return False

        row = SetupStateTable.ensure_singleton()
        if not force and row.index_state == "running":
            return False

        SetupStateTable.update_state(
            {
                "index_state": "queued",
                "index_progress": 0.0,
                "index_message": "Queued initial index",
            }
        )

        _index_thread = threading.Thread(
            target=_run_initial_index,
            daemon=True,
            name="swingmusic-setup-index",
        )
        _index_thread.start()
        return True


def bootstrap_setup(
    *,
    username: str,
    password: str,
    root_dirs: list[str] | None = None,
):
    existing_users = list(UserTable.get_all())
    if existing_users:
        raise ValueError(
            "Setup bootstrap is only available before any user account exists"
        )

    normalized_root_dirs = _normalize_root_dirs(root_dirs)
    if not normalized_root_dirs:
        raise ValueError("At least one primary music directory is required")

    owner = bootstrap_owner_user(
        username=username,
        password=password,
        root_dirs=normalized_root_dirs,
    )

    primary_dir = (
        normalized_root_dirs[0] if normalized_root_dirs else _primary_music_dir()
    )
    SetupStateTable.update_state(
        {
            "owner_userid": owner.id,
            "primary_music_dir": primary_dir,
            "setup_completed": False,
            "index_state": "queued",
            "index_progress": 0.0,
            "index_message": "Bootstrap complete. Initial index queued.",
            "extra": {
                "onboarding_version": 1,
            },
        }
    )

    trigger_initial_index(force=True)
    return owner


def configure_primary_directory(
    *,
    root_dirs: list[str],
) -> bool:
    """
    Configure primary music directories when setup is incomplete and owner already exists.
    """
    normalized_root_dirs = _normalize_root_dirs(root_dirs)
    if not normalized_root_dirs:
        raise ValueError("At least one primary music directory is required")

    owner = _owner_user()
    if not owner:
        raise ValueError(
            "Owner account must exist before configuring music directories"
        )

    config = UserConfig()
    config.rootDirs = normalized_root_dirs
    UserRootDirOwnershipTable.assign_paths(owner.id, normalized_root_dirs)

    SetupStateTable.update_state(
        {
            "owner_userid": owner.id,
            "primary_music_dir": normalized_root_dirs[0],
            "setup_completed": False,
            "index_state": "queued",
            "index_progress": 0.0,
            "index_message": "Primary directory configured. Initial index queued.",
            "extra": {
                "onboarding_version": 1,
                "directory_configured_at": "setup_api",
            },
        }
    )

    return trigger_initial_index(force=True)


def is_setup_complete() -> bool:
    status = get_setup_status()
    return bool(status["setup_completed"])


def resume_pending_index_if_needed() -> bool:
    status = get_setup_status()
    if status["index_state"] in {"queued", "running"} and not status["setup_completed"]:
        return trigger_initial_index(force=True)
    return False

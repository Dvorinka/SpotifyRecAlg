from __future__ import annotations

import os
import time
from typing import Any

from sqlalchemy import func, select

from swingmusic.config import UserConfig
from swingmusic.db.engine import DbEngine
from swingmusic.db.libdata import TrackTable
from swingmusic.db.production import (
    LibraryFileTable,
    UserLibraryTrackTable,
    UserRootDirOwnershipTable,
)
from swingmusic.db.userdata import UserTable
from swingmusic.utils.auth import get_current_userid

TRACK_AVAILABLE = "available"
TRACK_MISSING = "missing"
TRACK_QUEUED = "queued"
TRACK_FAILED = "failed"
VALID_TRACK_STATES = {TRACK_AVAILABLE, TRACK_MISSING, TRACK_QUEUED, TRACK_FAILED}


def _infer_codec(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower().lstrip(".")
    return ext or "unknown"


def _infer_quality_from_bitrate(bitrate: int) -> str:
    if bitrate >= 1400:
        return "lossless"
    if bitrate >= 256:
        return "high"
    if bitrate >= 160:
        return "medium"
    return "low"


def get_owner_user() -> Any | None:
    users = list(UserTable.get_all())
    if not users:
        return None

    owners = [u for u in users if "owner" in u.roles]
    if owners:
        return owners[0]

    admins = [u for u in users if "admin" in u.roles]
    if admins:
        owner = admins[0]
        roles = list(dict.fromkeys([*owner.roles, "owner"]))
        UserTable.update_one({"id": owner.id, "roles": roles})
        return UserTable.get_by_id(owner.id)

    fallback = users[0]
    roles = list(dict.fromkeys([*fallback.roles, "admin", "owner"]))
    UserTable.update_one({"id": fallback.id, "roles": roles})
    return UserTable.get_by_id(fallback.id)


def sync_library_files_from_index() -> None:
    for track in TrackTable.get_all():
        LibraryFileTable.upsert_from_local_track(
            trackhash=track.trackhash,
            filepath=track.filepath,
            bitrate=track.bitrate,
            codec=_infer_codec(track.filepath),
            quality=_infer_quality_from_bitrate(track.bitrate),
            source="local",
        )


def sync_owner_projection(owner_user_id: int | None = None) -> None:
    owner = UserTable.get_by_id(owner_user_id) if owner_user_id else get_owner_user()
    if not owner:
        return

    sync_library_files_from_index()

    for track in TrackTable.get_all():
        file_row = LibraryFileTable.get_by_trackhash(track.trackhash)
        UserLibraryTrackTable.upsert_status(
            userid=owner.id,
            trackhash=track.trackhash,
            status=TRACK_AVAILABLE,
            file_id=file_row.id if file_row else None,
            extra={"source": "migration", "updated_from": "local_scan"},
        )

    root_dirs = UserConfig().rootDirs or []
    UserRootDirOwnershipTable.assign_paths(owner.id, root_dirs)


def ensure_projection_for_user(userid: int, trackhashes: list[str] | set[str]) -> None:
    trackhashes = set(trackhashes)
    if not trackhashes:
        return

    existing = UserLibraryTrackTable.get_status_map(userid, trackhashes)

    for trackhash in trackhashes:
        if trackhash in existing:
            continue

        file_row = LibraryFileTable.get_by_trackhash(trackhash)
        UserLibraryTrackTable.upsert_status(
            userid=userid,
            trackhash=trackhash,
            status=TRACK_MISSING,
            file_id=file_row.id if file_row else None,
            extra={"projection": "auto_created"},
        )


def get_import_candidate_counts(
    userid: int, trackhashes: list[str] | set[str]
) -> dict[str, int]:
    trackhashes = set(trackhashes)
    if not trackhashes:
        return {}

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(
                UserLibraryTrackTable.trackhash,
                func.count(UserLibraryTrackTable.id).label("count"),
            )
            .where(UserLibraryTrackTable.trackhash.in_(trackhashes))
            .where(UserLibraryTrackTable.userid != userid)
            .where(UserLibraryTrackTable.status == TRACK_AVAILABLE)
            .group_by(UserLibraryTrackTable.trackhash)
        )
        rows = result.fetchall()

    return {row.trackhash: int(row.count) for row in rows}


def _state_to_action(state: str, candidate_count: int) -> dict[str, Any]:
    if state == TRACK_AVAILABLE:
        return {"type": "none", "label": "Available", "enabled": False}
    if state == TRACK_QUEUED:
        return {"type": "queued", "label": "Queued", "enabled": False}
    if state == TRACK_FAILED:
        return {"type": "retry", "label": "Retry download", "enabled": True}

    if candidate_count > 0:
        return {
            "type": "import_or_download",
            "label": "Import or download",
            "enabled": True,
        }

    return {"type": "download", "label": "Download", "enabled": True}


def _import_action(state: str, candidate_count: int) -> dict[str, Any]:
    enabled = candidate_count > 0 and state != TRACK_AVAILABLE
    return {
        "type": "import",
        "label": "Import existing",
        "enabled": enabled,
    }


def _quality_badge(quality: str | None) -> dict[str, str]:
    quality = (quality or "unknown").lower()
    mapping = {
        "lossless": {"label": "Lossless", "color": "green"},
        "high": {"label": "High", "color": "blue"},
        "medium": {"label": "Medium", "color": "orange"},
        "low": {"label": "Low", "color": "gray"},
        "unknown": {"label": "Unknown", "color": "gray"},
    }
    return mapping.get(quality, mapping["unknown"])


def get_track_availability_map(
    trackhashes: list[str] | set[str],
    userid: int | None = None,
) -> dict[str, dict[str, Any]]:
    userid = userid or get_current_userid()
    trackhashes = set(trackhashes)
    if not trackhashes:
        return {}

    ensure_projection_for_user(userid, trackhashes)

    status_rows = UserLibraryTrackTable.get_status_map(userid, trackhashes)
    candidate_counts = get_import_candidate_counts(userid, trackhashes)
    file_ids = {row.file_id for row in status_rows.values() if row.file_id}
    file_rows: dict[int, Any] = {}

    if file_ids:
        with DbEngine.manager() as conn:
            result = conn.execute(
                select(LibraryFileTable).where(LibraryFileTable.id.in_(file_ids))
            )
            for file_row in result.scalars():
                file_rows[file_row.id] = file_row

    availability: dict[str, dict[str, Any]] = {}

    for trackhash in trackhashes:
        row = status_rows.get(trackhash)
        state = (
            row.status if row and row.status in VALID_TRACK_STATES else TRACK_MISSING
        )
        candidate_count = candidate_counts.get(trackhash, 0)
        file_row = file_rows.get(row.file_id) if row and row.file_id else None
        quality = file_row.quality if file_row else None

        availability[trackhash] = {
            "state": state,
            "candidate_count": candidate_count,
            "import_available": candidate_count > 0 and state != TRACK_AVAILABLE,
            "import_action": _import_action(state, candidate_count),
            "download_action": _state_to_action(state, candidate_count),
            "quality": quality,
            "quality_badge": _quality_badge(quality),
        }

    return availability


def get_track_availability(trackhash: str, userid: int | None = None) -> dict[str, Any]:
    return get_track_availability_map({trackhash}, userid).get(
        trackhash,
        {
            "state": TRACK_MISSING,
            "candidate_count": 0,
            "import_available": False,
            "import_action": _import_action(TRACK_MISSING, 0),
            "download_action": _state_to_action(TRACK_MISSING, 0),
            "quality": None,
            "quality_badge": _quality_badge(None),
        },
    )


def list_import_candidates(
    trackhash: str, userid: int | None = None
) -> list[dict[str, Any]]:
    userid = userid or get_current_userid()

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(UserLibraryTrackTable, UserTable)
            .join(UserTable, UserTable.id == UserLibraryTrackTable.userid)
            .where(UserLibraryTrackTable.trackhash == trackhash)
            .where(UserLibraryTrackTable.userid != userid)
            .where(UserLibraryTrackTable.status == TRACK_AVAILABLE)
        )

        rows = result.fetchall()

    candidates: list[dict[str, Any]] = []
    for projection, user in rows:
        candidates.append(
            {
                "user_id": user.id,
                "username": user.username,
                "file_id": projection.file_id,
                "trackhash": projection.trackhash,
            }
        )

    return candidates


def import_existing_track(
    trackhash: str,
    *,
    userid: int | None = None,
    source_userid: int | None = None,
) -> bool:
    userid = userid or get_current_userid()
    candidates = list_import_candidates(trackhash, userid)

    if not candidates:
        return False

    candidate = candidates[0]
    if source_userid is not None:
        for item in candidates:
            if item["user_id"] == source_userid:
                candidate = item
                break

    file_id = candidate.get("file_id")
    UserLibraryTrackTable.upsert_status(
        userid=userid,
        trackhash=trackhash,
        status=TRACK_AVAILABLE,
        file_id=file_id,
        extra={
            "imported_from_user": candidate["user_id"],
            "imported_at": int(time.time()),
        },
    )
    return True


def mark_track_queued(
    trackhash: str,
    *,
    job_id: int,
    source_url: str | None,
    userid: int | None = None,
) -> None:
    userid = userid or get_current_userid()
    UserLibraryTrackTable.upsert_status(
        userid=userid,
        trackhash=trackhash,
        status=TRACK_QUEUED,
        download_job_id=job_id,
        source_url=source_url,
        extra={"queued_at": int(time.time())},
    )


def mark_track_failed(
    trackhash: str,
    *,
    error: str,
    job_id: int | None = None,
    userid: int | None = None,
) -> None:
    userid = userid or get_current_userid()
    UserLibraryTrackTable.upsert_status(
        userid=userid,
        trackhash=trackhash,
        status=TRACK_FAILED,
        download_job_id=job_id,
        error=error,
        extra={"failed_at": int(time.time())},
    )


def mark_track_available(
    trackhash: str,
    *,
    filepath: str,
    bitrate: int,
    userid: int | None = None,
    source: str = "download",
) -> None:
    userid = userid or get_current_userid()
    file_row = LibraryFileTable.upsert_from_local_track(
        trackhash=trackhash,
        filepath=filepath,
        bitrate=bitrate,
        codec=_infer_codec(filepath),
        quality=_infer_quality_from_bitrate(bitrate),
        source=source,
    )

    UserLibraryTrackTable.upsert_status(
        userid=userid,
        trackhash=trackhash,
        status=TRACK_AVAILABLE,
        file_id=file_row.id if file_row else None,
        error=None,
        extra={"available_at": int(time.time())},
    )

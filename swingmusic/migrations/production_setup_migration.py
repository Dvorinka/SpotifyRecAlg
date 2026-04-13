from __future__ import annotations

import os
import re
import time
from pathlib import Path

from swingmusic.config import UserConfig
from swingmusic.db.libdata import TrackTable
from swingmusic.db.production import (
    LyricsStatusTable,
    SetupStateTable,
    TrackedPlaylistTable,
    UserRootDirOwnershipTable,
)
from swingmusic.db.userdata import UserTable
from swingmusic.migrations.base import Migration
from swingmusic.services.library_projection import get_owner_user, sync_owner_projection


class Migration001EnsureSetupState(Migration):
    @staticmethod
    def migrate():
        SetupStateTable.ensure_singleton()


class Migration002SyncOwnerProjection(Migration):
    @staticmethod
    def migrate():
        owner = get_owner_user()
        if not owner:
            return
        sync_owner_projection(owner.id)


class Migration003BackfillLyricsStatus(Migration):
    @staticmethod
    def migrate():
        for track in TrackTable.get_all():
            filepath = track.filepath
            if not filepath:
                continue

            track_path = Path(filepath)
            has_lrc = (
                track_path.with_suffix(".lrc").exists()
                or track_path.with_suffix(".elrc").exists()
            )
            has_embedded = bool((track.extra or {}).get("lyrics"))

            if has_embedded:
                status = "embedded"
                source = "tags"
            elif has_lrc:
                status = "lrc"
                source = "lrc"
            else:
                status = "missing"
                source = None

            LyricsStatusTable.upsert(
                trackhash=track.trackhash,
                filepath=filepath,
                status=status,
                source=source,
                has_embedded=has_embedded,
                has_lrc=has_lrc,
                last_error=None,
                extra={"migration": "backfill"},
                increment_attempt=False,
            )


class Migration004BackfillUserRootOwnership(Migration):
    @staticmethod
    def migrate():
        config_roots = UserConfig().rootDirs or []
        if config_roots:
            primary_root = config_roots[0]
            if primary_root == "$home":
                base_root = os.path.join(os.path.expanduser("~"), "Music")
            else:
                base_root = os.path.expanduser(primary_root)
        else:
            base_root = os.path.join(os.path.expanduser("~"), "Music")

        for user in UserTable.get_all():
            if UserRootDirOwnershipTable.get_paths(user.id):
                continue

            if "owner" in user.roles or "admin" in user.roles:
                UserRootDirOwnershipTable.assign_paths(user.id, config_roots)
                continue

            safe_username = (
                re.sub(r"[^\w\-. ]", "", user.username or "").strip()
                or f"user-{user.id}"
            )
            user_root = os.path.join(base_root, "SwingMusic Users", safe_username)
            os.makedirs(user_root, exist_ok=True)
            UserRootDirOwnershipTable.assign_paths(user.id, [user_root])


class Migration005NormalizeTrackedPlaylists(Migration):
    @staticmethod
    def migrate():
        now = int(time.time())
        for row in TrackedPlaylistTable.all().scalars():
            interval = max(120, int(row.sync_interval_seconds or 900))
            update_payload = {}

            if int(row.sync_interval_seconds or 0) != interval:
                update_payload["sync_interval_seconds"] = interval

            if not row.next_sync_at:
                update_payload["next_sync_at"] = int(
                    row.updated_at or row.created_at or now
                )

            if row.status not in {"active", "syncing", "failed", "paused", "deleted"}:
                update_payload["status"] = "active"

            if row.snapshot_track_ids is None:
                update_payload["snapshot_track_ids"] = []

            if row.last_result is None:
                update_payload["last_result"] = {}

            if update_payload:
                TrackedPlaylistTable.update_row(row.id, update_payload)

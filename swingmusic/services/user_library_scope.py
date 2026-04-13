from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import and_, select

from swingmusic.config import UserConfig
from swingmusic.db.engine import DbEngine
from swingmusic.db.production import UserLibraryTrackTable, UserRootDirOwnershipTable
from swingmusic.db.userdata import UserTable
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.auth import get_current_userid


def _normalize_path(path: str) -> str:
    resolved = Path(path).resolve().as_posix()
    return resolved.rstrip("/")


def _is_owner_user(userid: int) -> bool:
    user = UserTable.get_by_id(userid)
    if not user:
        return False
    return "owner" in user.roles or "admin" in user.roles


def get_available_trackhashes(userid: int | None = None) -> set[str]:
    userid = userid or get_current_userid()

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(UserLibraryTrackTable.trackhash).where(
                and_(
                    UserLibraryTrackTable.userid == userid,
                    UserLibraryTrackTable.status == "available",
                )
            )
        )
        return set(result.scalars().all())


def filter_trackhashes_for_user(
    trackhashes: Iterable[str], userid: int | None = None
) -> list[str]:
    userid = userid or get_current_userid()
    available = get_available_trackhashes(userid)
    seen: set[str] = set()
    filtered: list[str] = []

    for trackhash in trackhashes:
        if not trackhash or trackhash not in available or trackhash in seen:
            continue
        seen.add(trackhash)
        filtered.append(trackhash)

    return filtered


def get_visible_albums(userid: int | None = None):
    userid = userid or get_current_userid()
    available = get_available_trackhashes(userid)
    if not available:
        return []

    albums = []
    for entry in AlbumStore.albummap.values():
        if set(entry.trackhashes).intersection(available):
            albums.append(entry.album)

    return albums


def get_visible_artists(userid: int | None = None):
    userid = userid or get_current_userid()
    available = get_available_trackhashes(userid)
    if not available:
        return []

    artists = []
    for entry in ArtistStore.artistmap.values():
        if set(entry.trackhashes).intersection(available):
            artists.append(entry.artist)

    return artists


def get_user_root_dirs(userid: int | None = None) -> list[str]:
    userid = userid or get_current_userid()

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(UserRootDirOwnershipTable.path).where(
                UserRootDirOwnershipTable.userid == userid
            )
        )
        owned_paths = [row for row in result.scalars().all() if row]

    if owned_paths:
        return list(dict.fromkeys(owned_paths))

    # Backward-compatibility: owner/admin users can access configured root dirs
    # even if ownership rows have not been backfilled yet.
    if _is_owner_user(userid):
        return list(UserConfig().rootDirs or [])

    return []


def is_path_within_user_roots(filepath: str, userid: int | None = None) -> bool:
    userid = userid or get_current_userid()
    resolved_path = Path(filepath).resolve()
    roots = get_user_root_dirs(userid)

    for root in roots:
        root_path = Path.home().resolve() if root == "$home" else Path(root).resolve()
        if resolved_path == root_path or root_path in resolved_path.parents:
            return True

    return False


def count_visible_tracks_in_paths(
    paths: Iterable[str], userid: int | None = None
) -> dict[str, int]:
    userid = userid or get_current_userid()
    available = get_available_trackhashes(userid)
    normalized_paths = [_normalize_path(path) for path in paths if path]
    counts = dict.fromkeys(normalized_paths, 0)

    if not normalized_paths or not available:
        return counts

    for trackhash in available:
        group = TrackStore.trackhashmap.get(trackhash)
        if not group:
            continue

        best_track = group.get_best()
        filepath = Path(best_track.filepath).resolve().as_posix()

        for path in normalized_paths:
            if filepath.startswith(path + "/") or filepath == path:
                counts[path] += 1

    return counts

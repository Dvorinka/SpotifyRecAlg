from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Any

from sqlalchemy import and_, insert, select, update

from swingmusic.db.engine import DbEngine
from swingmusic.db.libdata import TrackTable
from swingmusic.db.production import DownloadJobTable, TrackedPlaylistTable
from swingmusic.db.userdata import PlaylistTable
from swingmusic.services.download_jobs import download_job_manager
from swingmusic.services.library_projection import get_track_availability_map
from swingmusic.services.spotify_metadata_client import (
    SpotifyTrack,
    get_spotify_metadata_client,
)
from swingmusic.services.universal_url_parser import universal_url_parser
from swingmusic.utils.dates import create_new_date
from swingmusic.utils.hashing import create_hash

log = logging.getLogger(__name__)

_SPOTIFY_PLAYLIST_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:open\.)?spotify\.com/playlist/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def _quality_codec_pair(quality: str | None, codec: str | None) -> tuple[str, str]:
    quality_name = (quality or "lossless").lower().strip()
    codec_name = (codec or "flac").lower().strip()

    quality_map = {
        "flac": ("lossless", "flac"),
        "lossless": ("lossless", "flac"),
        "high": ("high", "mp3"),
        "medium": ("medium", "mp3"),
        "low": ("low", "mp3"),
        "mp3_320": ("high", "mp3"),
        "mp3_256": ("medium", "mp3"),
        "mp3_192": ("medium", "mp3"),
        "mp3_128": ("low", "mp3"),
    }

    if quality_name in quality_map:
        return quality_map[quality_name]

    if codec_name == "flac":
        return ("lossless", "flac")

    if quality_name not in {"lossless", "high", "medium", "low"}:
        quality_name = "high"

    if codec_name not in {"flac", "mp3", "aac", "ogg", "opus", "m4a"}:
        codec_name = "mp3"

    return (quality_name, codec_name)


def _parse_spotify_playlist_id(url: str) -> str | None:
    parsed = universal_url_parser.parse_url(url)
    if parsed and parsed.service.value == "spotify" and parsed.item_type == "playlist":
        return parsed.id

    match = _SPOTIFY_PLAYLIST_URL_PATTERN.search(url or "")
    if match:
        return match.group(1)

    return None


def _parse_trackable_playlist_source(url: str) -> tuple[str, str, str] | None:
    """
    Returns (service, item_type, item_id) for trackable external list sources.
    """
    parsed = universal_url_parser.parse_url(url)
    if not parsed:
        return None

    item_type = (parsed.item_type or "").lower()
    if item_type != "playlist":
        return None

    service = parsed.service.value
    item_id = parsed.id or ""
    if not item_id:
        return None

    return service, item_type, item_id


def _trackhash_from_spotify_track(track: SpotifyTrack) -> str | None:
    title = (track.name or "").strip()
    artist_names = [
        artist.get("name", "") for artist in (track.artists or []) if artist.get("name")
    ]
    artist = ", ".join([name for name in artist_names if name]).strip()
    album = ""
    if isinstance(track.album, dict):
        album = (track.album.get("name") or "").strip()

    if not title or not artist:
        return None

    return create_hash(title, album, artist)


def _snapshot_hash(track_ids: list[str]) -> str:
    joined = "\n".join(track_ids)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _tracked_playlist_name(service: str, title: str | None, playlist_id: str) -> str:
    base = (title or "").strip() or f"{service.title()} Playlist {playlist_id[:12]}"
    return f"[Tracked] {base}"[:180]


def _find_mirror_playlist_row(
    userid: int, tracked_id: int, local_playlist_id: int | None = None
) -> Any | None:
    with DbEngine.manager() as conn:
        if local_playlist_id:
            row = conn.execute(
                select(PlaylistTable).where(
                    and_(
                        PlaylistTable.id == local_playlist_id,
                        PlaylistTable.userid == userid,
                    )
                )
            ).scalar_one_or_none()
            if row:
                return row

        rows = list(
            conn.execute(
                select(PlaylistTable).where(PlaylistTable.userid == userid)
            ).scalars()
        )

    for row in rows:
        extra = row.extra or {}
        if not isinstance(extra, dict):
            continue
        tracked_extra = extra.get("tracked_playlist") or {}
        if not isinstance(tracked_extra, dict):
            continue
        if _as_int(tracked_extra.get("tracked_id")) == tracked_id:
            return row
    return None


def _sync_mirror_playlist(
    *,
    tracked_row: Any,
    playlist_title: str | None,
    owner_name: str | None,
    ordered_trackhashes: list[str],
    snapshot_track_ids: list[str],
) -> int | None:
    userid = int(tracked_row.userid)
    row_extra = tracked_row.extra or {}
    local_playlist_id = (
        _as_int(row_extra.get("local_playlist_id"))
        if isinstance(row_extra, dict)
        else None
    )

    mirror_row = _find_mirror_playlist_row(userid, tracked_row.id, local_playlist_id)
    playlist_name = _tracked_playlist_name(
        tracked_row.service, playlist_title, tracked_row.playlist_id
    )
    now = int(time.time())

    tracked_meta = {
        "tracked_id": tracked_row.id,
        "service": tracked_row.service,
        "playlist_id": tracked_row.playlist_id,
        "source_url": tracked_row.source_url,
        "owner_name": owner_name,
        "last_synced_at": now,
        "snapshot_track_count": len(snapshot_track_ids),
    }

    if mirror_row:
        playlist_extra = mirror_row.extra if isinstance(mirror_row.extra, dict) else {}
        playlist_extra = {
            **playlist_extra,
            "managed": True,
            "tracked_playlist": tracked_meta,
        }
        with DbEngine.manager(commit=True) as conn:
            conn.execute(
                update(PlaylistTable)
                .where(
                    and_(
                        PlaylistTable.id == mirror_row.id,
                        PlaylistTable.userid == userid,
                    )
                )
                .values(
                    name=playlist_name,
                    last_updated=create_new_date(),
                    trackhashes=ordered_trackhashes,
                    extra=playlist_extra,
                )
            )
        return int(mirror_row.id)

    playlist_settings = {
        "has_gif": False,
        "banner_pos": 50,
        "square_img": False,
        "pinned": False,
    }
    playlist_extra = {
        "managed": True,
        "tracked_playlist": tracked_meta,
    }
    with DbEngine.manager(commit=True) as conn:
        result = conn.execute(
            insert(PlaylistTable).values(
                name=playlist_name,
                image=None,
                last_updated=create_new_date(),
                userid=userid,
                settings=playlist_settings,
                trackhashes=ordered_trackhashes,
                extra=playlist_extra,
            )
        )

    inserted_id = None
    try:
        inserted_id = _as_int(result.inserted_primary_key[0])
    except Exception:
        inserted_id = None
    if inserted_id is None:
        inserted_id = _as_int(getattr(result, "lastrowid", None))
    return inserted_id


def _serialize_tracked_playlist(row: Any) -> dict[str, Any]:
    row_extra = row.extra if isinstance(row.extra, dict) else {}
    return {
        "id": row.id,
        "userid": row.userid,
        "service": row.service,
        "playlist_id": row.playlist_id,
        "source_url": row.source_url,
        "title": row.title,
        "owner_name": row.owner_name,
        "quality": row.quality,
        "codec": row.codec,
        "auto_sync": bool(row.auto_sync),
        "sync_interval_seconds": int(row.sync_interval_seconds or 0),
        "next_sync_at": row.next_sync_at,
        "last_sync_at": row.last_sync_at,
        "status": row.status,
        "snapshot_track_count": len(row.snapshot_track_ids or []),
        "snapshot_hash": row.snapshot_hash,
        "local_playlist_id": _as_int(row_extra.get("local_playlist_id")),
        "last_result": row.last_result or {},
        "last_error": row.last_error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _has_active_sync_job(userid: int, tracked_id: int) -> bool:
    with DbEngine.manager() as conn:
        result = conn.execute(
            select(DownloadJobTable)
            .where(
                and_(
                    DownloadJobTable.userid == userid,
                    DownloadJobTable.state.in_(["queued", "downloading"]),
                )
            )
            .order_by(DownloadJobTable.created_at.desc())
            .limit(200)
        )
        jobs = list(result.scalars())

    for job in jobs:
        payload = job.payload or {}
        if payload.get("tracked_playlist_id") == tracked_id:
            return True
    return False


def _latest_completed_sync_job(userid: int, tracked_id: int) -> Any | None:
    with DbEngine.manager() as conn:
        result = conn.execute(
            select(DownloadJobTable)
            .where(
                and_(
                    DownloadJobTable.userid == userid,
                    DownloadJobTable.state == "completed",
                )
            )
            .order_by(DownloadJobTable.created_at.desc())
            .limit(300)
        )
        jobs = list(result.scalars())

    for job in jobs:
        payload = job.payload or {}
        if payload.get("tracked_playlist_id") == tracked_id:
            return job
    return None


def _collect_trackhashes_for_path(path: str | None) -> list[str]:
    if not path:
        return []

    scope_path = path if os.path.isdir(path) else os.path.dirname(path)
    if not scope_path or not os.path.exists(scope_path):
        return []

    tracks = TrackTable.get_tracks_in_path(scope_path)
    seen: set[str] = set()
    ordered: list[str] = []

    for track in tracks:
        trackhash = getattr(track, "trackhash", None)
        if not trackhash or trackhash in seen:
            continue
        seen.add(trackhash)
        ordered.append(trackhash)

    return ordered


def _snapshot_ids_from_trackhashes(trackhashes: list[str]) -> list[str]:
    return [f"trackhash:{trackhash}" for trackhash in trackhashes if trackhash]


class PlaylistTrackingService:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._processing: set[int] = set()
        self.poll_interval_seconds = int(
            max(
                15,
                min(
                    int(float(os.getenv("SWINGMUSIC_PLAYLIST_TRACKER_POLL", "30"))), 300
                ),
            )
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="playlist-tracking-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def list_tracked_playlists(self, userid: int) -> list[dict[str, Any]]:
        rows = TrackedPlaylistTable.list_for_user(userid)
        return [_serialize_tracked_playlist(row) for row in rows]

    def get_tracked_playlist(
        self, tracked_id: int, userid: int
    ) -> dict[str, Any] | None:
        row = TrackedPlaylistTable.get_by_id(tracked_id, userid=userid)
        if not row:
            return None
        return _serialize_tracked_playlist(row)

    def find_tracked_playlist(
        self, *, userid: int, service: str, playlist_id: str
    ) -> dict[str, Any] | None:
        row = TrackedPlaylistTable.get_by_source(
            userid=userid, service=service, playlist_id=playlist_id
        )
        if not row or row.status == "deleted":
            return None
        return _serialize_tracked_playlist(row)

    def track_playlist(
        self,
        *,
        userid: int,
        source_url: str,
        quality: str | None = None,
        codec: str | None = None,
        auto_sync: bool = True,
        sync_interval_seconds: int = 900,
        sync_now: bool = True,
    ) -> dict[str, Any]:
        source = _parse_trackable_playlist_source(source_url)
        if not source:
            raise ValueError("Only trackable playlist links are supported")

        service, item_type, playlist_id = source

        quality_name, codec_name = _quality_codec_pair(quality, codec)
        interval = max(120, min(int(sync_interval_seconds or 900), 24 * 3600))
        now = int(time.time())

        playlist = None
        owner_name = None
        title = None

        if service == "spotify":
            client = get_spotify_metadata_client()
            playlist = client.get_playlist(playlist_id)
            if playlist:
                owner_name = (playlist.owner or {}).get("display_name")
                title = playlist.name

        existing = TrackedPlaylistTable.get_by_source(
            userid=userid,
            service=service,
            playlist_id=playlist_id,
        )
        existing_extra = (
            existing.extra if existing and isinstance(existing.extra, dict) else {}
        )
        merged_extra = {
            **existing_extra,
            "item_type": item_type,
            "service": service,
        }

        tracked = TrackedPlaylistTable.upsert(
            userid=userid,
            service=service,
            playlist_id=playlist_id,
            source_url=source_url,
            values={
                "title": title,
                "owner_name": owner_name,
                "quality": quality_name,
                "codec": codec_name,
                "auto_sync": bool(auto_sync),
                "sync_interval_seconds": interval,
                "next_sync_at": now,
                "status": "active",
                "last_error": None,
                "extra": merged_extra,
            },
        )

        result: dict[str, Any] = {
            "tracked": _serialize_tracked_playlist(tracked),
            "sync": None,
        }

        if sync_now:
            result["sync"] = self.sync_tracked_playlist(
                tracked.id, userid=userid, force=True
            )
            refreshed = TrackedPlaylistTable.get_by_id(tracked.id, userid=userid)
            if refreshed:
                result["tracked"] = _serialize_tracked_playlist(refreshed)

        return result

    def sync_tracked_playlist(
        self, tracked_id: int, *, userid: int, force: bool = False
    ) -> dict[str, Any]:
        with self._lock:
            if tracked_id in self._processing:
                return {
                    "success": False,
                    "tracked_id": tracked_id,
                    "message": "Sync already in progress",
                }
            self._processing.add(tracked_id)

        try:
            row = TrackedPlaylistTable.get_by_id(tracked_id, userid=userid)
            if not row:
                return {
                    "success": False,
                    "tracked_id": tracked_id,
                    "message": "Tracked playlist not found",
                }

            if row.status == "deleted":
                return {
                    "success": False,
                    "tracked_id": tracked_id,
                    "message": "Tracked playlist is deleted",
                }

            if not force and not row.auto_sync:
                return {
                    "success": True,
                    "tracked_id": tracked_id,
                    "message": "Auto-sync is disabled",
                    "queued_tracks": 0,
                    "added_tracks": 0,
                    "removed_tracks": 0,
                    "reordered_tracks": 0,
                }

            now = int(time.time())
            TrackedPlaylistTable.update_row(
                row.id,
                {
                    "status": "syncing",
                    "last_error": None,
                    "next_sync_at": now
                    + max(120, int(row.sync_interval_seconds or 900)),
                },
            )

            # Generic multi-platform fallback:
            # for non-Spotify playlist providers we still keep the link tracked and
            # periodically queue a playlist-level refresh job.
            if row.service != "spotify":
                old_snapshot_ids = [
                    track_id for track_id in (row.snapshot_track_ids or []) if track_id
                ]
                new_snapshot_ids = list(old_snapshot_ids)
                resolved_trackhashes: list[str] = []

                latest_job = _latest_completed_sync_job(userid, row.id)
                if latest_job:
                    resolved_trackhashes = _collect_trackhashes_for_path(
                        latest_job.target_path
                    )
                    new_snapshot_ids = _snapshot_ids_from_trackhashes(
                        resolved_trackhashes
                    )
                elif old_snapshot_ids and all(
                    str(track_id).startswith("trackhash:")
                    for track_id in old_snapshot_ids
                ):
                    resolved_trackhashes = [
                        str(track_id).split(":", 1)[1]
                        for track_id in old_snapshot_ids
                        if ":" in str(track_id)
                    ]

                old_set = set(old_snapshot_ids)
                new_set = set(new_snapshot_ids)
                added_items = [
                    track_id for track_id in new_snapshot_ids if track_id not in old_set
                ]
                removed_items = [
                    track_id for track_id in old_snapshot_ids if track_id not in new_set
                ]
                old_positions = {
                    track_id: index for index, track_id in enumerate(old_snapshot_ids)
                }
                reordered_items = 0
                for index, track_id in enumerate(new_snapshot_ids):
                    previous = old_positions.get(track_id)
                    if previous is not None and previous != index:
                        reordered_items += 1

                local_playlist_id = _sync_mirror_playlist(
                    tracked_row=row,
                    playlist_title=row.title,
                    owner_name=row.owner_name,
                    ordered_trackhashes=resolved_trackhashes,
                    snapshot_track_ids=new_snapshot_ids,
                )

                if _has_active_sync_job(userid, row.id):
                    summary = {
                        "success": True,
                        "tracked_id": row.id,
                        "playlist_id": row.playlist_id,
                        "playlist_title": row.title or row.playlist_id,
                        "local_playlist_id": local_playlist_id,
                        "total_tracks": len(new_snapshot_ids),
                        "added_tracks": len(added_items),
                        "removed_tracks": len(removed_items),
                        "reordered_tracks": reordered_items,
                        "queued_tracks": 0,
                        "skipped_tracks": 1,
                        "queue_errors": 0,
                        "synced_at": now,
                        "message": "Active job already exists for this tracked source",
                    }
                else:
                    item_type = str((row.extra or {}).get("item_type") or "playlist")
                    try:
                        download_job_manager.enqueue(
                            userid=userid,
                            source_url=row.source_url,
                            source=row.service,
                            quality=row.quality,
                            codec=row.codec,
                            title=row.title,
                            artist=row.owner_name,
                            album=None,
                            item_type=item_type,
                            payload={
                                "tracked_playlist_id": row.id,
                                "playlist_id": row.playlist_id,
                                "playlist_title": row.title or row.playlist_id,
                                "sync_reason": "scheduled_refresh",
                            },
                        )
                        summary = {
                            "success": True,
                            "tracked_id": row.id,
                            "playlist_id": row.playlist_id,
                            "playlist_title": row.title or row.playlist_id,
                            "local_playlist_id": local_playlist_id,
                            "total_tracks": len(new_snapshot_ids),
                            "added_tracks": len(added_items),
                            "removed_tracks": len(removed_items),
                            "reordered_tracks": reordered_items,
                            "queued_tracks": 1,
                            "skipped_tracks": 0,
                            "queue_errors": 0,
                            "synced_at": now,
                            "message": f"Queued {row.service} playlist refresh",
                        }
                    except Exception as queue_error:
                        summary = {
                            "success": False,
                            "tracked_id": row.id,
                            "playlist_id": row.playlist_id,
                            "playlist_title": row.title or row.playlist_id,
                            "local_playlist_id": local_playlist_id,
                            "queued_tracks": 0,
                            "skipped_tracks": 0,
                            "queue_errors": 1,
                            "synced_at": now,
                            "error": str(queue_error),
                        }

                tracked_extra = row.extra if isinstance(row.extra, dict) else {}
                if local_playlist_id:
                    tracked_extra = {
                        **tracked_extra,
                        "local_playlist_id": local_playlist_id,
                    }

                update_payload = {
                    "status": "active" if summary.get("success") else "failed",
                    "last_sync_at": now,
                    "next_sync_at": now
                    + max(120, int(row.sync_interval_seconds or 900)),
                    "last_result": summary,
                    "last_error": summary.get("error"),
                    "extra": tracked_extra,
                }
                if new_snapshot_ids is not None:
                    update_payload["snapshot_track_ids"] = new_snapshot_ids
                    update_payload["snapshot_hash"] = _snapshot_hash(new_snapshot_ids)

                TrackedPlaylistTable.update_row(
                    row.id,
                    update_payload,
                )
                return summary

            client = get_spotify_metadata_client()
            playlist = client.get_playlist(row.playlist_id)
            if not playlist:
                raise RuntimeError("Failed to load playlist metadata from Spotify")

            max_tracks = int(os.getenv("SWINGMUSIC_PLAYLIST_SYNC_MAX_TRACKS", "800"))
            tracks = client.get_playlist_tracks(
                row.playlist_id, limit=max(1, min(max_tracks, 2000))
            )

            track_records: list[dict[str, Any]] = []
            for track in tracks:
                if not track.id:
                    continue

                album_name = ""
                if isinstance(track.album, dict):
                    album_name = track.album.get("name", "")

                artists = [
                    artist.get("name", "")
                    for artist in (track.artists or [])
                    if artist.get("name")
                ]
                artist_name = ", ".join([name for name in artists if name]).strip()

                trackhash = _trackhash_from_spotify_track(track)
                track_records.append(
                    {
                        "spotify_id": track.id,
                        "trackhash": trackhash,
                        "title": track.name,
                        "artist": artist_name,
                        "album": album_name,
                        "source_url": f"https://open.spotify.com/track/{track.id}",
                    }
                )

            new_track_ids = [record["spotify_id"] for record in track_records]
            old_track_ids = [
                track_id for track_id in (row.snapshot_track_ids or []) if track_id
            ]

            old_set = set(old_track_ids)
            new_set = set(new_track_ids)

            added_track_ids = [
                track_id for track_id in new_track_ids if track_id not in old_set
            ]
            removed_track_ids = [
                track_id for track_id in old_track_ids if track_id not in new_set
            ]

            old_positions = {
                track_id: index for index, track_id in enumerate(old_track_ids)
            }
            reordered_tracks = 0
            for index, track_id in enumerate(new_track_ids):
                previous = old_positions.get(track_id)
                if previous is not None and previous != index:
                    reordered_tracks += 1

            trackhashes = [
                record["trackhash"]
                for record in track_records
                if record.get("trackhash")
            ]
            availability = get_track_availability_map(trackhashes, userid=userid)

            added_set = set(added_track_ids)
            removed_set = set(removed_track_ids)
            queued_tracks = 0
            skipped_tracks = 0
            queue_errors = 0
            cancelled_removed_jobs = 0
            seen_trackhashes: set[str] = set()

            mirror_trackhashes = [
                record["trackhash"]
                for record in track_records
                if record.get("trackhash")
            ]
            local_playlist_id = _sync_mirror_playlist(
                tracked_row=row,
                playlist_title=playlist.name,
                owner_name=(playlist.owner or {}).get("display_name")
                if playlist.owner
                else row.owner_name,
                ordered_trackhashes=mirror_trackhashes,
                snapshot_track_ids=new_track_ids,
            )

            if removed_set:
                active_jobs = DownloadJobTable.list_for_user(
                    userid, states={"queued", "downloading"}
                )
                for job in active_jobs:
                    payload = job.payload or {}
                    if payload.get("tracked_playlist_id") != row.id:
                        continue
                    if payload.get("spotify_id") not in removed_set:
                        continue
                    if download_job_manager.cancel(job.id, userid):
                        cancelled_removed_jobs += 1

            for record in track_records:
                trackhash = record.get("trackhash")
                spotify_id = record.get("spotify_id")
                if not spotify_id or not trackhash:
                    skipped_tracks += 1
                    continue

                if trackhash in seen_trackhashes:
                    skipped_tracks += 1
                    continue

                seen_trackhashes.add(trackhash)
                status = (availability.get(trackhash) or {}).get("state", "missing")
                should_queue = False

                if spotify_id in added_set:
                    should_queue = status != "available"
                elif force:
                    should_queue = status in {"missing", "failed"}

                if status == "queued":
                    should_queue = False

                if not should_queue:
                    skipped_tracks += 1
                    continue

                try:
                    download_job_manager.enqueue(
                        userid=userid,
                        source_url=record["source_url"],
                        source="spotify",
                        quality=row.quality,
                        codec=row.codec,
                        trackhash=trackhash,
                        title=record.get("title"),
                        artist=record.get("artist"),
                        album=record.get("album"),
                        item_type="track",
                        payload={
                            "tracked_playlist_id": row.id,
                            "playlist_id": row.playlist_id,
                            "playlist_title": row.title or playlist.name,
                            "spotify_id": spotify_id,
                            "sync_reason": "new_track"
                            if spotify_id in added_set
                            else "missing_repair",
                        },
                    )
                    queued_tracks += 1
                except Exception:
                    queue_errors += 1

            summary = {
                "success": True,
                "tracked_id": row.id,
                "playlist_id": row.playlist_id,
                "playlist_title": playlist.name,
                "local_playlist_id": local_playlist_id,
                "total_tracks": len(new_track_ids),
                "added_tracks": len(added_track_ids),
                "removed_tracks": len(removed_track_ids),
                "reordered_tracks": reordered_tracks,
                "queued_tracks": queued_tracks,
                "skipped_tracks": skipped_tracks,
                "queue_errors": queue_errors,
                "cancelled_removed_jobs": cancelled_removed_jobs,
                "synced_at": now,
            }

            tracked_extra = row.extra if isinstance(row.extra, dict) else {}
            if local_playlist_id:
                tracked_extra = {
                    **tracked_extra,
                    "local_playlist_id": local_playlist_id,
                }

            TrackedPlaylistTable.update_row(
                row.id,
                {
                    "title": playlist.name,
                    "owner_name": (playlist.owner or {}).get("display_name")
                    if playlist.owner
                    else row.owner_name,
                    "status": "active",
                    "last_sync_at": now,
                    "next_sync_at": now
                    + max(120, int(row.sync_interval_seconds or 900)),
                    "snapshot_track_ids": new_track_ids,
                    "snapshot_hash": _snapshot_hash(new_track_ids),
                    "last_result": {
                        **summary,
                        "removed_track_ids": removed_track_ids[:300],
                        "added_track_ids": added_track_ids[:300],
                    },
                    "last_error": None,
                    "extra": tracked_extra,
                },
            )

            return summary
        except Exception as error:
            log.exception("Playlist sync failed for tracked_id=%s", tracked_id)
            now = int(time.time())
            TrackedPlaylistTable.update_row(
                tracked_id,
                {
                    "status": "failed",
                    "last_error": str(error),
                    "next_sync_at": now + 300,
                    "last_result": {
                        "success": False,
                        "tracked_id": tracked_id,
                        "error": str(error),
                        "synced_at": now,
                    },
                },
            )
            return {
                "success": False,
                "tracked_id": tracked_id,
                "error": str(error),
            }
        finally:
            with self._lock:
                self._processing.discard(tracked_id)

    def set_auto_sync(
        self, tracked_id: int, *, userid: int, enabled: bool
    ) -> dict[str, Any] | None:
        row = TrackedPlaylistTable.get_by_id(tracked_id, userid=userid)
        if not row:
            return None

        now = int(time.time())
        updated = TrackedPlaylistTable.update_row(
            tracked_id,
            {
                "auto_sync": bool(enabled),
                "status": "active" if enabled else "paused",
                "next_sync_at": now + max(120, int(row.sync_interval_seconds or 900)),
            },
        )
        return _serialize_tracked_playlist(updated)

    def untrack_playlist(self, tracked_id: int, *, userid: int) -> bool:
        row = TrackedPlaylistTable.get_by_id(tracked_id, userid=userid)
        if not row:
            return False

        TrackedPlaylistTable.update_row(
            tracked_id,
            {
                "status": "deleted",
                "auto_sync": False,
                "next_sync_at": int(time.time()) + (10 * 365 * 24 * 3600),
            },
        )
        return True

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                due = TrackedPlaylistTable.due_for_sync(
                    now_ts=int(time.time()), limit=20
                )
                for row in due:
                    if self._stop.is_set():
                        break
                    self.sync_tracked_playlist(row.id, userid=row.userid, force=False)
            except Exception:
                log.exception("Playlist tracking worker iteration failed")

            self._stop.wait(self.poll_interval_seconds)


playlist_tracking_service = PlaylistTrackingService()

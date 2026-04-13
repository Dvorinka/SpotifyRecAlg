from __future__ import annotations

import os
from pathlib import Path

from flask_jwt_extended import get_jwt_identity
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.config import UserConfig
from swingmusic.db.production import UserRootDirOwnershipTable
from swingmusic.services.download_jobs import download_job_manager
from swingmusic.services.library_projection import (
    get_track_availability,
    get_track_availability_map,
    import_existing_track,
    list_import_candidates,
)
from swingmusic.services.playlist_tracking import playlist_tracking_service
from swingmusic.services.user_library_scope import get_user_root_dirs
from swingmusic.utils.auth import get_current_userid

bp_tag = Tag(name="Downloads", description="Unified download jobs and import flow")
api = APIBlueprint(
    "downloads", __name__, url_prefix="/api/downloads", abp_tags=[bp_tag]
)


class JobsQuery(BaseModel):
    limit: int = Field(default=200, description="Maximum number of jobs to return")


class HistoryQuery(BaseModel):
    limit: int = Field(default=100, description="Maximum history items")
    offset: int = Field(default=0, description="History offset")


class CreateDownloadJobBody(BaseModel):
    source_url: str | None = Field(default=None, description="Original source URL")
    source: str = Field(default="spotify", description="Source provider")
    quality: str = Field(default="high", description="Requested quality")
    codec: str | None = Field(default=None, description="Codec hint")
    trackhash: str | None = Field(default=None, description="Track hash")
    title: str | None = Field(default=None, description="Track title")
    artist: str | None = Field(default=None, description="Track artist")
    album: str | None = Field(default=None, description="Track album")
    item_type: str = Field(default="track", description="Item type")
    target_path: str | None = Field(
        default=None, description="Optional destination path"
    )
    payload: dict = Field(default_factory=dict, description="Extra provider payload")


class JobPath(BaseModel):
    job_id: int


class ImportCandidatesBody(BaseModel):
    trackhash: str = Field(description="Trackhash to query import candidates for")


class ImportConfirmBody(BaseModel):
    trackhash: str = Field(description="Trackhash to import")
    source_userid: int | None = Field(
        default=None, description="Specific source user ID"
    )


class AvailabilityBody(BaseModel):
    trackhashes: list[str] = Field(default_factory=list)


class TrackPlaylistBody(BaseModel):
    source_url: str = Field(
        description="Trackable playlist URL (Spotify and supported providers)"
    )
    quality: str | None = Field(default="lossless", description="Requested quality")
    codec: str | None = Field(default="flac", description="Requested codec")
    auto_sync: bool = Field(default=True, description="Enable periodic sync")
    sync_interval_seconds: int = Field(
        default=900, description="Sync cadence in seconds"
    )
    sync_now: bool = Field(default=True, description="Run immediate sync")


class TrackedPlaylistPath(BaseModel):
    tracked_id: int


class TrackedPlaylistsQuery(BaseModel):
    playlist_id: str | None = Field(
        default=None, description="Filter by Spotify playlist ID"
    )


class ToggleAutoSyncBody(BaseModel):
    enabled: bool = Field(default=True, description="Whether auto sync is enabled")


class StorageRootsBody(BaseModel):
    root_dirs: list[str] = Field(
        default_factory=list, description="Root directories for current user"
    )


def _current_userid() -> int:
    try:
        identity = get_jwt_identity()
        if isinstance(identity, dict) and identity.get("id") is not None:
            return int(identity["id"])
    except Exception:
        pass

    return get_current_userid()


def _normalize_root_path(value: str) -> str:
    if value == "$home":
        return "$home"

    return Path(value).expanduser().resolve().as_posix().rstrip("/")


def _allowed_root_bases() -> list[Path]:
    bases: list[Path] = []
    for root in UserConfig().rootDirs or []:
        if root == "$home":
            bases.append(Path.home().resolve())
        else:
            bases.append(Path(root).expanduser().resolve())
    return bases


def _validate_user_roots(root_dirs: list[str]) -> list[str]:
    normalized = [
        _normalize_root_path(path.strip())
        for path in root_dirs
        if path and path.strip()
    ]
    normalized = list(dict.fromkeys(normalized))

    configured_bases = _allowed_root_bases()
    configured_raw = UserConfig().rootDirs or []
    if not configured_bases:
        return normalized

    for root in normalized:
        if root == "$home":
            if "$home" not in configured_raw:
                raise ValueError(
                    "$home is not allowed because it is not configured as a server root"
                )
            continue

        candidate = Path(root).expanduser().resolve()
        valid = False
        for base in configured_bases:
            if candidate == base or base in candidate.parents:
                valid = True
                break
        if not valid:
            raise ValueError(
                "User root directories must be inside configured library roots"
            )

    return normalized


@api.get("/jobs")
def list_download_jobs(query: JobsQuery):
    userid = _current_userid()
    limit = max(1, min(int(query.limit or 200), 500))
    jobs = download_job_manager.list_jobs(userid, limit=limit)
    return {
        "jobs": jobs,
        "total": len(jobs),
    }


@api.get("/queue")
def get_download_queue(query: JobsQuery):
    userid = _current_userid()
    limit = max(1, min(int(query.limit or 200), 500))
    jobs = download_job_manager.list_jobs(userid, limit=limit)

    pending = [job for job in jobs if job["state"] == "queued"]
    active = [job for job in jobs if job["state"] == "downloading"]
    queued = [job for job in jobs if job["state"] in {"queued", "downloading"}]
    history = [
        job for job in jobs if job["state"] in {"completed", "failed", "cancelled"}
    ]

    return {
        "queue_length": len(pending),
        "active_downloads": len(active),
        "queue": queued,
        "pending": pending,
        "active": active,
        "history": history,
    }


@api.get("/status")
def get_download_status(query: JobsQuery):
    userid = _current_userid()
    limit = max(1, min(int(query.limit or 500), 2000))
    jobs = download_job_manager.list_jobs(userid, limit=limit)

    counts = {
        "queued": 0,
        "downloading": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for job in jobs:
        state = job.get("state")
        if state in counts:
            counts[state] += 1

    return {
        "counts": counts,
        "total": len(jobs),
    }


@api.get("/history")
def get_download_history(query: HistoryQuery):
    userid = _current_userid()
    limit = max(1, min(int(query.limit or 100), 500))
    offset = max(0, int(query.offset or 0))

    jobs = download_job_manager.list_jobs(userid, limit=2000)
    history = [
        job for job in jobs if job["state"] in {"completed", "failed", "cancelled"}
    ]
    sliced = history[offset : offset + limit]

    return {
        "history": sliced,
        "total": len(history),
        "limit": limit,
        "offset": offset,
    }


@api.post("/history/clear")
def clear_download_history():
    userid = _current_userid()
    deleted = download_job_manager.clear_history(userid)
    return {
        "success": True,
        "deleted": deleted,
    }


@api.post("/jobs")
def create_download_job(body: CreateDownloadJobBody):
    userid = _current_userid()

    job_id = download_job_manager.enqueue(
        userid=userid,
        source_url=body.source_url,
        source=body.source,
        quality=body.quality,
        codec=body.codec,
        trackhash=body.trackhash,
        title=body.title,
        artist=body.artist,
        album=body.album,
        item_type=body.item_type,
        target_path=body.target_path,
        payload=body.payload,
    )

    job = download_job_manager.get_job(job_id, userid=userid)
    return {
        "success": True,
        "job_id": job_id,
        "job": job,
    }, 201


@api.get("/jobs/<job_id>")
def get_download_job(path: JobPath):
    userid = _current_userid()
    job = download_job_manager.get_job(path.job_id, userid=userid)

    if not job:
        return {"error": "Job not found"}, 404

    return job


@api.post("/jobs/<job_id>/cancel")
def cancel_download_job(path: JobPath):
    userid = _current_userid()
    success = download_job_manager.cancel(path.job_id, userid)

    if not success:
        return {"success": False, "error": "Unable to cancel job"}, 400

    return {"success": True}


@api.post("/jobs/<job_id>/retry")
def retry_download_job(path: JobPath):
    userid = _current_userid()
    success = download_job_manager.retry(path.job_id, userid)

    if not success:
        return {"success": False, "error": "Unable to retry job"}, 400

    return {"success": True}


@api.post("/imports/candidates")
def get_import_candidates(body: ImportCandidatesBody):
    userid = _current_userid()
    candidates = list_import_candidates(body.trackhash, userid=userid)
    availability = get_track_availability(body.trackhash, userid=userid)

    return {
        "trackhash": body.trackhash,
        "availability": availability,
        "candidates": candidates,
    }


@api.post("/imports/confirm")
def confirm_import(body: ImportConfirmBody):
    userid = _current_userid()
    imported = import_existing_track(
        body.trackhash,
        userid=userid,
        source_userid=body.source_userid,
    )

    availability = get_track_availability(body.trackhash, userid=userid)

    if not imported:
        return {
            "success": False,
            "error": "No import candidate available",
            "availability": availability,
        }, 404

    return {
        "success": True,
        "availability": availability,
    }


@api.post("/tracks/availability")
def get_tracks_availability(body: AvailabilityBody):
    userid = _current_userid()
    availability = get_track_availability_map(body.trackhashes, userid=userid)
    return {
        "availability": availability,
    }


@api.post("/playlists/track")
def track_playlist(body: TrackPlaylistBody):
    userid = _current_userid()

    try:
        payload = playlist_tracking_service.track_playlist(
            userid=userid,
            source_url=body.source_url,
            quality=body.quality,
            codec=body.codec,
            auto_sync=body.auto_sync,
            sync_interval_seconds=body.sync_interval_seconds,
            sync_now=body.sync_now,
        )
    except ValueError as error:
        return {"success": False, "error": str(error)}, 400
    except Exception as error:
        return {"success": False, "error": f"Failed to track playlist: {error}"}, 500

    return {
        "success": True,
        **payload,
    }, 201


@api.get("/playlists/tracked")
def list_tracked_playlists(query: TrackedPlaylistsQuery):
    userid = _current_userid()
    items = playlist_tracking_service.list_tracked_playlists(userid)

    if query.playlist_id:
        filtered = [
            item for item in items if item.get("playlist_id") == query.playlist_id
        ]
    else:
        filtered = items

    return {
        "tracked_playlists": filtered,
        "total": len(filtered),
    }


@api.post("/playlists/<tracked_id>/sync")
def sync_tracked_playlist(path: TrackedPlaylistPath):
    userid = _current_userid()
    result = playlist_tracking_service.sync_tracked_playlist(
        path.tracked_id, userid=userid, force=True
    )

    if not result.get("success"):
        if result.get("message") == "Tracked playlist not found":
            return {"success": False, **result}, 404
        return {"success": False, **result}, 400

    tracked = playlist_tracking_service.get_tracked_playlist(path.tracked_id, userid)
    return {
        "success": True,
        "result": result,
        "tracked": tracked,
    }


@api.post("/playlists/<tracked_id>/auto-sync")
def toggle_playlist_auto_sync(path: TrackedPlaylistPath, body: ToggleAutoSyncBody):
    userid = _current_userid()
    tracked = playlist_tracking_service.set_auto_sync(
        path.tracked_id, userid=userid, enabled=body.enabled
    )
    if not tracked:
        return {"success": False, "error": "Tracked playlist not found"}, 404

    return {
        "success": True,
        "tracked": tracked,
    }


@api.delete("/playlists/<tracked_id>")
def delete_tracked_playlist(path: TrackedPlaylistPath):
    userid = _current_userid()
    deleted = playlist_tracking_service.untrack_playlist(path.tracked_id, userid=userid)
    if not deleted:
        return {"success": False, "error": "Tracked playlist not found"}, 404

    return {
        "success": True,
    }


@api.get("/storage/roots")
def get_storage_roots():
    userid = _current_userid()
    configured_roots = UserConfig().rootDirs or []
    owned_roots = UserRootDirOwnershipTable.get_paths(userid)
    effective = get_user_root_dirs(userid)

    return {
        "configured_roots": configured_roots,
        "owned_roots": owned_roots,
        "effective_roots": effective,
    }


@api.post("/storage/roots")
def set_storage_roots(body: StorageRootsBody):
    userid = _current_userid()
    try:
        normalized = _validate_user_roots(body.root_dirs)
    except ValueError as error:
        return {"success": False, "error": str(error)}, 400

    for root in normalized:
        if root == "$home":
            continue
        os.makedirs(root, exist_ok=True)

    UserRootDirOwnershipTable.replace_paths(userid, normalized)

    return {
        "success": True,
        "owned_roots": UserRootDirOwnershipTable.get_paths(userid),
        "effective_roots": get_user_root_dirs(userid),
    }

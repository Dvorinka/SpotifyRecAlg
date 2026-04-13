from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

from sqlalchemy import select

from swingmusic.config import UserConfig

# DragonflyDB integration for fast job queue operations
from swingmusic.db.dragonfly_extended_client import get_job_queue_service
from swingmusic.db.engine import DbEngine
from swingmusic.db.libdata import TrackTable
from swingmusic.db.production import (
    DownloadJobTable,
    LibraryFileTable,
    LyricsStatusTable,
    UserRootDirOwnershipTable,
)
from swingmusic.db.userdata import UserTable
from swingmusic.lib.index import run_index_pipeline
from swingmusic.services.download_provider_adapters import fallback_download_adapter
from swingmusic.services.library_projection import (
    mark_track_available,
    mark_track_failed,
    mark_track_queued,
)
from swingmusic.services.lyrics_backfill import backfill_lyrics_async
from swingmusic.services.spotiflac_worker import spotiflac_worker
from swingmusic.utils.hashing import create_hash

log = logging.getLogger(__name__)


def _sanitize_filename(value: str) -> str:
    filename = re.sub(r"[^\w\s\-.]", "", value, flags=re.UNICODE)
    filename = re.sub(r"\s+", " ", filename).strip()
    return filename[:120] or f"download-{int(time.time())}"


def _quality_to_codec_and_bitrate(
    quality: str, codec_hint: str | None = None
) -> tuple[str, int]:
    quality = (quality or "high").lower()

    if quality == "lossless":
        return (codec_hint or "flac", 1411)
    if quality == "high":
        return (codec_hint or "mp3", 320)
    if quality == "medium":
        return (codec_hint or "mp3", 192)

    return (codec_hint or "mp3", 128)


def _resolve_primary_root_dir() -> str:
    config = UserConfig()
    if config.rootDirs:
        root = config.rootDirs[0]
        if root == "$home":
            return os.path.join(os.path.expanduser("~"), "Music")
        return root
    return os.path.join(os.path.expanduser("~"), "Music")


def _resolve_download_root_for_user(userid: int | None = None) -> str:
    if userid is None:
        return _resolve_primary_root_dir()

    owned_roots = UserRootDirOwnershipTable.get_paths(userid)
    if owned_roots:
        root = owned_roots[0]
        if root == "$home":
            return os.path.join(os.path.expanduser("~"), "Music")
        return root

    shared_root = _resolve_primary_root_dir()
    user = UserTable.get_by_id(userid)
    username = (
        _sanitize_filename(user.username)
        if user and user.username
        else f"user-{userid}"
    )

    # Isolate user downloads by default while keeping paths under configured roots.
    user_root = os.path.join(shared_root, "SwingMusic Users", username)
    os.makedirs(user_root, exist_ok=True)
    UserRootDirOwnershipTable.assign_paths(userid, [user_root])
    return user_root


def _resolve_download_dir(
    target_path: str | None = None, userid: int | None = None
) -> str:
    if target_path:
        directory = os.path.dirname(target_path) or target_path
        os.makedirs(directory, exist_ok=True)
        return directory

    root = _resolve_download_root_for_user(userid)
    download_dir = os.path.join(root, "SwingMusic Downloads")
    os.makedirs(download_dir, exist_ok=True)
    return download_dir


def _compute_trackhash(
    title: str | None,
    artist: str | None,
    album: str | None,
    fallback: str | None = None,
) -> str | None:
    if title and artist:
        return create_hash(title, album or "", artist)

    return fallback


def _refresh_user_projection_for_download_path(
    *,
    userid: int,
    path: str,
    source: str,
) -> int:
    """
    Re-indexes library metadata and marks tracks in the downloaded path as
    available for the requesting user.

    Returns number of projected tracks.
    """
    if not path:
        return 0

    scope_path = path
    if not os.path.isdir(scope_path):
        scope_path = os.path.dirname(scope_path) or scope_path

    if not scope_path or not os.path.exists(scope_path):
        return 0

    run_index_pipeline()

    projected = 0
    for track in TrackTable.get_tracks_in_path(scope_path):
        if not track.filepath or not os.path.exists(track.filepath):
            continue

        mark_track_available(
            track.trackhash,
            filepath=track.filepath,
            bitrate=int(track.bitrate or 0),
            userid=userid,
            source=source,
        )
        projected += 1

    return projected


class DownloadJobManager:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_loop, name="download-job-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def enqueue(
        self,
        *,
        userid: int,
        source_url: str | None,
        source: str,
        quality: str,
        codec: str | None = None,
        trackhash: str | None = None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        item_type: str = "track",
        target_path: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        resolved_trackhash = _compute_trackhash(title, artist, album, trackhash)

        job_id = DownloadJobTable.enqueue(
            {
                "userid": userid,
                "source_url": source_url,
                "source": source,
                "provider": source,
                "quality": quality,
                "codec": codec or "mp3",
                "trackhash": resolved_trackhash,
                "title": title,
                "artist": artist,
                "album": album,
                "item_type": item_type,
                "target_path": target_path,
                "payload": payload or {},
            }
        )

        if resolved_trackhash:
            mark_track_queued(
                resolved_trackhash,
                job_id=job_id,
                source_url=source_url,
                userid=userid,
            )

        # Also enqueue to DragonflyDB for fast queue access and monitoring
        job_queue = get_job_queue_service()
        if job_queue.cache.client.is_available():
            try:
                job_queue.enqueue_job(
                    "downloads",
                    {
                        "job_id": job_id,
                        "userid": userid,
                        "source": source,
                        "trackhash": resolved_trackhash,
                        "title": title,
                        "artist": artist,
                        "item_type": item_type,
                        "queued_at": int(time.time()),
                    },
                )
                log.debug(f"Enqueued job {job_id} to DragonflyDB queue")
            except Exception as e:
                log.debug(f"Failed to enqueue to DragonflyDB: {e}")

        return job_id

    def list_jobs(self, userid: int, limit: int = 200) -> list[dict[str, Any]]:
        with DbEngine.manager() as conn:
            result = conn.execute(
                select(DownloadJobTable)
                .where(DownloadJobTable.userid == userid)
                .order_by(DownloadJobTable.created_at.desc())
                .limit(limit)
            )
            jobs = list(result.scalars())

        return [self.serialize_job(job) for job in jobs]

    def get_job(self, job_id: int, userid: int | None = None) -> dict[str, Any] | None:
        job = DownloadJobTable.get_by_id(job_id)
        if not job:
            return None

        if userid is not None and job.userid != userid:
            return None

        return self.serialize_job(job)

    def cancel(self, job_id: int, userid: int) -> bool:
        job = DownloadJobTable.get_by_id(job_id)
        if not job or job.userid != userid:
            return False

        if job.state in {"completed", "failed", "cancelled"}:
            return False

        DownloadJobTable.update_job(
            job_id,
            {
                "state": "cancelled",
                "error": "Cancelled by user",
                "finished_at": int(time.time()),
            },
        )

        if job.trackhash:
            mark_track_failed(
                job.trackhash, error="Cancelled by user", job_id=job_id, userid=userid
            )

        return True

    def retry(self, job_id: int, userid: int) -> bool:
        job = DownloadJobTable.get_by_id(job_id)
        if not job or job.userid != userid:
            return False

        if job.state not in {"failed", "cancelled"}:
            return False

        DownloadJobTable.update_job(
            job_id,
            {
                "state": "queued",
                "progress": 0.0,
                "error": None,
                "started_at": None,
                "finished_at": None,
                "retry_count": int(job.retry_count or 0) + 1,
            },
        )

        if job.trackhash:
            mark_track_queued(
                job.trackhash, job_id=job_id, source_url=job.source_url, userid=userid
            )

        return True

    def clear_queue(self, userid: int) -> int:
        jobs = DownloadJobTable.list_for_user(userid, states={"queued", "downloading"})
        cancelled = 0
        for job in jobs:
            if self.cancel(job.id, userid):
                cancelled += 1
        return cancelled

    def clear_history(self, userid: int) -> int:
        return DownloadJobTable.delete_for_user(
            userid,
            states={"completed", "failed", "cancelled"},
        )

    @staticmethod
    def serialize_job(job: Any) -> dict[str, Any]:
        return {
            "id": job.id,
            "state": job.state,
            "status": job.state,
            "source": job.source,
            "service": job.source,
            "provider": job.provider,
            "source_url": job.source_url,
            "quality": job.quality,
            "codec": job.codec,
            "target_path": job.target_path,
            "error": job.error,
            "progress": round(float(job.progress or 0.0), 2),
            "trackhash": job.trackhash,
            "title": job.title,
            "artist": job.artist,
            "album": job.album,
            "item_type": job.item_type,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "retry_count": int(job.retry_count or 0),
        }

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job = DownloadJobTable.get_queued_job()
            if not job:
                time.sleep(0.6)
                continue

            self._process_job(job)

    def _process_job(self, job: Any) -> None:
        now = int(time.time())
        DownloadJobTable.update_job(
            job.id,
            {
                "state": "downloading",
                "started_at": now,
                "progress": 1.0,
                "error": None,
            },
        )

        trackhash = _compute_trackhash(job.title, job.artist, job.album, job.trackhash)

        try:
            # Job might have been cancelled by user while running.
            current = DownloadJobTable.get_by_id(job.id)
            if not current or current.state == "cancelled":
                return

            # Dedupe/import-aware reuse: if file already exists in the media registry,
            # re-link it to this user instead of downloading again.
            if trackhash:
                existing_file = LibraryFileTable.get_by_trackhash(trackhash)
                if (
                    existing_file
                    and existing_file.filepath
                    and os.path.exists(existing_file.filepath)
                ):
                    mark_track_available(
                        trackhash,
                        filepath=existing_file.filepath,
                        bitrate=int(existing_file.bitrate or 0),
                        userid=job.userid,
                        source="registry_reuse",
                    )
                    DownloadJobTable.update_job(
                        job.id,
                        {
                            "state": "completed",
                            "progress": 100.0,
                            "target_path": existing_file.filepath,
                            "trackhash": trackhash,
                            "codec": existing_file.codec or job.codec,
                            "finished_at": int(time.time()),
                        },
                    )
                    return

            DownloadJobTable.update_job(job.id, {"progress": 11.0})

            codec, bitrate = _quality_to_codec_and_bitrate(job.quality, job.codec)
            extension = codec.lower() if codec else "mp3"
            safe_title = _sanitize_filename(
                job.title or job.trackhash or f"job-{job.id}"
            )
            directory = _resolve_download_dir(job.target_path, userid=job.userid)
            target_path = job.target_path or os.path.join(
                directory, f"{safe_title}.{extension}"
            )

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            DownloadJobTable.update_job(job.id, {"progress": 23.0})

            provider_errors: list[str] = []
            result = None

            try:
                result = spotiflac_worker.download(
                    source_url=job.source_url or "",
                    output_dir=directory,
                    codec=codec,
                    quality=job.quality,
                    item_type=job.item_type,
                    target_path=target_path if job.item_type == "track" else None,
                )
            except Exception as primary_error:
                provider_errors.append(f"spotiflac: {primary_error}")

            if result is None and fallback_download_adapter.is_available():
                try:
                    result = fallback_download_adapter.download(
                        source_url=job.source_url or "",
                        output_dir=directory,
                        codec=codec,
                        quality=job.quality,
                        item_type=job.item_type,
                        target_path=target_path if job.item_type == "track" else None,
                    )
                except Exception as fallback_error:
                    provider_errors.append(
                        f"{fallback_download_adapter.name}: {fallback_error}"
                    )

            if result is None:
                error_message = (
                    "; ".join(provider_errors) or "No download provider succeeded"
                )
                raise RuntimeError(error_message)

            DownloadJobTable.update_job(job.id, {"progress": 92.0})

            final_path = result.file_path
            final_codec = result.codec or codec
            final_bitrate = int(result.bitrate or bitrate)

            if trackhash and final_path and os.path.exists(final_path):
                mark_track_available(
                    trackhash,
                    filepath=final_path,
                    bitrate=final_bitrate,
                    userid=job.userid,
                    source=result.provider or job.source,
                )

            # Non-track jobs (album/artist/playlist) must project downloaded files
            # to the requesting user's library before final completion.
            if job.item_type != "track":
                try:
                    DownloadJobTable.update_job(job.id, {"progress": 96.0})
                    projected = _refresh_user_projection_for_download_path(
                        userid=job.userid,
                        path=final_path or directory,
                        source=result.provider or job.source,
                    )
                    DownloadJobTable.update_job(
                        job.id,
                        {
                            "progress": 99.0,
                            "payload": {
                                **(job.payload or {}),
                                "projected_tracks": projected,
                            },
                        },
                    )
                except Exception as projection_error:
                    # Keep the download successful, but expose projection warning
                    # so UI can surface retries/rescan actions.
                    log.exception("Failed to refresh projection for job %s", job.id)
                    DownloadJobTable.update_job(
                        job.id,
                        {
                            "payload": {
                                **(job.payload or {}),
                                "projection_error": str(projection_error),
                            },
                        },
                    )

            DownloadJobTable.update_job(
                job.id,
                {
                    "state": "completed",
                    "progress": 100.0,
                    "target_path": final_path,
                    "trackhash": trackhash,
                    "codec": final_codec,
                    "finished_at": int(time.time()),
                },
            )

            if trackhash and final_path and os.path.exists(final_path):
                LyricsStatusTable.upsert(
                    trackhash=trackhash,
                    filepath=final_path,
                    status="pending",
                    source="download",
                    has_embedded=False,
                    has_lrc=os.path.exists(os.path.splitext(final_path)[0] + ".lrc"),
                    last_error=None,
                    extra={"job_id": job.id, "provider": result.provider},
                )
                backfill_lyrics_async(
                    filepath=final_path,
                    title=job.title,
                    artist=job.artist,
                    album=job.album,
                    trackhash=trackhash,
                )
        except Exception as error:
            message = str(error)
            DownloadJobTable.update_job(
                job.id,
                {
                    "state": "failed",
                    "error": message,
                    "finished_at": int(time.time()),
                },
            )

            if trackhash:
                mark_track_failed(
                    trackhash, error=message, job_id=job.id, userid=job.userid
                )
                LyricsStatusTable.upsert(
                    trackhash=trackhash,
                    filepath=job.target_path,
                    status="failed",
                    source="download",
                    last_error=message,
                    extra={"job_id": job.id},
                    increment_attempt=True,
                )


# Global process-wide manager used by API wrappers.
download_job_manager = DownloadJobManager()

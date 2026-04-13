"""
Spotify downloader compatibility service.

This module preserves the historic ``spotify_downloader`` entrypoint while
routing all download operations through the durable ``DownloadJobManager``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from swingmusic.services.download_jobs import download_job_manager
from swingmusic.services.spotify_metadata_client import get_spotify_metadata_client
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.hashing import create_hash

logger = logging.getLogger(__name__)


class DownloadSource(Enum):
    SPOTIFY = "spotify"
    TIDAL = "tidal"
    QOBUZ = "qobuz"
    YOUTUBE = "youtube"
    GENERIC = "generic"


@dataclass
class DownloadItemMetadata:
    spotify_id: str
    item_type: str
    title: str
    artist: str
    album: str
    duration_ms: int | None
    image_url: str | None
    release_date: str | None
    track_number: int | None = None
    total_tracks: int | None = None
    is_explicit: bool = False
    preview_url: str | None = None


_SPOTIFY_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:open\.)?spotify\.com/(track|album|playlist|artist)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def _parse_spotify_url(url: str) -> tuple[str, str] | None:
    match = _SPOTIFY_URL_PATTERN.search(url or "")
    if not match:
        return None

    return match.group(1).lower(), match.group(2)


def _quality_to_job_quality(quality: str | None) -> tuple[str, str]:
    quality = (quality or "flac").lower()

    mapping = {
        "flac": ("lossless", "flac"),
        "lossless": ("lossless", "flac"),
        "mp3_320": ("high", "mp3"),
        "high": ("high", "mp3"),
        "mp3_192": ("medium", "mp3"),
        "medium": ("medium", "mp3"),
        "mp3_128": ("low", "mp3"),
        "low": ("low", "mp3"),
    }

    return mapping.get(quality, ("high", "mp3"))


def _metadata_to_trackhash(metadata: DownloadItemMetadata) -> str | None:
    if metadata.item_type != "track":
        return None

    title = (metadata.title or "").strip()
    artist = (metadata.artist or "").strip()

    if not title or not artist:
        return None

    return create_hash(title, metadata.album or "", artist)


class SpotifyDownloaderService:
    """Compatibility wrapper that exposes the old downloader API."""

    def __init__(self) -> None:
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        download_job_manager.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return

        download_job_manager.stop()
        self._started = False

    async def get_metadata(self, url: str) -> DownloadItemMetadata | None:
        parsed = _parse_spotify_url(url)
        if not parsed:
            return None

        item_type, item_id = parsed
        client = get_spotify_metadata_client()

        if item_type == "track":
            track = client.get_track(item_id)
            if not track:
                return None

            album_name = (
                track.album.get("name", "") if isinstance(track.album, dict) else ""
            )
            album_images = (
                track.album.get("images", []) if isinstance(track.album, dict) else []
            )
            image_url = album_images[0].get("url") if album_images else None

            return DownloadItemMetadata(
                spotify_id=track.id,
                item_type="track",
                title=track.name,
                artist=", ".join(
                    a.get("name", "") for a in track.artists if a.get("name")
                ),
                album=album_name,
                duration_ms=track.duration_ms,
                image_url=image_url,
                release_date=track.album.get("release_date")
                if isinstance(track.album, dict)
                else None,
                track_number=track.track_number,
                total_tracks=track.album.get("total_tracks")
                if isinstance(track.album, dict)
                else None,
                is_explicit=bool(track.explicit),
                preview_url=track.preview_url,
            )

        if item_type == "album":
            album = client.get_album(item_id)
            if not album:
                return None

            return DownloadItemMetadata(
                spotify_id=album.id,
                item_type="album",
                title=album.name,
                artist=", ".join(
                    a.get("name", "") for a in album.artists if a.get("name")
                ),
                album=album.name,
                duration_ms=None,
                image_url=album.images[0].get("url") if album.images else None,
                release_date=album.release_date,
                track_number=None,
                total_tracks=album.total_tracks,
                is_explicit=False,
                preview_url=None,
            )

        if item_type == "artist":
            artist = client.get_artist(item_id)
            if not artist:
                return None

            return DownloadItemMetadata(
                spotify_id=artist.id,
                item_type="artist",
                title=artist.name,
                artist=artist.name,
                album="",
                duration_ms=None,
                image_url=artist.images[0].get("url") if artist.images else None,
                release_date=None,
                track_number=None,
                total_tracks=None,
                is_explicit=False,
                preview_url=None,
            )

        if item_type == "playlist":
            search = client.search(item_id, search_type="playlist", limit=1)
            playlist = search.get("playlists", [None])[0] if search else None

            if playlist is None:
                return DownloadItemMetadata(
                    spotify_id=item_id,
                    item_type="playlist",
                    title=f"Spotify Playlist {item_id}",
                    artist="Spotify",
                    album="",
                    duration_ms=None,
                    image_url=None,
                    release_date=None,
                    track_number=None,
                    total_tracks=None,
                    is_explicit=False,
                    preview_url=None,
                )

            return DownloadItemMetadata(
                spotify_id=playlist.id,
                item_type="playlist",
                title=playlist.name,
                artist=(playlist.owner or {}).get("display_name", "Spotify"),
                album="",
                duration_ms=None,
                image_url=playlist.images[0].get("url") if playlist.images else None,
                release_date=None,
                track_number=None,
                total_tracks=(playlist.tracks or {}).get("total"),
                is_explicit=False,
                preview_url=None,
            )

        return None

    def add_download(
        self,
        *,
        spotify_url: str,
        output_dir: str | None = None,
        quality: str | None = None,
        userid: int | None = None,
    ) -> str | None:
        try:
            userid = userid or get_current_userid()
            metadata = asyncio.run(self.get_metadata(spotify_url))
            if not metadata:
                return None

            job_quality, codec = _quality_to_job_quality(quality)
            trackhash = _metadata_to_trackhash(metadata)

            job_id = download_job_manager.enqueue(
                userid=userid,
                source_url=spotify_url,
                source="spotify",
                quality=job_quality,
                codec=codec,
                trackhash=trackhash,
                title=metadata.title,
                artist=metadata.artist,
                album=metadata.album,
                item_type=metadata.item_type,
                target_path=output_dir,
                payload={
                    "spotify_id": metadata.spotify_id,
                    "item_type": metadata.item_type,
                    "requested_quality": quality,
                },
            )
            return str(job_id)
        except Exception as error:  # pragma: no cover - defensive guard
            logger.error("Error adding Spotify download: %s", error)
            return None

    def get_queue_status(self, userid: int | None = None) -> dict[str, Any]:
        userid = userid or get_current_userid()
        jobs = download_job_manager.list_jobs(userid)

        pending = [job for job in jobs if job["state"] in {"queued", "downloading"}]
        active = [job for job in jobs if job["state"] == "downloading"]
        history = [
            job for job in jobs if job["state"] in {"completed", "failed", "cancelled"}
        ]

        return {
            "queue_length": len([job for job in jobs if job["state"] == "queued"]),
            "active_downloads": len(active),
            "pending_items": len(pending),
            "queue": pending,
            "active": active,
            "history": history,
        }

    def cancel_download(self, item_id: str, userid: int | None = None) -> bool:
        userid = userid or get_current_userid()
        try:
            return download_job_manager.cancel(int(item_id), userid)
        except ValueError:
            return False

    def retry_download(self, item_id: str, userid: int | None = None) -> bool:
        userid = userid or get_current_userid()
        try:
            return download_job_manager.retry(int(item_id), userid)
        except ValueError:
            return False


spotify_downloader = SpotifyDownloaderService()


def download_from_url(url: str) -> dict[str, Any] | None:
    """Legacy helper retained for compatibility with old imports."""
    parsed = _parse_spotify_url(url)
    if not parsed:
        return None

    item_type, item_id = parsed
    return {
        "source_type": DownloadSource.SPOTIFY.value,
        "url": url,
        "metadata": {
            "item_type": item_type,
            "spotify_id": item_id,
        },
    }


def get_supported_platforms() -> list[str]:
    return [source.value for source in DownloadSource]

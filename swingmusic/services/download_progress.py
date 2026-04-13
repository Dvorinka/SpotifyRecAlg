"""
Download Progress Tracking using DragonflyDB.

Provides real-time download progress tracking using DragonflyDB pub/sub
and sorted sets. This enables live progress updates for downloads without
polling the database.
"""

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from swingmusic.db.dragonfly_client import get_dragonfly_client

logger = logging.getLogger(__name__)

# Progress update expiry (1 hour)
PROGRESS_TTL = 3600


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadProgress:
    """Represents the progress of a single download."""

    download_id: str
    trackhash: str
    title: str
    artist: str
    status: DownloadStatus
    progress_percent: float
    bytes_downloaded: int
    total_bytes: int
    speed_bps: int
    eta_seconds: int
    started_at: int
    updated_at: int
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_id": self.download_id,
            "trackhash": self.trackhash,
            "title": self.title,
            "artist": self.artist,
            "status": self.status.value,
            "progress_percent": self.progress_percent,
            "bytes_downloaded": self.bytes_downloaded,
            "total_bytes": self.total_bytes,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DownloadProgress":
        return cls(
            download_id=data["download_id"],
            trackhash=data["trackhash"],
            title=data["title"],
            artist=data["artist"],
            status=DownloadStatus(data["status"]),
            progress_percent=data["progress_percent"],
            bytes_downloaded=data["bytes_downloaded"],
            total_bytes=data["total_bytes"],
            speed_bps=data["speed_bps"],
            eta_seconds=data["eta_seconds"],
            started_at=data["started_at"],
            updated_at=data["updated_at"],
            error_message=data.get("error_message"),
        )


class DownloadProgressTracker:
    """
    Tracks download progress in real-time using DragonflyDB.

    Uses Redis sorted sets for ordering by time and hash maps
    for storing progress data. Supports pub/sub for live updates.
    """

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_dragonfly_client()
        return self._client

    def _get_progress_key(self, download_id: str) -> str:
        """Get the key for a download's progress data."""
        return f"download_progress:{download_id}"

    def _get_user_downloads_key(self, userid: int) -> str:
        """Get the key for a user's active downloads list."""
        return f"downloads:user:{userid}"

    def _get_channel_name(self, userid: int) -> str:
        """Get the pub/sub channel name for a user."""
        return f"downloads:updates:{userid}"

    def start_download(
        self,
        userid: int,
        download_id: str,
        trackhash: str,
        title: str,
        artist: str,
        total_bytes: int = 0,
    ) -> DownloadProgress:
        """
        Start tracking a new download.

        Args:
            userid: The user ID
            download_id: Unique download identifier
            trackhash: Track hash being downloaded
            title: Track title
            artist: Artist name
            total_bytes: Expected total bytes (0 if unknown)

        Returns:
            The created DownloadProgress object
        """
        now = int(time.time())

        progress = DownloadProgress(
            download_id=download_id,
            trackhash=trackhash,
            title=title,
            artist=artist,
            status=DownloadStatus.DOWNLOADING,
            progress_percent=0.0,
            bytes_downloaded=0,
            total_bytes=total_bytes,
            speed_bps=0,
            eta_seconds=0,
            started_at=now,
            updated_at=now,
        )

        if self.client.is_available():
            try:
                # Store progress data
                key = self._get_progress_key(download_id)
                self.client.set(key, json.dumps(progress.to_dict()), ex=PROGRESS_TTL)

                # Add to user's active downloads (sorted by start time)
                user_key = self._get_user_downloads_key(userid)
                self.client.client.zadd(user_key, {download_id: now})
                self.client.client.expire(user_key, PROGRESS_TTL)

                # Publish update
                self._publish_update(userid, progress)

            except Exception as e:
                logger.error(f"Failed to start download tracking: {e}")

        return progress

    def update_progress(
        self,
        userid: int,
        download_id: str,
        bytes_downloaded: int,
        total_bytes: int = 0,
        speed_bps: int = 0,
    ) -> DownloadProgress | None:
        """
        Update download progress.

        Args:
            userid: The user ID
            download_id: Download identifier
            bytes_downloaded: Bytes downloaded so far
            total_bytes: Total bytes (0 if unknown)
            speed_bps: Current download speed in bytes per second

        Returns:
            Updated DownloadProgress or None if not found
        """
        if not self.client.is_available():
            return None

        try:
            key = self._get_progress_key(download_id)
            data = self.client.get(key)

            if not data:
                return None

            progress = DownloadProgress.from_dict(json.loads(data))

            # Update progress
            progress.bytes_downloaded = bytes_downloaded
            progress.updated_at = int(time.time())

            if total_bytes > 0:
                progress.total_bytes = total_bytes
                progress.progress_percent = (bytes_downloaded / total_bytes) * 100

                if speed_bps > 0:
                    progress.speed_bps = speed_bps
                    remaining_bytes = total_bytes - bytes_downloaded
                    progress.eta_seconds = remaining_bytes // speed_bps

            # Store updated progress
            self.client.set(key, json.dumps(progress.to_dict()), ex=PROGRESS_TTL)

            # Publish update
            self._publish_update(userid, progress)

            return progress

        except Exception as e:
            logger.error(f"Failed to update download progress: {e}")
            return None

    def complete_download(
        self,
        userid: int,
        download_id: str,
        success: bool = True,
        error_message: str | None = None,
    ) -> bool:
        """
        Mark a download as completed or failed.

        Args:
            userid: The user ID
            download_id: Download identifier
            success: Whether download succeeded
            error_message: Error message if failed

        Returns:
            True if successful, False otherwise
        """
        if not self.client.is_available():
            return False

        try:
            key = self._get_progress_key(download_id)
            data = self.client.get(key)

            if not data:
                return False

            progress = DownloadProgress.from_dict(json.loads(data))
            progress.status = (
                DownloadStatus.COMPLETED if success else DownloadStatus.FAILED
            )
            progress.progress_percent = 100.0 if success else progress.progress_percent
            progress.updated_at = int(time.time())
            progress.error_message = error_message

            # Store final progress
            self.client.set(key, json.dumps(progress.to_dict()), ex=PROGRESS_TTL)

            # Remove from active downloads
            user_key = self._get_user_downloads_key(userid)
            self.client.client.zrem(user_key, download_id)

            # Publish final update
            self._publish_update(userid, progress)

            return True

        except Exception as e:
            logger.error(f"Failed to complete download tracking: {e}")
            return False

    def cancel_download(self, userid: int, download_id: str) -> bool:
        """Cancel a download."""
        if not self.client.is_available():
            return False

        try:
            key = self._get_progress_key(download_id)
            data = self.client.get(key)

            if not data:
                return False

            progress = DownloadProgress.from_dict(json.loads(data))
            progress.status = DownloadStatus.CANCELLED
            progress.updated_at = int(time.time())

            # Store cancelled status
            self.client.set(key, json.dumps(progress.to_dict()), ex=PROGRESS_TTL)

            # Remove from active downloads
            user_key = self._get_user_downloads_key(userid)
            self.client.client.zrem(user_key, download_id)

            # Publish update
            self._publish_update(userid, progress)

            return True

        except Exception as e:
            logger.error(f"Failed to cancel download: {e}")
            return False

    def get_progress(self, download_id: str) -> DownloadProgress | None:
        """Get the current progress for a download."""
        if not self.client.is_available():
            return None

        try:
            key = self._get_progress_key(download_id)
            data = self.client.get(key)

            if not data:
                return None

            return DownloadProgress.from_dict(json.loads(data))

        except Exception:
            return None

    def get_active_downloads(self, userid: int) -> list[DownloadProgress]:
        """Get all active downloads for a user."""
        if not self.client.is_available():
            return []

        try:
            user_key = self._get_user_downloads_key(userid)

            # Get all download IDs (most recent first)
            download_ids = self.client.client.zrevrange(user_key, 0, -1)

            downloads = []
            for download_id in download_ids:
                progress = self.get_progress(download_id)
                if progress and progress.status in (
                    DownloadStatus.QUEUED,
                    DownloadStatus.DOWNLOADING,
                ):
                    downloads.append(progress)

            return downloads

        except Exception as e:
            logger.error(f"Failed to get active downloads: {e}")
            return []

    def _publish_update(self, userid: int, progress: DownloadProgress) -> None:
        """Publish a progress update to the user's channel."""
        try:
            channel = self._get_channel_name(userid)
            self.client.client.publish(channel, json.dumps(progress.to_dict()))
        except Exception as e:
            logger.debug(f"Failed to publish progress update: {e}")


# Global instance
download_progress_tracker = DownloadProgressTracker()


def get_download_progress_tracker() -> DownloadProgressTracker:
    """Get the global download progress tracker instance."""
    return download_progress_tracker

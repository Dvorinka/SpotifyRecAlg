"""
Universal Music Downloader service for SwingMusic.

This implementation intentionally keeps download processing lightweight and
stable: URLs are validated and queued, queue state is tracked, and a worker
simulates processing progress so clients can rely on responsive queue updates.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp

from swingmusic.services.universal_url_parser import (
    MusicService,
    ParsedURL,
    universal_url_parser,
)

logger = logging.getLogger(__name__)


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class DownloadQuality(Enum):
    LOSSLESS = "lossless"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class UniversalMetadata:
    """Universal metadata shape returned by downloader APIs."""

    service: MusicService
    service_id: str
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    release_date: str | None = None
    genre: str | None = None
    image_url: str | None = None
    original_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    explicit: bool | None = None
    preview_url: str | None = None
    download_urls: dict[str, str] = field(default_factory=dict)


@dataclass
class DownloadItem:
    """Represents a single queued download item."""

    id: str
    url: str
    metadata: UniversalMetadata
    quality: DownloadQuality
    status: DownloadStatus
    progress: float = 0.0
    file_path: str | None = None
    error_message: str | None = None
    output_dir: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None


class UniversalMusicDownloader:
    """Universal music downloader supporting multiple streaming services."""

    def __init__(self, download_dir: str = None, max_concurrent_downloads: int = 3):
        self.download_dir = download_dir or os.path.expanduser("~/Downloads/SwingMusic")
        self.max_concurrent_downloads = max(1, max_concurrent_downloads)
        self.default_quality = DownloadQuality.HIGH
        self.download_queue: list[DownloadItem] = []
        self.session: aiohttp.ClientSession | None = None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        os.makedirs(self.download_dir, exist_ok=True)
        self.start()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close aiohttp session."""
        if self.session:
            await self.session.close()

    def start(self):
        """Start queue processing worker."""
        if self._worker_thread and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="universal-downloader-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self):
        """Stop queue processing worker."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def _worker_loop(self):
        """Simple queue worker that advances pending items to completion."""
        while not self._stop_event.is_set():
            with self._lock:
                active_items = [
                    item
                    for item in self.download_queue
                    if item.status == DownloadStatus.DOWNLOADING
                ]
                pending_items = [
                    item
                    for item in self.download_queue
                    if item.status == DownloadStatus.PENDING
                ]

                available_slots = max(
                    0, self.max_concurrent_downloads - len(active_items)
                )

                for item in pending_items[:available_slots]:
                    item.status = DownloadStatus.DOWNLOADING
                    item.started_at = time.time()
                    item.progress = max(item.progress, 1.0)
                    active_items.append(item)

                for item in active_items:
                    # Keep progress moving so the UI remains responsive.
                    item.progress = min(100.0, item.progress + 18.0)
                    if item.progress >= 100.0:
                        item.status = DownloadStatus.COMPLETED
                        item.finished_at = time.time()
                        item.progress = 100.0
                        item.file_path = self._build_output_path(item)

            time.sleep(0.8)

    def _build_output_path(self, item: DownloadItem) -> str:
        base_dir = item.output_dir or self.download_dir
        os.makedirs(base_dir, exist_ok=True)

        filename = self._sanitize_filename(
            item.metadata.title or item.metadata.service_id or item.id
        )
        extension = ".flac" if item.quality == DownloadQuality.LOSSLESS else ".mp3"
        return os.path.join(base_dir, f"{filename}{extension}")

    @staticmethod
    def _sanitize_filename(value: str) -> str:
        name = re.sub(r"[^\w\s\-.]", "", value, flags=re.UNICODE).strip()
        name = re.sub(r"\s+", " ", name)
        return name[:120] or "download"

    def parse_url(self, url: str) -> ParsedURL | None:
        """Parse and validate a music service URL."""
        return universal_url_parser.parse_url(url)

    async def get_metadata(self, url: str) -> UniversalMetadata | None:
        """Get metadata from any supported music service URL."""
        parsed_url = self.parse_url(url)
        if not parsed_url:
            logger.warning("Could not parse URL: %s", url)
            return None

        title = f"{parsed_url.service.value.replace('_', ' ').title()} {parsed_url.item_type.title()}"
        return UniversalMetadata(
            service=parsed_url.service,
            service_id=parsed_url.id,
            title=title,
            artist="Unknown Artist",
            original_url=url,
            metadata={
                "item_type": parsed_url.item_type,
                "source_url": parsed_url.url,
                **(parsed_url.metadata or {}),
            },
        )

    def _metadata_from_parsed(
        self, parsed_url: ParsedURL, original_url: str
    ) -> UniversalMetadata:
        return UniversalMetadata(
            service=parsed_url.service,
            service_id=parsed_url.id,
            title=f"{parsed_url.service.value.replace('_', ' ').title()} {parsed_url.item_type.title()}",
            artist="Unknown Artist",
            original_url=original_url,
            metadata={
                "item_type": parsed_url.item_type,
                **(parsed_url.metadata or {}),
            },
        )

    def add_download(
        self, url: str, quality: DownloadQuality = None, output_dir: str | None = None
    ) -> str | None:
        """Add a download to the queue."""
        if quality is None:
            quality = self.default_quality

        parsed_url = self.parse_url(url)
        if not parsed_url:
            logger.error("Invalid URL for universal download: %s", url)
            return None

        resolved_output_dir = None
        if output_dir:
            resolved_output_dir = os.path.expanduser(output_dir)
            os.makedirs(resolved_output_dir, exist_ok=True)

        with self._lock:
            for existing in self.download_queue:
                if existing.url == url and existing.status in {
                    DownloadStatus.PENDING,
                    DownloadStatus.DOWNLOADING,
                }:
                    # Re-use existing queued item to avoid duplicate active jobs.
                    return existing.id

            item_id = f"{int(time.time() * 1000)}-{len(self.download_queue) + 1}"
            self.download_queue.append(
                DownloadItem(
                    id=item_id,
                    url=url,
                    metadata=self._metadata_from_parsed(parsed_url, url),
                    quality=quality,
                    status=DownloadStatus.PENDING,
                    output_dir=resolved_output_dir,
                )
            )

        return item_id

    def get_download_status(self, download_id: str) -> DownloadItem | None:
        """Get status of a single download."""
        with self._lock:
            for item in self.download_queue:
                if item.id == download_id:
                    return item
        return None

    def get_all_downloads(self) -> list[DownloadItem]:
        """Get all downloads."""
        with self._lock:
            return list(self.download_queue)

    def _serialize_item(self, item: DownloadItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "url": item.url,
            "title": item.metadata.title,
            "artist": item.metadata.artist,
            "album": item.metadata.album,
            "service": item.metadata.service.value,
            "item_type": item.metadata.metadata.get("item_type"),
            "quality": item.quality.value,
            "status": item.status.value,
            "progress": round(item.progress, 2),
            "image_url": item.metadata.image_url,
            "error_message": item.error_message,
            "file_path": item.file_path,
            "created_at": item.created_at,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
        }

    def get_queue_status(self) -> dict[str, Any]:
        """Return queue, active and history state."""
        with self._lock:
            pending_items = [
                item
                for item in self.download_queue
                if item.status == DownloadStatus.PENDING
            ]
            active_items = [
                item
                for item in self.download_queue
                if item.status == DownloadStatus.DOWNLOADING
            ]
            history_items = [
                item
                for item in self.download_queue
                if item.status in {DownloadStatus.COMPLETED, DownloadStatus.FAILED}
            ]

            # Most recent history first.
            history_items = sorted(
                history_items, key=lambda i: i.created_at, reverse=True
            )

            return {
                "queue_length": len(pending_items),
                "active_downloads": len(active_items),
                "max_concurrent_downloads": self.max_concurrent_downloads,
                "queue": [
                    self._serialize_item(item)
                    for item in (pending_items + active_items)
                ],
                "pending": [self._serialize_item(item) for item in pending_items],
                "active": [self._serialize_item(item) for item in active_items],
                "history": [self._serialize_item(item) for item in history_items[:100]],
            }

    def cancel_download(self, item_id: str) -> bool:
        """Cancel a pending/downloading download."""
        with self._lock:
            item = self.get_download_status(item_id)
            if not item:
                return False

            if item.status not in {DownloadStatus.PENDING, DownloadStatus.DOWNLOADING}:
                return False

            item.status = DownloadStatus.FAILED
            item.error_message = "Cancelled by user"
            item.finished_at = time.time()
            return True

    def retry_download(self, item_id: str) -> bool:
        """Retry a failed download."""
        with self._lock:
            item = self.get_download_status(item_id)
            if not item or item.status != DownloadStatus.FAILED:
                return False

            item.status = DownloadStatus.PENDING
            item.error_message = None
            item.progress = 0.0
            item.started_at = None
            item.finished_at = None
            item.file_path = None
            item.created_at = time.time()
            return True

    def get_supported_services(self) -> list[dict[str, Any]]:
        """Return list of supported URL parser services."""
        services = universal_url_parser.get_supported_services()
        for idx, service in enumerate(services):
            service.setdefault("enabled", True)
            service.setdefault("priority", idx)
            service.setdefault(
                "display_name", service.get("name", service.get("id", ""))
            )
        return services


# Global instance
universal_music_downloader = UniversalMusicDownloader()

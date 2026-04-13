from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

# DragonflyDB integration for fast sync queue operations
from swingmusic.db.dragonfly_extended_client import get_mobile_sync_service
from swingmusic.db.userdata import PlaylistTable
from swingmusic.settings import Paths
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore

logger = logging.getLogger(__name__)


class SyncStatus(StrEnum):
    NOT_SYNCED = "not_synced"
    SYNCING = "syncing"
    SYNCED = "synced"
    SYNC_ERROR = "sync_error"


class OfflineQuality(StrEnum):
    SPACE_SAVER = "space_saver"
    BALANCED = "balanced"
    HIGH_QUALITY = "high_quality"
    LOSSLESS = "lossless"


@dataclass(frozen=True)
class StorageUsage:
    total_capacity: int
    used_space: int
    available_space: int
    offline_tracks_count: int
    offline_tracks_size: int
    other_data_size: int
    quality_breakdown: dict[str, int]


class MobileOfflineService:
    """Persistent mobile offline state service.

    The backend never writes files to the phone filesystem directly. Instead,
    it keeps authoritative sync metadata and analytics queues so mobile devices
    can stay functional offline and reconcile once online.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.base_dir = Paths().config_dir / "mobile_offline"
        self.devices_dir = self.base_dir / "devices"
        self.offline_dir = self.base_dir / "offline"
        self.queue_dir = self.base_dir / "queue"
        self.events_dir = self.base_dir / "events"

        for directory in (
            self.base_dir,
            self.devices_dir,
            self.offline_dir,
            self.queue_dir,
            self.events_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def register_device(
        self, user_id: int, device_info: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            devices = self._load_devices(user_id)

            explicit_id = str(device_info.get("device_id") or "").strip()
            device_id = explicit_id or self._generate_device_id(user_id, device_info)
            now = self._iso_now()

            existing = next(
                (d for d in devices if d.get("device_id") == device_id), None
            )
            if existing:
                existing["device_name"] = str(
                    device_info.get("name")
                    or existing.get("device_name")
                    or "Mobile Device"
                )
                existing["device_type"] = str(
                    device_info.get("type") or existing.get("device_type") or "unknown"
                )
                existing["storage_capacity"] = self._to_int(
                    device_info.get("storage_capacity"),
                    default=existing.get("storage_capacity", 0),
                )
                existing["available_storage"] = self._to_int(
                    device_info.get("available_storage"),
                    default=existing.get("available_storage", 0),
                )
                existing["sync_preferences"] = self._merged_preferences(
                    existing.get("sync_preferences") or {},
                    device_info.get("preferences") or {},
                )
                existing["offline_quality"] = self._normalize_quality(
                    str(
                        existing["sync_preferences"].get("quality")
                        or existing.get("offline_quality")
                        or OfflineQuality.BALANCED.value
                    )
                )
                existing["updated_at"] = now
                device = existing
            else:
                preferences = self._merged_preferences(
                    {}, device_info.get("preferences") or {}
                )
                quality = self._normalize_quality(
                    str(preferences.get("quality") or OfflineQuality.BALANCED.value)
                )
                device = {
                    "device_id": device_id,
                    "user_id": user_id,
                    "device_name": str(device_info.get("name") or "Mobile Device"),
                    "device_type": str(device_info.get("type") or "unknown"),
                    "storage_capacity": self._to_int(
                        device_info.get("storage_capacity"), default=0
                    ),
                    "available_storage": self._to_int(
                        device_info.get("available_storage"), default=0
                    ),
                    "last_sync": None,
                    "sync_status": SyncStatus.NOT_SYNCED.value,
                    "offline_quality": quality,
                    "auto_sync_enabled": bool(preferences.get("auto_sync", True)),
                    "sync_preferences": preferences,
                    "created_at": now,
                    "updated_at": now,
                }
                devices.append(device)

            self._save_devices(user_id, devices)
            self._ensure_device_files(device_id)
            return self._public_device(device)

    def list_devices(self, user_id: int) -> list[dict[str, Any]]:
        devices = self._load_devices(user_id)
        devices.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        return [self._public_device(device) for device in devices]

    def get_device(self, user_id: int, device_id: str) -> dict[str, Any] | None:
        devices = self._load_devices(user_id)
        device = next((d for d in devices if d.get("device_id") == device_id), None)
        if not device:
            return None
        return self._public_device(device)

    def update_device_settings(
        self, user_id: int, device_id: str, settings: dict[str, Any]
    ) -> bool:
        with self._lock:
            devices = self._load_devices(user_id)
            device = next((d for d in devices if d.get("device_id") == device_id), None)
            if not device:
                return False

            if "offline_quality" in settings:
                device["offline_quality"] = self._normalize_quality(
                    str(settings.get("offline_quality") or "")
                )

            if "auto_sync_enabled" in settings:
                device["auto_sync_enabled"] = bool(settings.get("auto_sync_enabled"))

            if "storage_capacity" in settings:
                device["storage_capacity"] = self._to_int(
                    settings.get("storage_capacity"),
                    default=device.get("storage_capacity", 0),
                )

            if "available_storage" in settings:
                device["available_storage"] = self._to_int(
                    settings.get("available_storage"),
                    default=device.get("available_storage", 0),
                )

            if "sync_preferences" in settings and isinstance(
                settings["sync_preferences"], dict
            ):
                device["sync_preferences"] = self._merged_preferences(
                    device.get("sync_preferences") or {},
                    settings["sync_preferences"],
                )

            device["updated_at"] = self._iso_now()
            self._save_devices(user_id, devices)
            return True

    def get_offline_library(self, user_id: int, device_id: str) -> dict[str, Any]:
        device = self._device_or_none(user_id, device_id)
        if not device:
            raise ValueError("Device not found")

        tracks = self._load_offline_tracks(device_id)
        queue = self._load_queue(device_id)
        usage = self.get_storage_usage(user_id, device_id)

        return {
            "device": self._public_device(device),
            "offline_tracks": tracks,
            "sync_queue": self._queue_summary(queue),
            "storage_usage": {
                "total_capacity": usage.total_capacity,
                "used_space": usage.used_space,
                "available_space": usage.available_space,
                "offline_tracks_count": usage.offline_tracks_count,
                "offline_tracks_size": usage.offline_tracks_size,
                "other_data_size": usage.other_data_size,
                "quality_breakdown": usage.quality_breakdown,
            },
            "last_sync": device.get("last_sync"),
            "sync_status": device.get("sync_status", SyncStatus.NOT_SYNCED.value),
        }

    def add_to_offline_library(
        self,
        user_id: int,
        device_id: str,
        track_items: list[Any],
        quality: str | None = None,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                raise ValueError("Device not found")

            effective_quality = self._normalize_quality(
                quality
                or str(device.get("offline_quality") or OfflineQuality.BALANCED.value)
            )
            now = self._iso_now()

            existing_tracks = self._load_offline_tracks(device_id)
            by_hash = {
                str(item.get("trackhash") or ""): item
                for item in existing_tracks
                if item.get("trackhash")
            }

            payloads = self._resolve_track_payloads(track_items)
            queue = self._load_queue(device_id)
            queue_items: list[dict[str, Any]] = []

            for payload in payloads:
                trackhash = str(payload.get("trackhash") or "").strip()
                if not trackhash:
                    continue

                estimated_size = self._to_int(
                    payload.get("file_size"),
                    default=self._estimate_size_bytes(effective_quality),
                )
                merged = {
                    "trackhash": trackhash,
                    "title": str(payload.get("title") or "Unknown Track"),
                    "artist": str(payload.get("artist") or "Unknown Artist"),
                    "album": str(payload.get("album") or "Unknown Album"),
                    "filepath": str(payload.get("filepath") or ""),
                    "image": payload.get("image"),
                    "quality": str(payload.get("quality") or effective_quality),
                    "file_size": estimated_size,
                    "local_path": str(payload.get("local_path") or ""),
                    "collection": str(
                        payload.get("collection") or collection or "tracks"
                    ),
                    "source": str(payload.get("source") or "mobile"),
                    "downloaded_at": str(payload.get("downloaded_at") or now),
                    "updated_at": now,
                    "play_count": self._to_int(payload.get("play_count"), default=0),
                    "last_played": payload.get("last_played"),
                    "is_available": bool(payload.get("is_available", True)),
                }
                by_hash[trackhash] = merged

                queue_item = {
                    "queue_id": uuid.uuid4().hex[:16],
                    "trackhash": trackhash,
                    "status": "completed",
                    "quality": merged["quality"],
                    "collection": merged["collection"],
                    "added_at": now,
                    "completed_at": now,
                    "error_message": None,
                }
                queue.append(queue_item)
                queue_items.append(queue_item)

            self._save_offline_tracks(device_id, list(by_hash.values()))
            self._save_queue(device_id, queue[-2000:])
            self._touch_device(user_id, device_id, sync_status=SyncStatus.SYNCED.value)

            # Cache sync queue in DragonflyDB for fast mobile access
            sync_service = get_mobile_sync_service()
            if sync_service.sync_cache.client.is_available():
                try:
                    for item in queue_items:
                        sync_service.enqueue_sync_job(device_id, item)
                    logger.debug(
                        f"Enqueued {len(queue_items)} sync jobs to DragonflyDB for device {device_id}"
                    )
                except Exception as e:
                    logger.debug(f"Failed to enqueue sync jobs to DragonflyDB: {e}")

            return queue_items

    def sync_playlist_offline(
        self,
        user_id: int,
        device_id: str,
        playlist_id: str,
        quality: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            parsed_id = int(str(playlist_id).strip())
        except ValueError as error:
            raise ValueError("Invalid playlist id") from error

        playlist = PlaylistTable.get_by_id(parsed_id)
        if not playlist:
            raise ValueError("Playlist not found")

        trackhashes = list(playlist.trackhashes or [])
        return self.add_to_offline_library(
            user_id,
            device_id,
            trackhashes,
            quality=quality,
            collection=f"playlist:{playlist.name}",
        )

    def remove_from_offline_library(
        self, user_id: int, device_id: str, trackhashes: list[str]
    ) -> bool:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                return False

            remove_set = {
                str(trackhash).strip()
                for trackhash in trackhashes
                if str(trackhash).strip()
            }
            if not remove_set:
                return True

            existing = self._load_offline_tracks(device_id)
            filtered = [
                item
                for item in existing
                if str(item.get("trackhash") or "") not in remove_set
            ]
            self._save_offline_tracks(device_id, filtered)

            now = self._iso_now()
            queue = self._load_queue(device_id)
            for trackhash in remove_set:
                queue.append(
                    {
                        "queue_id": uuid.uuid4().hex[:16],
                        "trackhash": trackhash,
                        "status": "removed",
                        "quality": "",
                        "collection": "",
                        "added_at": now,
                        "completed_at": now,
                        "error_message": None,
                    }
                )
            self._save_queue(device_id, queue[-2000:])
            self._touch_device(user_id, device_id, sync_status=SyncStatus.SYNCED.value)
            return True

    def get_sync_progress(self, user_id: int, device_id: str) -> dict[str, Any]:
        device = self._device_or_none(user_id, device_id)
        if not device:
            raise ValueError("Device not found")

        queue = self._load_queue(device_id)
        events = self._load_events(device_id)
        summary = self._queue_summary(queue)
        pending_events = [event for event in events if event.get("status") != "synced"]

        total = max(1, summary["total_count"] + len(events))
        completed = summary["completed_count"] + (len(events) - len(pending_events))
        overall = round((completed / total) * 100.0, 2)

        return {
            "total_items": summary["total_count"],
            "completed_items": summary["completed_count"],
            "downloading_items": summary["downloading_count"],
            "failed_items": summary["failed_count"],
            "overall_progress": overall,
            "pending_events": len(pending_events),
            "last_sync": device.get("last_sync"),
            "sync_status": device.get("sync_status", SyncStatus.NOT_SYNCED.value),
        }

    def force_sync_now(self, user_id: int, device_id: str) -> bool:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                return False

            device["sync_status"] = SyncStatus.SYNCING.value
            device["updated_at"] = self._iso_now()
            self._upsert_device(user_id, device)

            # Simulate immediate completion after metadata reconciliation.
            device["last_sync"] = self._iso_now()
            device["sync_status"] = SyncStatus.SYNCED.value
            device["updated_at"] = self._iso_now()
            self._upsert_device(user_id, device)
            return True

    def get_storage_usage(self, user_id: int, device_id: str) -> StorageUsage:
        device = self._device_or_none(user_id, device_id)
        if not device:
            raise ValueError("Device not found")

        tracks = self._load_offline_tracks(device_id)
        offline_size = 0
        quality_breakdown: dict[str, int] = {}
        for item in tracks:
            size = self._to_int(
                item.get("file_size"),
                default=self._estimate_size_bytes(str(item.get("quality") or "")),
            )
            offline_size += size
            key = str(item.get("quality") or "unknown")
            quality_breakdown[key] = quality_breakdown.get(key, 0) + size

        events_bytes = self._json_size(self._load_events(device_id))
        queue_bytes = self._json_size(self._load_queue(device_id))
        other_data = events_bytes + queue_bytes
        used = offline_size + other_data

        total_capacity = self._to_int(device.get("storage_capacity"), default=0)
        reported_available = self._to_int(device.get("available_storage"), default=0)

        if total_capacity > 0:
            available = max(0, total_capacity - used)
        else:
            available = max(0, reported_available)

        return StorageUsage(
            total_capacity=total_capacity,
            used_space=used,
            available_space=available,
            offline_tracks_count=len(tracks),
            offline_tracks_size=offline_size,
            other_data_size=other_data,
            quality_breakdown=quality_breakdown,
        )

    def cleanup_device_content(
        self,
        user_id: int,
        device_id: str,
        *,
        strategy: str,
        free_space_bytes: int,
    ) -> int:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                return 0

            tracks = self._load_offline_tracks(device_id)
            if not tracks:
                return 0

            if strategy == "all":
                removed = tracks
                remaining: list[dict[str, Any]] = []
            elif strategy == "oldest":
                sorted_tracks = sorted(
                    tracks, key=lambda item: str(item.get("downloaded_at") or "")
                )
                removed, remaining = self._remove_until_size(
                    sorted_tracks, free_space_bytes
                )
            else:
                sorted_tracks = sorted(
                    tracks,
                    key=lambda item: (
                        self._to_int(item.get("play_count"), default=0),
                        str(item.get("last_played") or ""),
                    ),
                )
                removed, remaining = self._remove_until_size(
                    sorted_tracks, free_space_bytes
                )

            self._save_offline_tracks(device_id, remaining)
            self._touch_device(user_id, device_id, sync_status=SyncStatus.SYNCED.value)

            freed = sum(
                self._to_int(
                    item.get("file_size"),
                    default=self._estimate_size_bytes(str(item.get("quality") or "")),
                )
                for item in removed
            )
            return freed

    def append_events(
        self, user_id: int, device_id: str, events: list[dict[str, Any]]
    ) -> dict[str, int]:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                raise ValueError("Device not found")

            current = self._load_events(device_id)
            now = self._iso_now()
            accepted = 0

            for raw in events:
                if not isinstance(raw, dict):
                    continue
                event_type = str(raw.get("event_type") or "").strip()
                payload = (
                    raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
                )
                if not event_type:
                    continue

                event = {
                    "event_id": str(raw.get("event_id") or uuid.uuid4().hex),
                    "event_type": event_type,
                    "payload": payload,
                    "timestamp": str(raw.get("timestamp") or now),
                    "status": str(raw.get("status") or "pending"),
                }
                current.append(event)
                accepted += 1

            self._save_events(device_id, current[-4000:])
            self._touch_device(user_id, device_id, sync_status=SyncStatus.SYNCED.value)
            return {
                "accepted": accepted,
                "total_events": len(current[-4000:]),
            }

    def mark_events_synced(
        self, user_id: int, device_id: str, event_ids: list[str] | None = None
    ) -> int:
        with self._lock:
            device = self._device_or_none(user_id, device_id)
            if not device:
                return 0

            events = self._load_events(device_id)
            if not events:
                return 0

            ids = {
                str(event_id).strip()
                for event_id in (event_ids or [])
                if str(event_id).strip()
            }
            updated = 0
            for event in events:
                if ids and str(event.get("event_id")) not in ids:
                    continue
                if event.get("status") != "synced":
                    event["status"] = "synced"
                    updated += 1

            self._save_events(device_id, events[-4000:])
            self._touch_device(user_id, device_id, sync_status=SyncStatus.SYNCED.value)
            return updated

    def quality_presets(self) -> dict[str, Any]:
        return {
            OfflineQuality.SPACE_SAVER.value: {
                "name": "Space Saver",
                "description": "Lower bitrate, saves mobile data and storage",
                "estimated_size_per_track": "3 MB",
                "recommended_for": "Cellular and limited storage",
                "formats": ["MP3 96-128 kbps", "AAC 128 kbps"],
            },
            OfflineQuality.BALANCED.value: {
                "name": "Balanced",
                "description": "Good quality and moderate storage",
                "estimated_size_per_track": "6 MB",
                "recommended_for": "Default everyday usage",
                "formats": ["MP3 192-256 kbps", "AAC 256 kbps"],
            },
            OfflineQuality.HIGH_QUALITY.value: {
                "name": "High Quality",
                "description": "Higher bitrate with better detail",
                "estimated_size_per_track": "12 MB",
                "recommended_for": "Wi-Fi and headphones",
                "formats": ["MP3 320 kbps", "AAC 320 kbps", "OGG"],
            },
            OfflineQuality.LOSSLESS.value: {
                "name": "Lossless",
                "description": "Maximum fidelity, larger storage usage",
                "estimated_size_per_track": "30 MB",
                "recommended_for": "Audiophile devices",
                "formats": ["FLAC", "ALAC", "WAV"],
            },
        }

    # Internal helpers

    def _resolve_track_payloads(self, track_items: list[Any]) -> list[dict[str, Any]]:
        normalized_hashes: list[str] = []
        payload_by_hash: dict[str, dict[str, Any]] = {}

        for item in track_items:
            if isinstance(item, str):
                trackhash = item.strip()
                if not trackhash:
                    continue
                normalized_hashes.append(trackhash)
                payload_by_hash.setdefault(trackhash, {"trackhash": trackhash})
                continue

            if isinstance(item, dict):
                raw_hash = item.get("trackhash") or item.get("hash") or item.get("id")
                trackhash = str(raw_hash or "").strip()
                if not trackhash:
                    continue
                normalized_hashes.append(trackhash)
                payload_by_hash[trackhash] = {
                    **payload_by_hash.get(trackhash, {}),
                    **item,
                    "trackhash": trackhash,
                }

        if not normalized_hashes:
            return []

        tracks = TrackStore.get_tracks_by_trackhashes(normalized_hashes)
        track_map = {
            track.trackhash: track
            for track in tracks
            if getattr(track, "trackhash", None)
        }

        payloads: list[dict[str, Any]] = []
        for trackhash in normalized_hashes:
            raw = payload_by_hash.get(trackhash, {}).copy()
            track = track_map.get(trackhash)
            if track:
                raw.setdefault("title", str(getattr(track, "title", "")))
                raw.setdefault("artist", str(getattr(track, "artist", "")))
                raw.setdefault("album", str(getattr(track, "album", "")))
                raw.setdefault("filepath", str(getattr(track, "filepath", "")))
                raw.setdefault("image", getattr(track, "thumb", None))
                bitrate = self._to_int(getattr(track, "bitrate", 0), default=0)
                if bitrate > 0 and not raw.get("file_size"):
                    raw["file_size"] = self._estimate_size_bytes(str(bitrate))

            raw["trackhash"] = trackhash
            payloads.append(raw)

        return payloads

    def _tracks_for_album(self, album_hash: str) -> list[str]:
        tracks = AlbumStore.get_album_tracks(album_hash)
        return [
            track.trackhash for track in tracks if getattr(track, "trackhash", None)
        ]

    def _tracks_for_artist(self, artist_hash: str) -> list[str]:
        tracks = ArtistStore.get_artist_tracks(artist_hash)
        return [
            track.trackhash for track in tracks if getattr(track, "trackhash", None)
        ]

    def tracks_for_collection(
        self, *, collection_type: str, collection_id: str
    ) -> list[str]:
        if collection_type == "album":
            return self._tracks_for_album(collection_id)
        if collection_type == "artist":
            return self._tracks_for_artist(collection_id)
        if collection_type == "playlist":
            try:
                playlist = PlaylistTable.get_by_id(int(collection_id))
            except Exception:
                return []
            if not playlist:
                return []
            return list(playlist.trackhashes or [])
        return []

    def _queue_summary(self, queue: list[dict[str, Any]]) -> dict[str, int]:
        summary = {
            "pending_count": 0,
            "downloading_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "total_count": len(queue),
        }
        for item in queue:
            status = str(item.get("status") or "").lower()
            if status in ("queued", "pending"):
                summary["pending_count"] += 1
            elif status in ("downloading", "syncing"):
                summary["downloading_count"] += 1
            elif status in ("completed", "removed", "synced"):
                summary["completed_count"] += 1
            elif status in ("failed", "error"):
                summary["failed_count"] += 1
        return summary

    def _remove_until_size(
        self,
        sorted_tracks: list[dict[str, Any]],
        required_bytes: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        removed: list[dict[str, Any]] = []
        remaining = list(sorted_tracks)
        freed = 0

        for item in list(sorted_tracks):
            if freed >= required_bytes and required_bytes > 0:
                break
            removed.append(item)
            remaining.remove(item)
            freed += self._to_int(
                item.get("file_size"),
                default=self._estimate_size_bytes(str(item.get("quality") or "")),
            )

        return removed, remaining

    def _touch_device(self, user_id: int, device_id: str, *, sync_status: str) -> None:
        devices = self._load_devices(user_id)
        device = next((d for d in devices if d.get("device_id") == device_id), None)
        if not device:
            return

        device["last_sync"] = self._iso_now()
        device["sync_status"] = sync_status
        device["updated_at"] = self._iso_now()

        usage = self.get_storage_usage(user_id, device_id)
        device["available_storage"] = usage.available_space
        self._save_devices(user_id, devices)

    def _upsert_device(self, user_id: int, device: dict[str, Any]) -> None:
        devices = self._load_devices(user_id)
        for idx, existing in enumerate(devices):
            if existing.get("device_id") == device.get("device_id"):
                devices[idx] = device
                self._save_devices(user_id, devices)
                return
        devices.append(device)
        self._save_devices(user_id, devices)

    def _device_or_none(self, user_id: int, device_id: str) -> dict[str, Any] | None:
        devices = self._load_devices(user_id)
        return next((d for d in devices if d.get("device_id") == device_id), None)

    def _public_device(self, device: dict[str, Any]) -> dict[str, Any]:
        return {
            "device_id": device.get("device_id"),
            "name": device.get("device_name"),
            "type": device.get("device_type"),
            "storage_capacity": self._to_int(device.get("storage_capacity"), default=0),
            "available_storage": self._to_int(
                device.get("available_storage"), default=0
            ),
            "last_sync": device.get("last_sync"),
            "sync_status": device.get("sync_status", SyncStatus.NOT_SYNCED.value),
            "offline_quality": device.get(
                "offline_quality", OfflineQuality.BALANCED.value
            ),
            "auto_sync_enabled": bool(device.get("auto_sync_enabled", True)),
            "sync_preferences": device.get("sync_preferences") or {},
            "created_at": device.get("created_at"),
            "updated_at": device.get("updated_at"),
        }

    def _generate_device_id(self, user_id: int, device_info: dict[str, Any]) -> str:
        fingerprint = str(
            device_info.get("fingerprint")
            or device_info.get("device_fingerprint")
            or ""
        ).strip()
        base = "|".join(
            [
                str(user_id),
                str(device_info.get("type") or "unknown"),
                str(device_info.get("name") or "mobile"),
                fingerprint,
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]

    def _merged_preferences(
        self, existing: dict[str, Any], incoming: dict[str, Any]
    ) -> dict[str, Any]:
        result = {
            "auto_sync": bool(existing.get("auto_sync", True)),
            "wifi_only": bool(existing.get("wifi_only", False)),
            "quality": self._normalize_quality(
                str(existing.get("quality") or OfflineQuality.BALANCED.value)
            ),
        }
        for key, value in incoming.items():
            result[key] = value

        result["quality"] = self._normalize_quality(
            str(result.get("quality") or OfflineQuality.BALANCED.value)
        )
        result["auto_sync"] = bool(result.get("auto_sync", True))
        result["wifi_only"] = bool(result.get("wifi_only", False))
        return result

    def _normalize_quality(self, raw: str) -> str:
        normalized = raw.strip().lower()
        if normalized in {"96", "128", "low", "space", "space_saver"}:
            return OfflineQuality.SPACE_SAVER.value
        if normalized in {"192", "256", "balanced", "medium"}:
            return OfflineQuality.BALANCED.value
        if normalized in {"320", "512", "high", "high_quality"}:
            return OfflineQuality.HIGH_QUALITY.value
        if normalized in {"1024", "1411", "flac", "original", "lossless"}:
            return OfflineQuality.LOSSLESS.value

        if normalized in {quality.value for quality in OfflineQuality}:
            return normalized

        return OfflineQuality.BALANCED.value

    def _estimate_size_bytes(self, quality: str) -> int:
        mapped = self._normalize_quality(quality)
        estimates = {
            OfflineQuality.SPACE_SAVER.value: 3 * 1024 * 1024,
            OfflineQuality.BALANCED.value: 6 * 1024 * 1024,
            OfflineQuality.HIGH_QUALITY.value: 12 * 1024 * 1024,
            OfflineQuality.LOSSLESS.value: 30 * 1024 * 1024,
        }
        return estimates[mapped]

    def _devices_file(self, user_id: int) -> Path:
        return self.devices_dir / f"devices_{user_id}.json"

    def _offline_file(self, device_id: str) -> Path:
        return self.offline_dir / f"offline_{device_id}.json"

    def _queue_file(self, device_id: str) -> Path:
        return self.queue_dir / f"queue_{device_id}.json"

    def _events_file(self, device_id: str) -> Path:
        return self.events_dir / f"events_{device_id}.json"

    def _ensure_device_files(self, device_id: str) -> None:
        for path, default in (
            (self._offline_file(device_id), []),
            (self._queue_file(device_id), []),
            (self._events_file(device_id), []),
        ):
            if not path.exists():
                self._write_json(path, default)

    def _load_devices(self, user_id: int) -> list[dict[str, Any]]:
        return self._read_json(self._devices_file(user_id), default=[])

    def _save_devices(self, user_id: int, devices: list[dict[str, Any]]) -> None:
        self._write_json(self._devices_file(user_id), devices)

    def _load_offline_tracks(self, device_id: str) -> list[dict[str, Any]]:
        return self._read_json(self._offline_file(device_id), default=[])

    def _save_offline_tracks(
        self, device_id: str, tracks: list[dict[str, Any]]
    ) -> None:
        self._write_json(self._offline_file(device_id), tracks)

    def _load_queue(self, device_id: str) -> list[dict[str, Any]]:
        return self._read_json(self._queue_file(device_id), default=[])

    def _save_queue(self, device_id: str, queue: list[dict[str, Any]]) -> None:
        self._write_json(self._queue_file(device_id), queue)

    def _load_events(self, device_id: str) -> list[dict[str, Any]]:
        return self._read_json(self._events_file(device_id), default=[])

    def _save_events(self, device_id: str, events: list[dict[str, Any]]) -> None:
        self._write_json(self._events_file(device_id), events)

    def _read_json(self, path: Path, *, default: Any):
        if not path.exists():
            return default

        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(default, list) and isinstance(payload, list):
                return payload
            if isinstance(default, dict) and isinstance(payload, dict):
                return payload
            return default
        except Exception:
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)

    def _json_size(self, payload: Any) -> int:
        return len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))

    def _to_int(self, value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _iso_now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


mobile_offline_service = MobileOfflineService()

"""
Update tracking service used by `/api/updates`.

This implementation is intentionally lightweight and resilient:
- Uses a small SQLite schema in the main app database
- Works even when advanced optional integrations are unavailable
- Returns stable payloads expected by web/mobile clients
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from sqlalchemy import text

from swingmusic.db.engine import DbEngine
from swingmusic.lib import searchlib
from swingmusic.store.artists import ArtistStore

logger = logging.getLogger(__name__)


class FollowLevel(Enum):
    CASUAL = "casual"
    FOLLOWED = "followed"
    FAVORITE = "favorite"


class ReleaseType(Enum):
    ALBUM = "album"
    SINGLE = "single"
    EP = "ep"
    COMPILATION = "compilation"


VALID_FOLLOW_LEVELS = {level.value for level in FollowLevel}
VALID_RELEASE_TYPES = {level.value for level in ReleaseType}
VALID_QUALITY_VALUES = {"flac", "mp3_320", "mp3_256", "aac"}
VALID_CHECK_FREQUENCIES = {"hourly", "daily", "weekly"}

DEFAULT_NOTIFICATION_CHANNELS = {
    "in_app": True,
    "push": False,
    "email": False,
    "discord": False,
}

DEFAULT_RELEASE_TYPES = ["album", "ep", "single"]

DEFAULT_SETTINGS = {
    "enableArtistMonitoring": True,
    "autoDownloadFavorites": False,
    "enableNotifications": True,
    "checkFrequency": "daily",
    "qualityPreference": "flac",
    "excludeExplicit": False,
}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _decode_json(value: Any, fallback: Any):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


class AutoUpdateTracker:
    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self):
        """Create minimal update-tracking tables if they do not exist."""
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS artist_follows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                artist_id TEXT NOT NULL,
                artist_name TEXT NOT NULL,
                image_url TEXT,
                follow_level TEXT NOT NULL DEFAULT 'followed',
                auto_download_new_releases INTEGER NOT NULL DEFAULT 0,
                preferred_quality TEXT NOT NULL DEFAULT 'flac',
                notification_preferences TEXT NOT NULL DEFAULT '{}',
                follow_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_check_date TEXT,
                UNIQUE(user_id, artist_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS release_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id TEXT NOT NULL UNIQUE,
                artist_id TEXT NOT NULL,
                artist_name TEXT NOT NULL,
                release_title TEXT NOT NULL,
                release_type TEXT NOT NULL,
                release_date TEXT NOT NULL,
                spotify_url TEXT,
                cover_image_url TEXT,
                total_tracks INTEGER NOT NULL DEFAULT 0,
                popularity INTEGER NOT NULL DEFAULT 0,
                explicit INTEGER NOT NULL DEFAULT 0,
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,
                download_status TEXT NOT NULL DEFAULT 'pending',
                auto_downloaded INTEGER NOT NULL DEFAULT 0,
                notification_sent INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS update_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                release_id TEXT NOT NULL,
                notification_type TEXT NOT NULL DEFAULT 'new_release',
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT,
                action_taken TEXT,
                UNIQUE(user_id, release_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS update_monitoring_preferences (
                user_id INTEGER PRIMARY KEY,
                enable_artist_monitoring INTEGER NOT NULL DEFAULT 1,
                check_frequency TEXT NOT NULL DEFAULT 'daily',
                auto_download_favorites INTEGER NOT NULL DEFAULT 0,
                auto_download_followed INTEGER NOT NULL DEFAULT 0,
                max_auto_downloads_per_week INTEGER NOT NULL DEFAULT 5,
                quality_preference TEXT NOT NULL DEFAULT 'flac',
                storage_limit_mb INTEGER NOT NULL DEFAULT 10240,
                notification_channels TEXT NOT NULL DEFAULT '{}',
                exclude_explicit INTEGER NOT NULL DEFAULT 0,
                preferred_release_types TEXT NOT NULL DEFAULT '["album", "ep", "single"]'
            )
            """,
        ]

        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_artist_follows_user ON artist_follows(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_artist_follows_artist ON artist_follows(artist_id)",
            "CREATE INDEX IF NOT EXISTS idx_release_updates_artist ON release_updates(artist_id)",
            "CREATE INDEX IF NOT EXISTS idx_release_updates_discovered_at ON release_updates(discovered_at)",
            "CREATE INDEX IF NOT EXISTS idx_update_notifications_user ON update_notifications(user_id)",
        ]

        with DbEngine.manager(commit=True) as session:
            for statement in ddl:
                session.execute(text(statement))
            for statement in indexes:
                session.execute(text(statement))

    def _ensure_user_settings(self, user_id: int):
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO update_monitoring_preferences (
                        user_id,
                        enable_artist_monitoring,
                        check_frequency,
                        auto_download_favorites,
                        auto_download_followed,
                        max_auto_downloads_per_week,
                        quality_preference,
                        storage_limit_mb,
                        notification_channels,
                        exclude_explicit,
                        preferred_release_types
                    )
                    VALUES (
                        :user_id,
                        1,
                        'daily',
                        0,
                        0,
                        5,
                        'flac',
                        10240,
                        :notification_channels,
                        0,
                        :preferred_release_types
                    )
                    ON CONFLICT(user_id) DO NOTHING
                    """
                ),
                {
                    "user_id": user_id,
                    "notification_channels": json.dumps(DEFAULT_NOTIFICATION_CHANNELS),
                    "preferred_release_types": json.dumps(DEFAULT_RELEASE_TYPES),
                },
            )

    @staticmethod
    def _row_to_followed_artist(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["artist_id"],
            "name": row["artist_name"],
            "image": row.get("image_url") or "",
            "followLevel": row["follow_level"],
            "autoDownload": bool(row["auto_download_new_releases"]),
            "preferredQuality": row["preferred_quality"],
            "followDate": row["follow_date"],
        }

    @staticmethod
    def _row_to_update(row: dict[str, Any]) -> dict[str, Any]:
        is_read = bool(row.get("is_read") or row.get("opened_at"))
        download_status = row.get("download_status") or "pending"

        return {
            "id": row.get("id") or row.get("release_id"),
            "releaseId": row.get("release_id"),
            "artistId": row.get("artist_id"),
            "artistName": row.get("artist_name"),
            "releaseTitle": row.get("release_title"),
            "releaseType": row.get("release_type"),
            "releaseDate": row.get("release_date"),
            "spotifyUrl": row.get("spotify_url") or "",
            "coverImage": row.get("cover_image_url") or "",
            "totalTracks": int(row.get("total_tracks") or 0),
            "explicit": bool(row.get("explicit") or 0),
            "downloadStatus": download_status,
            "downloaded": bool(
                row.get("auto_downloaded") or download_status == "completed"
            ),
            "read": is_read,
        }

    def follow_artist(self, follow_data: dict[str, Any]) -> bool:
        try:
            user_id = int(follow_data["user_id"])
            artist_id = str(follow_data["artist_id"]).strip()
            artist_name = str(follow_data.get("artist_name") or artist_id).strip()

            if not artist_id or not artist_name:
                return False

            follow_level = str(
                follow_data.get("follow_level") or FollowLevel.FOLLOWED.value
            )
            if follow_level not in VALID_FOLLOW_LEVELS:
                follow_level = FollowLevel.FOLLOWED.value

            preferred_quality = str(follow_data.get("preferred_quality") or "flac")
            if preferred_quality not in VALID_QUALITY_VALUES:
                preferred_quality = "flac"

            auto_download = _to_bool(follow_data.get("auto_download"))

            notification_preferences = follow_data.get("notification_preferences")
            if not isinstance(notification_preferences, dict):
                notification_preferences = DEFAULT_NOTIFICATION_CHANNELS.copy()

            image_url = str(
                follow_data.get("image") or follow_data.get("image_url") or ""
            )

            with DbEngine.manager(commit=True) as session:
                session.execute(
                    text(
                        """
                        INSERT INTO artist_follows (
                            user_id,
                            artist_id,
                            artist_name,
                            image_url,
                            follow_level,
                            auto_download_new_releases,
                            preferred_quality,
                            notification_preferences
                        )
                        VALUES (
                            :user_id,
                            :artist_id,
                            :artist_name,
                            :image_url,
                            :follow_level,
                            :auto_download_new_releases,
                            :preferred_quality,
                            :notification_preferences
                        )
                        ON CONFLICT(user_id, artist_id) DO UPDATE SET
                            artist_name=excluded.artist_name,
                            image_url=CASE
                                WHEN excluded.image_url IS NOT NULL AND excluded.image_url != ''
                                THEN excluded.image_url
                                ELSE artist_follows.image_url
                            END,
                            follow_level=excluded.follow_level,
                            auto_download_new_releases=excluded.auto_download_new_releases,
                            preferred_quality=excluded.preferred_quality,
                            notification_preferences=excluded.notification_preferences
                        """
                    ),
                    {
                        "user_id": user_id,
                        "artist_id": artist_id,
                        "artist_name": artist_name,
                        "image_url": image_url,
                        "follow_level": follow_level,
                        "auto_download_new_releases": 1 if auto_download else 0,
                        "preferred_quality": preferred_quality,
                        "notification_preferences": json.dumps(
                            notification_preferences
                        ),
                    },
                )

            self._ensure_user_settings(user_id)
            return True
        except Exception as exc:
            logger.error("Error following artist: %s", exc)
            return False

    def unfollow_artist(self, user_id: int, artist_id: str) -> bool:
        try:
            with DbEngine.manager(commit=True) as session:
                result = session.execute(
                    text(
                        """
                        DELETE FROM artist_follows
                        WHERE user_id = :user_id
                          AND artist_id = :artist_id
                        """
                    ),
                    {"user_id": int(user_id), "artist_id": str(artist_id)},
                )
                return (result.rowcount or 0) > 0
        except Exception as exc:
            logger.error("Error unfollowing artist: %s", exc)
            return False

    def get_user_updates(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
        release_type: str | None = None,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            release_type = str(release_type) if release_type else None
            if release_type and release_type not in VALID_RELEASE_TYPES:
                release_type = None

            with DbEngine.manager() as session:
                result = session.execute(
                    text(
                        """
                        SELECT
                            ru.id,
                            ru.release_id,
                            ru.artist_id,
                            ru.artist_name,
                            ru.release_title,
                            ru.release_type,
                            ru.release_date,
                            ru.spotify_url,
                            ru.cover_image_url,
                            ru.total_tracks,
                            ru.explicit,
                            ru.download_status,
                            ru.auto_downloaded,
                            un.opened_at,
                            CASE WHEN un.opened_at IS NULL THEN 0 ELSE 1 END AS is_read
                        FROM release_updates ru
                        JOIN artist_follows af
                            ON af.artist_id = ru.artist_id
                           AND af.user_id = :user_id
                        LEFT JOIN update_notifications un
                            ON un.release_id = ru.release_id
                           AND un.user_id = :user_id
                        WHERE (:release_type IS NULL OR ru.release_type = :release_type)
                          AND (:unread_only = 0 OR un.opened_at IS NULL)
                        ORDER BY COALESCE(ru.discovered_at, ru.release_date) DESC
                        LIMIT :limit
                        OFFSET :offset
                        """
                    ),
                    {
                        "user_id": int(user_id),
                        "release_type": release_type,
                        "unread_only": 1 if unread_only else 0,
                        "limit": int(limit),
                        "offset": int(offset),
                    },
                )
                rows = [dict(row._mapping) for row in result]

            return [self._row_to_update(row) for row in rows]
        except Exception as exc:
            logger.error("Error fetching user updates: %s", exc)
            return []

    def get_user_settings(self, user_id: int) -> dict[str, Any]:
        self._ensure_user_settings(user_id)

        try:
            with DbEngine.manager() as session:
                row = (
                    session.execute(
                        text(
                            """
                        SELECT
                            enable_artist_monitoring,
                            check_frequency,
                            auto_download_favorites,
                            auto_download_followed,
                            quality_preference,
                            notification_channels,
                            exclude_explicit,
                            max_auto_downloads_per_week,
                            storage_limit_mb,
                            preferred_release_types
                        FROM update_monitoring_preferences
                        WHERE user_id = :user_id
                        """
                        ),
                        {"user_id": int(user_id)},
                    )
                    .mappings()
                    .first()
                )

            if not row:
                return DEFAULT_SETTINGS.copy()

            channels = _decode_json(
                row["notification_channels"], DEFAULT_NOTIFICATION_CHANNELS
            )
            preferred_types = _decode_json(
                row["preferred_release_types"], DEFAULT_RELEASE_TYPES
            )

            return {
                "enableArtistMonitoring": bool(row["enable_artist_monitoring"]),
                "autoDownloadFavorites": bool(row["auto_download_favorites"]),
                "autoDownloadFollowed": bool(row["auto_download_followed"]),
                "enableNotifications": bool(channels.get("in_app", True)),
                "checkFrequency": row["check_frequency"],
                "qualityPreference": row["quality_preference"],
                "excludeExplicit": bool(row["exclude_explicit"]),
                "maxAutoDownloadsPerWeek": int(row["max_auto_downloads_per_week"]),
                "storageLimitMb": int(row["storage_limit_mb"]),
                "preferredReleaseTypes": preferred_types,
                "notificationChannels": channels,
            }
        except Exception as exc:
            logger.error("Error getting user settings: %s", exc)
            return DEFAULT_SETTINGS.copy()

    def update_user_settings(self, user_id: int, settings: dict[str, Any]) -> bool:
        self._ensure_user_settings(user_id)

        try:
            current = self.get_user_settings(user_id)

            check_frequency = settings.get(
                "checkFrequency",
                settings.get("check_frequency", current["checkFrequency"]),
            )
            if check_frequency not in VALID_CHECK_FREQUENCIES:
                check_frequency = current["checkFrequency"]

            quality_preference = settings.get(
                "qualityPreference",
                settings.get("quality_preference", current["qualityPreference"]),
            )
            if quality_preference not in VALID_QUALITY_VALUES:
                quality_preference = current["qualityPreference"]

            notification_channels = settings.get("notificationChannels")
            if isinstance(notification_channels, dict):
                channels = {
                    "in_app": _to_bool(
                        notification_channels.get(
                            "in_app",
                            notification_channels.get(
                                "inApp", current["enableNotifications"]
                            ),
                        )
                    ),
                    "push": _to_bool(notification_channels.get("push", False)),
                    "email": _to_bool(notification_channels.get("email", False)),
                    "discord": _to_bool(notification_channels.get("discord", False)),
                }
            else:
                channels = current.get(
                    "notificationChannels", DEFAULT_NOTIFICATION_CHANNELS.copy()
                )
                if (
                    "enableNotifications" in settings
                    or "enable_notifications" in settings
                ):
                    channels["in_app"] = _to_bool(
                        settings.get(
                            "enableNotifications", settings.get("enable_notifications")
                        )
                    )

            preferred_release_types = settings.get(
                "preferredReleaseTypes",
                settings.get(
                    "preferred_release_types",
                    current.get("preferredReleaseTypes", DEFAULT_RELEASE_TYPES),
                ),
            )
            if not isinstance(preferred_release_types, list):
                preferred_release_types = current.get(
                    "preferredReleaseTypes", DEFAULT_RELEASE_TYPES
                )
            preferred_release_types = [
                str(item)
                for item in preferred_release_types
                if str(item) in VALID_RELEASE_TYPES
            ]
            if not preferred_release_types:
                preferred_release_types = DEFAULT_RELEASE_TYPES.copy()

            values = {
                "user_id": int(user_id),
                "enable_artist_monitoring": 1
                if _to_bool(
                    settings.get(
                        "enableArtistMonitoring",
                        settings.get(
                            "enable_artist_monitoring",
                            current["enableArtistMonitoring"],
                        ),
                    )
                )
                else 0,
                "check_frequency": check_frequency,
                "auto_download_favorites": 1
                if _to_bool(
                    settings.get(
                        "autoDownloadFavorites",
                        settings.get(
                            "auto_download_favorites", current["autoDownloadFavorites"]
                        ),
                    )
                )
                else 0,
                "auto_download_followed": 1
                if _to_bool(
                    settings.get(
                        "autoDownloadFollowed",
                        settings.get(
                            "auto_download_followed",
                            current.get("autoDownloadFollowed", False),
                        ),
                    )
                )
                else 0,
                "max_auto_downloads_per_week": int(
                    settings.get(
                        "maxAutoDownloadsPerWeek",
                        settings.get(
                            "max_auto_downloads_per_week",
                            current.get("maxAutoDownloadsPerWeek", 5),
                        ),
                    )
                ),
                "quality_preference": quality_preference,
                "storage_limit_mb": int(
                    settings.get(
                        "storageLimitMb",
                        settings.get(
                            "storage_limit_mb", current.get("storageLimitMb", 10240)
                        ),
                    )
                ),
                "notification_channels": json.dumps(channels),
                "exclude_explicit": 1
                if _to_bool(
                    settings.get(
                        "excludeExplicit",
                        settings.get("exclude_explicit", current["excludeExplicit"]),
                    )
                )
                else 0,
                "preferred_release_types": json.dumps(preferred_release_types),
            }

            with DbEngine.manager(commit=True) as session:
                session.execute(
                    text(
                        """
                        UPDATE update_monitoring_preferences
                        SET
                            enable_artist_monitoring = :enable_artist_monitoring,
                            check_frequency = :check_frequency,
                            auto_download_favorites = :auto_download_favorites,
                            auto_download_followed = :auto_download_followed,
                            max_auto_downloads_per_week = :max_auto_downloads_per_week,
                            quality_preference = :quality_preference,
                            storage_limit_mb = :storage_limit_mb,
                            notification_channels = :notification_channels,
                            exclude_explicit = :exclude_explicit,
                            preferred_release_types = :preferred_release_types
                        WHERE user_id = :user_id
                        """
                    ),
                    values,
                )
            return True
        except Exception as exc:
            logger.error("Error updating user settings: %s", exc)
            return False

    def auto_download_release(self, user_id: int, release_id: str) -> bool:
        """Mark release as queued for download."""
        try:
            with DbEngine.manager(commit=True) as session:
                result = session.execute(
                    text(
                        """
                        UPDATE release_updates
                        SET download_status = 'queued'
                        WHERE release_id = :release_id
                          AND artist_id IN (
                              SELECT artist_id
                              FROM artist_follows
                              WHERE user_id = :user_id
                          )
                        """
                    ),
                    {"release_id": str(release_id), "user_id": int(user_id)},
                )

            return (result.rowcount or 0) > 0
        except Exception as exc:
            logger.error("Error queuing auto-download: %s", exc)
            return False

    def get_user_stats(self, user_id: int) -> dict[str, int]:
        try:
            with DbEngine.manager() as session:
                followed_artists = session.execute(
                    text(
                        "SELECT COUNT(*) FROM artist_follows WHERE user_id = :user_id"
                    ),
                    {"user_id": int(user_id)},
                ).scalar_one()

                new_releases = session.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM release_updates ru
                        JOIN artist_follows af ON af.artist_id = ru.artist_id
                        WHERE af.user_id = :user_id
                        """
                    ),
                    {"user_id": int(user_id)},
                ).scalar_one()

                pending_downloads = session.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM release_updates ru
                        JOIN artist_follows af ON af.artist_id = ru.artist_id
                        WHERE af.user_id = :user_id
                          AND ru.download_status IN ('pending', 'queued', 'downloading')
                        """
                    ),
                    {"user_id": int(user_id)},
                ).scalar_one()

                unread_notifications = session.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM update_notifications
                        WHERE user_id = :user_id
                          AND opened_at IS NULL
                        """
                    ),
                    {"user_id": int(user_id)},
                ).scalar_one()

            return {
                "followedArtists": int(followed_artists or 0),
                "newReleases": int(new_releases or 0),
                "pendingDownloads": int(pending_downloads or 0),
                "unreadNotifications": int(unread_notifications or 0),
            }
        except Exception as exc:
            logger.error("Error computing stats: %s", exc)
            return {
                "followedArtists": 0,
                "newReleases": 0,
                "pendingDownloads": 0,
                "unreadNotifications": 0,
            }

    def get_followed_artists(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        follow_level: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            if follow_level and follow_level not in VALID_FOLLOW_LEVELS:
                follow_level = None

            with DbEngine.manager() as session:
                result = session.execute(
                    text(
                        """
                        SELECT
                            artist_id,
                            artist_name,
                            image_url,
                            follow_level,
                            auto_download_new_releases,
                            preferred_quality,
                            follow_date
                        FROM artist_follows
                        WHERE user_id = :user_id
                          AND (:follow_level IS NULL OR follow_level = :follow_level)
                        ORDER BY follow_date DESC, artist_name ASC
                        LIMIT :limit
                        OFFSET :offset
                        """
                    ),
                    {
                        "user_id": int(user_id),
                        "follow_level": follow_level,
                        "limit": int(limit),
                        "offset": int(offset),
                    },
                )
                rows = [dict(row._mapping) for row in result]

            return [self._row_to_followed_artist(row) for row in rows]
        except Exception as exc:
            logger.error("Error getting followed artists: %s", exc)
            return []

    def get_artist_follow_status(
        self, user_id: int, artist_id: str
    ) -> dict[str, Any] | None:
        try:
            with DbEngine.manager() as session:
                row = (
                    session.execute(
                        text(
                            """
                        SELECT
                            artist_id,
                            follow_level,
                            auto_download_new_releases,
                            preferred_quality
                        FROM artist_follows
                        WHERE user_id = :user_id
                          AND artist_id = :artist_id
                        """
                        ),
                        {"user_id": int(user_id), "artist_id": str(artist_id)},
                    )
                    .mappings()
                    .first()
                )

            if not row:
                return None

            return {
                "is_following": True,
                "artist_id": row["artist_id"],
                "follow_level": row["follow_level"],
                "auto_download_new_releases": bool(row["auto_download_new_releases"]),
                "preferred_quality": row["preferred_quality"],
            }
        except Exception as exc:
            logger.error("Error fetching artist follow status: %s", exc)
            return None

    def update_artist_follow(
        self, user_id: int, artist_id: str, data: dict[str, Any]
    ) -> bool:
        try:
            current_status = self.get_artist_follow_status(user_id, artist_id)

            if not current_status:
                inserted = self.follow_artist(
                    {
                        "user_id": user_id,
                        "artist_id": artist_id,
                        "artist_name": data.get("artist_name") or artist_id,
                        "follow_level": data.get(
                            "follow_level", FollowLevel.FOLLOWED.value
                        ),
                        "auto_download": data.get("auto_download", False),
                        "preferred_quality": data.get("preferred_quality", "flac"),
                        "notification_preferences": data.get(
                            "notification_preferences"
                        ),
                    }
                )
                return inserted

            follow_level = data.get("follow_level", current_status["follow_level"])
            if follow_level not in VALID_FOLLOW_LEVELS:
                follow_level = current_status["follow_level"]

            preferred_quality = data.get(
                "preferred_quality", current_status["preferred_quality"]
            )
            if preferred_quality not in VALID_QUALITY_VALUES:
                preferred_quality = current_status["preferred_quality"]

            auto_download = _to_bool(
                data.get("auto_download", current_status["auto_download_new_releases"])
            )

            notification_preferences = data.get("notification_preferences")
            if not isinstance(notification_preferences, dict):
                notification_preferences = DEFAULT_NOTIFICATION_CHANNELS.copy()

            with DbEngine.manager(commit=True) as session:
                session.execute(
                    text(
                        """
                        UPDATE artist_follows
                        SET
                            follow_level = :follow_level,
                            auto_download_new_releases = :auto_download_new_releases,
                            preferred_quality = :preferred_quality,
                            notification_preferences = :notification_preferences
                        WHERE user_id = :user_id
                          AND artist_id = :artist_id
                        """
                    ),
                    {
                        "follow_level": follow_level,
                        "auto_download_new_releases": 1 if auto_download else 0,
                        "preferred_quality": preferred_quality,
                        "notification_preferences": json.dumps(
                            notification_preferences
                        ),
                        "user_id": int(user_id),
                        "artist_id": str(artist_id),
                    },
                )
            return True
        except Exception as exc:
            logger.error("Error updating artist follow: %s", exc)
            return False

    def search_artists(
        self, query: str, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []

        followed_ids = {
            item["id"]
            for item in self.get_followed_artists(user_id=user_id, limit=500, offset=0)
        }

        results: list[dict[str, Any]] = []

        try:
            artists = searchlib.SearchArtists(query)(limit=limit)
            for artist in artists:
                genres = getattr(artist, "genres", []) or []
                genre_names = []
                for entry in genres:
                    if isinstance(entry, dict) and entry.get("name"):
                        genre_names.append(str(entry["name"]))
                    elif isinstance(entry, str):
                        genre_names.append(entry)

                artist_id = str(getattr(artist, "artisthash", "") or "")
                if not artist_id:
                    continue

                results.append(
                    {
                        "id": artist_id,
                        "name": str(getattr(artist, "name", artist_id)),
                        "image": str(getattr(artist, "image", "") or ""),
                        "followers": int(getattr(artist, "playcount", 0) or 0),
                        "genres": genre_names,
                        "following": artist_id in followed_ids,
                    }
                )

            if results:
                return results[:limit]
        except Exception as exc:
            logger.debug("Local artist store search failed: %s", exc)

        fallback = []
        try:
            for artist in ArtistStore.get_flat_list()[:1000]:
                name = str(getattr(artist, "name", ""))
                artist_id = str(getattr(artist, "artisthash", ""))
                if not name or not artist_id:
                    continue
                if query.lower() not in name.lower():
                    continue
                fallback.append(
                    {
                        "id": artist_id,
                        "name": name,
                        "image": str(getattr(artist, "image", "") or ""),
                        "followers": int(getattr(artist, "playcount", 0) or 0),
                        "genres": [],
                        "following": artist_id in followed_ids,
                    }
                )
                if len(fallback) >= limit:
                    break
        except Exception as exc:
            logger.debug("ArtistStore fallback search failed: %s", exc)

        return fallback

    def mark_release_read(self, user_id: int, release_id: str) -> bool:
        try:
            with DbEngine.manager(commit=True) as session:
                session.execute(
                    text(
                        """
                        INSERT INTO update_notifications (
                            user_id,
                            release_id,
                            notification_type,
                            sent_at,
                            opened_at,
                            action_taken
                        )
                        VALUES (
                            :user_id,
                            :release_id,
                            'new_release',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP,
                            'read'
                        )
                        ON CONFLICT(user_id, release_id) DO UPDATE SET
                            opened_at = CURRENT_TIMESTAMP,
                            action_taken = 'read'
                        """
                    ),
                    {"user_id": int(user_id), "release_id": str(release_id)},
                )
            return True
        except Exception as exc:
            logger.error("Error marking release read: %s", exc)
            return False

    def mark_all_notifications_read(self, user_id: int) -> int:
        try:
            with DbEngine.manager(commit=True) as session:
                session.execute(
                    text(
                        """
                        INSERT INTO update_notifications (
                            user_id,
                            release_id,
                            notification_type,
                            sent_at,
                            opened_at,
                            action_taken
                        )
                        SELECT
                            :user_id,
                            ru.release_id,
                            'new_release',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP,
                            'read'
                        FROM release_updates ru
                        JOIN artist_follows af
                            ON af.artist_id = ru.artist_id
                           AND af.user_id = :user_id
                        LEFT JOIN update_notifications un
                            ON un.user_id = :user_id
                           AND un.release_id = ru.release_id
                        WHERE un.id IS NULL
                        """
                    ),
                    {"user_id": int(user_id)},
                )

                result = session.execute(
                    text(
                        """
                        UPDATE update_notifications
                        SET opened_at = CURRENT_TIMESTAMP,
                            action_taken = 'read'
                        WHERE user_id = :user_id
                          AND opened_at IS NULL
                        """
                    ),
                    {"user_id": int(user_id)},
                )

            return int(result.rowcount or 0)
        except Exception as exc:
            logger.error("Error marking all notifications read: %s", exc)
            return 0

    def export_followed_artists(self, user_id: int) -> list[dict[str, Any]]:
        artists = self.get_followed_artists(user_id=user_id, limit=10000, offset=0)
        return [
            {
                "artist_id": artist["id"],
                "artist_name": artist["name"],
                "follow_level": artist["followLevel"],
                "auto_download": artist["autoDownload"],
                "preferred_quality": artist["preferredQuality"],
                "follow_date": artist["followDate"],
            }
            for artist in artists
        ]


update_tracker = AutoUpdateTracker()

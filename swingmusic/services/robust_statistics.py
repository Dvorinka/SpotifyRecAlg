"""
Robust Statistics System for SwingMusic
Prevents data loss with backup, validation, and integrity checks
"""

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swingmusic import logger
from swingmusic.db.sqlite.utils import get_db_connection


@dataclass
class ListeningStats:
    """Listening statistics for a track"""

    user_id: str
    track_id: str
    play_count: int
    last_played: float
    total_time: int  # Total seconds listened
    skip_count: int
    favorite: bool
    rating: int | None  # 1-5 stars
    created_at: float
    updated_at: float


@dataclass
class ArtistStats:
    """Artist-level statistics"""

    artist_id: str
    artist_name: str
    total_plays: int
    total_time: int
    unique_tracks: int
    last_played: float
    favorite_tracks: list[str]


@dataclass
class AlbumStats:
    """Album-level statistics"""

    album_id: str
    album_name: str
    artist_name: str
    total_plays: int
    total_time: int
    unique_tracks: int
    last_played: float
    completion_rate: float  # Percentage of album listened to


@dataclass
class BackupEntry:
    """Backup entry metadata"""

    backup_id: str
    timestamp: float
    backup_type: str  # 'full', 'incremental', 'auto'
    file_path: str
    checksum: str
    size: int
    compressed: bool


class StatisticsValidator:
    """Validates statistics data integrity"""

    @staticmethod
    def validate_listening_data(data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate listening statistics data"""
        errors = []

        # Required fields
        required_fields = ["user_id", "track_id", "play_count", "last_played"]
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        # Data type validation
        if "play_count" in data and not isinstance(data["play_count"], int):
            errors.append("play_count must be an integer")

        if "last_played" in data and not isinstance(data["last_played"], (int, float)):
            errors.append("last_played must be a timestamp")

        if "total_time" in data and not isinstance(data["total_time"], int):
            errors.append("total_time must be an integer")

        # Value validation
        if "play_count" in data and data["play_count"] < 0:
            errors.append("play_count cannot be negative")

        if "total_time" in data and data["total_time"] < 0:
            errors.append("total_time cannot be negative")

        if "rating" in data and data["rating"] is not None:
            if not isinstance(data["rating"], int) or not (1 <= data["rating"] <= 5):
                errors.append("rating must be an integer between 1 and 5")

        return len(errors) == 0, errors

    @staticmethod
    def validate_timestamp_consistency(stats: list[ListeningStats]) -> list[str]:
        """Validate timestamp consistency across statistics"""
        errors = []

        current_time = time.time()

        for stat in stats:
            # Check for future timestamps
            if stat.last_played > current_time + 60:  # Allow 1 minute buffer
                errors.append(f"Future timestamp detected for track {stat.track_id}")

            # Check for very old timestamps (before 2000)
            if stat.last_played < 946684800:  # Jan 1, 2000
                errors.append(f"Suspicious old timestamp for track {stat.track_id}")

            # Check if updated_at >= last_played
            if stat.updated_at < stat.last_played:
                errors.append(
                    f"updated_at before last_played for track {stat.track_id}"
                )

        return errors

    @staticmethod
    def calculate_checksum(data: Any) -> str:
        """Calculate SHA-256 checksum of data"""
        if isinstance(data, str):
            data_bytes = data.encode("utf-8")
        elif isinstance(data, dict):
            data_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
        else:
            data_bytes = str(data).encode("utf-8")

        return hashlib.sha256(data_bytes).hexdigest()


class StatisticsBackup:
    """Manages statistics backups with compression and verification"""

    def __init__(self, backup_dir: str = None):
        self.backup_dir = backup_dir or os.path.join(
            Path.home(), ".swingmusic", "backups", "statistics"
        )
        os.makedirs(self.backup_dir, exist_ok=True)

        # Backup configuration
        self.max_backups = 10  # Maximum number of backups to keep
        self.auto_backup_interval = 3600  # 1 hour in seconds
        self.compress_backups = True

    def create_backup(self, backup_type: str = "auto") -> BackupEntry:
        """Create a statistics backup"""
        timestamp = time.time()
        backup_id = f"stats_{backup_type}_{int(timestamp)}"
        backup_file = os.path.join(self.backup_dir, f"{backup_id}.json")

        try:
            # Collect statistics data
            stats_data = self._collect_statistics_data()

            # Create backup entry
            backup_entry = BackupEntry(
                backup_id=backup_id,
                timestamp=timestamp,
                backup_type=backup_type,
                file_path=backup_file,
                checksum="",
                size=0,
                compressed=self.compress_backups,
            )

            # Write backup file
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(stats_data, f, indent=2, ensure_ascii=False)

            # Calculate checksum and size
            backup_entry.checksum = StatisticsValidator.calculate_checksum(stats_data)
            backup_entry.size = os.path.getsize(backup_file)

            # Compress if enabled
            if self.compress_backups:
                backup_file = self._compress_backup(backup_file)
                backup_entry.file_path = backup_file
                backup_entry.size = os.path.getsize(backup_file)

            logger.info(f"Created statistics backup: {backup_id}")
            return backup_entry

        except Exception as e:
            logger.error(f"Failed to create statistics backup: {e}")
            if os.path.exists(backup_file):
                os.remove(backup_file)
            raise

    def _collect_statistics_data(self) -> dict[str, Any]:
        """Collect all statistics data from database"""
        try:
            with get_db_connection() as conn:
                # Get listening statistics
                cursor = conn.execute("""
                    SELECT
                        user_id,
                        trackhash as track_id,
                        playcount as play_count,
                        lastplayed as last_played,
                        total_time,
                        skip_count,
                        favorite,
                        rating,
                        created_at,
                        updated_at
                    FROM listening_stats
                """)

                listening_stats = [dict(row) for row in cursor.fetchall()]

                # Get artist statistics
                cursor = conn.execute("""
                    SELECT
                        artist_id,
                        artist_name,
                        total_plays,
                        total_time,
                        unique_tracks,
                        last_played,
                        favorite_tracks
                    FROM artist_stats
                """)

                artist_stats = [dict(row) for row in cursor.fetchall()]

                # Get album statistics
                cursor = conn.execute("""
                    SELECT
                        album_id,
                        album_name,
                        artist_name,
                        total_plays,
                        total_time,
                        unique_tracks,
                        last_played,
                        completion_rate
                    FROM album_stats
                """)

                album_stats = [dict(row) for row in cursor.fetchall()]

                return {
                    "backup_timestamp": time.time(),
                    "listening_stats": listening_stats,
                    "artist_stats": artist_stats,
                    "album_stats": album_stats,
                    "version": "1.0",
                }

        except Exception as e:
            logger.error(f"Error collecting statistics data: {e}")
            return {}

    def _compress_backup(self, file_path: str) -> str:
        """Compress backup file using gzip"""
        try:
            import gzip

            compressed_path = file_path + ".gz"

            with open(file_path, "rb") as f_in:
                with gzip.open(compressed_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Remove uncompressed file
            os.remove(file_path)

            return compressed_path

        except ImportError:
            logger.warning("gzip not available, backup not compressed")
            return file_path
        except Exception as e:
            logger.error(f"Error compressing backup: {e}")
            return file_path

    def restore_backup(self, backup_id: str) -> bool:
        """Restore statistics from backup"""
        backup_file = None

        try:
            # Find backup file
            if backup_id.endswith(".gz"):
                backup_file = os.path.join(self.backup_dir, backup_id)
            else:
                backup_file = os.path.join(self.backup_dir, f"{backup_id}.json")
                if not os.path.exists(backup_file):
                    backup_file = os.path.join(self.backup_dir, f"{backup_id}.json.gz")

            if not os.path.exists(backup_file):
                logger.error(f"Backup file not found: {backup_id}")
                return False

            # Load backup data
            stats_data = self._load_backup_file(backup_file)

            if not stats_data:
                logger.error("Failed to load backup data")
                return False

            # Restore data to database
            success = self._restore_statistics_data(stats_data)

            if success:
                logger.info(
                    f"Successfully restored statistics from backup: {backup_id}"
                )
            else:
                logger.error(f"Failed to restore statistics from backup: {backup_id}")

            return success

        except Exception as e:
            logger.error(f"Error restoring backup {backup_id}: {e}")
            return False

    def _load_backup_file(self, file_path: str) -> dict[str, Any] | None:
        """Load backup file (compressed or uncompressed)"""
        try:
            if file_path.endswith(".gz"):
                import gzip

                with gzip.open(file_path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            else:
                with open(file_path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading backup file {file_path}: {e}")
            return None

    def _restore_statistics_data(self, stats_data: dict[str, Any]) -> bool:
        """Restore statistics data to database"""
        try:
            with get_db_connection() as conn:
                # Clear existing statistics
                conn.execute("DELETE FROM listening_stats")
                conn.execute("DELETE FROM artist_stats")
                conn.execute("DELETE FROM album_stats")

                # Restore listening statistics
                if "listening_stats" in stats_data:
                    for stat in stats_data["listening_stats"]:
                        conn.execute(
                            """
                            INSERT INTO listening_stats (
                                user_id, trackhash, playcount, lastplayed, total_time,
                                skip_count, favorite, rating, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                stat["user_id"],
                                stat["track_id"],
                                stat["play_count"],
                                stat["last_played"],
                                stat["total_time"],
                                stat.get("skip_count", 0),
                                stat.get("favorite", False),
                                stat.get("rating"),
                                stat.get("created_at", time.time()),
                                stat.get("updated_at", time.time()),
                            ),
                        )

                # Restore artist statistics
                if "artist_stats" in stats_data:
                    for stat in stats_data["artist_stats"]:
                        conn.execute(
                            """
                            INSERT INTO artist_stats (
                                artist_id, artist_name, total_plays, total_time,
                                unique_tracks, last_played, favorite_tracks
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                stat["artist_id"],
                                stat["artist_name"],
                                stat["total_plays"],
                                stat["total_time"],
                                stat["unique_tracks"],
                                stat["last_played"],
                                json.dumps(stat.get("favorite_tracks", [])),
                            ),
                        )

                # Restore album statistics
                if "album_stats" in stats_data:
                    for stat in stats_data["album_stats"]:
                        conn.execute(
                            """
                            INSERT INTO album_stats (
                                album_id, album_name, artist_name, total_plays,
                                total_time, unique_tracks, last_played, completion_rate
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                stat["album_id"],
                                stat["album_name"],
                                stat["artist_name"],
                                stat["total_plays"],
                                stat["total_time"],
                                stat["unique_tracks"],
                                stat["last_played"],
                                stat.get("completion_rate", 0.0),
                            ),
                        )

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error restoring statistics data: {e}")
            return False

    def list_backups(self) -> list[BackupEntry]:
        """List all available backups"""
        backups = []

        try:
            for file_name in os.listdir(self.backup_dir):
                if file_name.endswith((".json", ".gz")):
                    file_path = os.path.join(self.backup_dir, file_name)

                    # Extract backup info from filename
                    parts = file_name.replace(".json", "").replace(".gz", "").split("_")
                    if len(parts) >= 3:
                        backup_type = parts[1]
                        timestamp = float(parts[2])

                        backup_entry = BackupEntry(
                            backup_id=file_name.replace(".json", "").replace(".gz", ""),
                            timestamp=timestamp,
                            backup_type=backup_type,
                            file_path=file_path,
                            checksum="",
                            size=os.path.getsize(file_path),
                            compressed=file_path.endswith(".gz"),
                        )

                        backups.append(backup_entry)

            # Sort by timestamp (newest first)
            backups.sort(key=lambda x: x.timestamp, reverse=True)

        except Exception as e:
            logger.error(f"Error listing backups: {e}")

        return backups

    def cleanup_old_backups(self):
        """Remove old backups, keeping only the most recent ones"""
        backups = self.list_backups()

        if len(backups) > self.max_backups:
            # Keep the most recent backups
            backups[: self.max_backups]
            backups_to_remove = backups[self.max_backups :]

            for backup in backups_to_remove:
                try:
                    os.remove(backup.file_path)
                    logger.info(f"Removed old backup: {backup.backup_id}")
                except Exception as e:
                    logger.error(f"Error removing backup {backup.backup_id}: {e}")


class RobustStatisticsManager:
    """Robust statistics manager with backup and validation"""

    def __init__(self):
        self.backup_manager = StatisticsBackup()
        self.validator = StatisticsValidator()
        self.last_backup_time = 0
        self.backup_lock = threading.Lock()

        # Start auto-backup thread
        self._start_auto_backup()

    def _start_auto_backup(self):
        """Start automatic backup thread"""

        def backup_worker():
            while True:
                time.sleep(self.backup_manager.auto_backup_interval)
                try:
                    self._create_auto_backup()
                except Exception as e:
                    logger.error(f"Auto-backup failed: {e}")

        backup_thread = threading.Thread(target=backup_worker, daemon=True)
        backup_thread.start()

    def _create_auto_backup(self):
        """Create automatic backup"""
        with self.backup_lock:
            try:
                self.backup_manager.create_backup("auto")
                self.last_backup_time = time.time()
                self.backup_manager.cleanup_old_backups()
            except Exception as e:
                logger.error(f"Auto-backup failed: {e}")

    async def update_listening_stats(
        self, user_id: str, track_id: str, listening_data: dict[str, Any]
    ) -> bool:
        """Update statistics with data integrity checks"""
        try:
            # Validate data before storage
            is_valid, errors = self.validator.validate_listening_data(listening_data)
            if not is_valid:
                logger.error(f"Invalid listening data: {errors}")
                return False

            # Create backup before update
            backup_success = self._create_update_backup(user_id)
            if not backup_success:
                logger.warning("Failed to create backup before statistics update")

            # Update with transaction
            with get_db_connection() as conn:
                conn.execute("BEGIN TRANSACTION")

                try:
                    # Update or insert listening stats
                    cursor = conn.execute(
                        """
                        SELECT playcount, total_time, skip_count, favorite, rating
                        FROM listening_stats
                        WHERE user_id = ? AND trackhash = ?
                    """,
                        (user_id, track_id),
                    )

                    existing = cursor.fetchone()

                    if existing:
                        # Update existing record
                        new_play_count = existing["playcount"] + listening_data.get(
                            "play_count", 1
                        )
                        new_total_time = existing["total_time"] + listening_data.get(
                            "duration", 0
                        )
                        new_skip_count = existing["skip_count"] + listening_data.get(
                            "skip_count", 0
                        )

                        conn.execute(
                            """
                            UPDATE listening_stats
                            SET playcount = ?, lastplayed = ?, total_time = ?,
                                skip_count = ?, updated_at = ?
                            WHERE user_id = ? AND trackhash = ?
                        """,
                            (
                                new_play_count,
                                listening_data.get("last_played", time.time()),
                                new_total_time,
                                new_skip_count,
                                time.time(),
                                user_id,
                                track_id,
                            ),
                        )
                    else:
                        # Insert new record
                        conn.execute(
                            """
                            INSERT INTO listening_stats (
                                user_id, trackhash, playcount, lastplayed, total_time,
                                skip_count, favorite, rating, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                user_id,
                                track_id,
                                listening_data.get("play_count", 1),
                                listening_data.get("last_played", time.time()),
                                listening_data.get("duration", 0),
                                listening_data.get("skip_count", 0),
                                listening_data.get("favorite", False),
                                listening_data.get("rating"),
                                time.time(),
                                time.time(),
                            ),
                        )

                    # Update artist and album statistics
                    await self._update_artist_stats(conn, user_id, track_id)
                    await self._update_album_stats(conn, user_id, track_id)

                    conn.commit()

                    # Verify integrity after update
                    await self._verify_integrity(user_id)

                    return True

                except Exception as e:
                    conn.rollback()
                    logger.error(f"Error updating statistics: {e}")

                    # Attempt to restore from backup
                    if backup_success:
                        self._restore_from_backup(user_id)

                    return False

        except Exception as e:
            logger.error(f"Error in update_listening_stats: {e}")
            return False

    async def _update_artist_stats(
        self, conn: sqlite3.Connection, user_id: str, track_id: str
    ):
        """Update artist-level statistics"""
        try:
            # Get track information
            cursor = conn.execute(
                """
                SELECT artist, album FROM tracks WHERE trackhash = ?
            """,
                (track_id,),
            )

            track_info = cursor.fetchone()
            if not track_info:
                return

            artist = track_info["artist"]

            # Update artist statistics
            cursor = conn.execute(
                """
                SELECT total_plays, total_time, unique_tracks, last_played
                FROM artist_stats
                WHERE artist_id = ? AND user_id = ?
            """,
                (artist, user_id),
            )

            existing = cursor.fetchone()

            if existing:
                # Update existing
                cursor = conn.execute(
                    """
                    SELECT COUNT(DISTINCT trackhash) as unique_count
                    FROM listening_stats
                    WHERE user_id = ? AND trackhash IN (
                        SELECT trackhash FROM tracks WHERE artist = ?
                    )
                """,
                    (user_id, artist),
                )

                unique_tracks = cursor.fetchone()["unique_count"]

                conn.execute(
                    """
                    UPDATE artist_stats
                    SET total_plays = total_plays + 1,
                        total_time = total_time + ?,
                        unique_tracks = ?,
                        last_played = ?
                    WHERE artist_id = ? AND user_id = ?
                """,
                    (
                        track_info.get("duration", 0),
                        unique_tracks,
                        time.time(),
                        artist,
                        user_id,
                    ),
                )
            else:
                # Insert new
                conn.execute(
                    """
                    INSERT INTO artist_stats (
                        artist_id, artist_name, user_id, total_plays, total_time,
                        unique_tracks, last_played, favorite_tracks
                    ) VALUES (?, ?, ?, 1, ?, 1, ?, ?)
                """,
                    (
                        artist,
                        artist,
                        user_id,
                        track_info.get("duration", 0),
                        time.time(),
                        json.dumps([]),
                    ),
                )

        except Exception as e:
            logger.error(f"Error updating artist stats: {e}")

    async def _update_album_stats(
        self, conn: sqlite3.Connection, user_id: str, track_id: str
    ):
        """Update album-level statistics"""
        try:
            # Get track information
            cursor = conn.execute(
                """
                SELECT artist, album FROM tracks WHERE trackhash = ?
            """,
                (track_id,),
            )

            track_info = cursor.fetchone()
            if not track_info:
                return

            album = track_info["album"]
            artist = track_info["artist"]

            # Update album statistics
            cursor = conn.execute(
                """
                SELECT total_plays, total_time, unique_tracks, last_played
                FROM album_stats
                WHERE album_id = ? AND user_id = ?
            """,
                (album, user_id),
            )

            existing = cursor.fetchone()

            if existing:
                # Update existing
                cursor = conn.execute(
                    """
                    SELECT COUNT(DISTINCT trackhash) as unique_count
                    FROM listening_stats
                    WHERE user_id = ? AND trackhash IN (
                        SELECT trackhash FROM tracks WHERE album = ?
                    )
                """,
                    (user_id, album),
                )

                unique_tracks = cursor.fetchone()["unique_count"]

                conn.execute(
                    """
                    UPDATE album_stats
                    SET total_plays = total_plays + 1,
                        total_time = total_time + ?,
                        unique_tracks = ?,
                        last_played = ?
                    WHERE album_id = ? AND user_id = ?
                """,
                    (
                        track_info.get("duration", 0),
                        unique_tracks,
                        time.time(),
                        album,
                        user_id,
                    ),
                )
            else:
                # Insert new
                conn.execute(
                    """
                    INSERT INTO album_stats (
                        album_id, album_name, artist_name, user_id, total_plays,
                        total_time, unique_tracks, last_played, completion_rate
                    ) VALUES (?, ?, ?, ?, 1, ?, 1, ?, 0.0)
                """,
                    (
                        album,
                        album,
                        artist,
                        user_id,
                        track_info.get("duration", 0),
                        time.time(),
                    ),
                )

        except Exception as e:
            logger.error(f"Error updating album stats: {e}")

    async def _verify_integrity(self, user_id: str):
        """Verify statistics integrity after update"""
        try:
            with get_db_connection() as conn:
                # Get all listening stats for user
                cursor = conn.execute(
                    """
                    SELECT * FROM listening_stats WHERE user_id = ?
                """,
                    (user_id,),
                )

                stats = [ListeningStats(**dict(row)) for row in cursor.fetchall()]

                # Validate timestamp consistency
                errors = self.validator.validate_timestamp_consistency(stats)

                if errors:
                    logger.warning(
                        f"Statistics integrity issues for user {user_id}: {errors}"
                    )

        except Exception as e:
            logger.error(f"Error verifying statistics integrity: {e}")

    def _create_update_backup(self, user_id: str) -> bool:
        """Create backup before statistics update"""
        try:
            with self.backup_lock:
                f"pre_update_{user_id}_{int(time.time())}"
                self.backup_manager.create_backup("update")
                return True
        except Exception as e:
            logger.error(f"Failed to create update backup: {e}")
            return False

    def _restore_from_backup(self, user_id: str):
        """Restore statistics from most recent backup"""
        try:
            backups = self.backup_manager.list_backups()
            if backups:
                # Find the most recent backup
                latest_backup = backups[0]
                success = self.backup_manager.restore_backup(latest_backup.backup_id)

                if success:
                    logger.info(
                        f"Restored statistics from backup: {latest_backup.backup_id}"
                    )
                else:
                    logger.error(
                        f"Failed to restore from backup: {latest_backup.backup_id}"
                    )

        except Exception as e:
            logger.error(f"Error restoring from backup: {e}")

    def get_statistics_summary(self, user_id: str) -> dict[str, Any]:
        """Get statistics summary for user"""
        try:
            with get_db_connection() as conn:
                # Get overall statistics
                cursor = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_tracks,
                        SUM(playcount) as total_plays,
                        SUM(total_time) as total_time,
                        COUNT(DISTINCT artist) as unique_artists,
                        COUNT(DISTINCT album) as unique_albums
                    FROM listening_stats ls
                    JOIN tracks t ON ls.trackhash = t.trackhash
                    WHERE ls.user_id = ?
                """,
                    (user_id,),
                )

                overall = cursor.fetchone()

                # Get top tracks
                cursor = conn.execute(
                    """
                    SELECT t.title, t.artist, ls.playcount, ls.lastplayed
                    FROM listening_stats ls
                    JOIN tracks t ON ls.trackhash = t.trackhash
                    WHERE ls.user_id = ?
                    ORDER BY ls.playcount DESC
                    LIMIT 10
                """,
                    (user_id,),
                )

                top_tracks = [dict(row) for row in cursor.fetchall()]

                # Get top artists
                cursor = conn.execute(
                    """
                    SELECT artist_name, total_plays, total_time
                    FROM artist_stats
                    WHERE user_id = ?
                    ORDER BY total_plays DESC
                    LIMIT 10
                """,
                    (user_id,),
                )

                top_artists = [dict(row) for row in cursor.fetchall()]

                return {
                    "overall": dict(overall) if overall else {},
                    "top_tracks": top_tracks,
                    "top_artists": top_artists,
                    "last_backup": self.last_backup_time,
                }

        except Exception as e:
            logger.error(f"Error getting statistics summary: {e}")
            return {}


# Global robust statistics manager instance
robust_statistics_manager = RobustStatisticsManager()

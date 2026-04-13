"""
Library integration service for Spotify downloads
Handles automatic addition of downloaded tracks to SwingMusic library
"""

import hashlib
import logging
import os
from datetime import datetime
from typing import Any

from swingmusic.config import UserConfig
from swingmusic.db.engine import DbEngine
from swingmusic.db.libdata import TrackTable
from swingmusic.services.cache_invalidation import (
    on_track_deleted,
    on_track_inserted,
    on_track_updated,
)

logger = logging.getLogger(__name__)


class LibraryIntegrator:
    """Handles integration of downloaded tracks into SwingMusic library"""

    def __init__(self):
        self.config = UserConfig()
        self.music_dirs = self.config.rootDirs

    def add_downloaded_track(self, download_item: dict[str, Any]) -> bool:
        """
        Add a downloaded track to the SwingMusic library

        Args:
            download_item: Dictionary containing download information

        Returns:
            bool: True if successfully added, False otherwise
        """
        try:
            if not download_item.get("file_path") or not os.path.exists(
                download_item["file_path"]
            ):
                logger.error(
                    f"Downloaded file not found: {download_item.get('file_path')}"
                )
                return False

            # Check if track already exists in library
            if self._track_exists(download_item["file_path"]):
                logger.info(
                    f"Track already exists in library: {download_item['file_path']}"
                )
                return True

            # Create track record
            track_data = self._create_track_data(download_item)

            # Insert into database
            self._insert_track(track_data)

            logger.info(
                f"Added track to library: {track_data['title']} by {track_data['artists']}"
            )
            return True

        except Exception as e:
            logger.error(f"Error adding track to library: {e}")
            return False

    def add_downloaded_album(
        self, download_item: dict[str, Any], track_files: list[str]
    ) -> int:
        """
        Add all tracks from a downloaded album to the library

        Args:
            download_item: Album download information
            track_files: List of downloaded track file paths

        Returns:
            int: Number of tracks successfully added
        """
        added_count = 0

        try:
            for track_file in track_files:
                if not os.path.exists(track_file):
                    logger.warning(f"Track file not found: {track_file}")
                    continue

                # Check if track already exists
                if self._track_exists(track_file):
                    logger.info(f"Track already exists in library: {track_file}")
                    added_count += 1
                    continue

                # Create track data for album track
                track_data = self._create_album_track_data(download_item, track_file)

                # Insert into database
                self._insert_track(track_data)
                added_count += 1

            logger.info(f"Added {added_count} tracks from album to library")
            return added_count

        except Exception as e:
            logger.error(f"Error adding album to library: {e}")
            return added_count

    def _track_exists(self, filepath: str) -> bool:
        """Check if track already exists in library"""
        try:
            with DbEngine.manager() as conn:
                result = conn.execute(
                    TrackTable.select().where(TrackTable.filepath == filepath)
                )
                return result.scalar() is not None
        except Exception as e:
            logger.error(f"Error checking if track exists: {e}")
            return False

    def _create_track_data(self, download_item: dict[str, Any]) -> dict[str, Any]:
        """Create track data dictionary from download item"""
        filepath = download_item["file_path"]
        file_stat = os.stat(filepath)

        # Extract metadata from download item
        title = download_item.get("title", "Unknown Title")
        artist = download_item.get("artist", "Unknown Artist")
        album = download_item.get("album", "Unknown Album")

        # Generate hashes
        trackhash = self._generate_track_hash(filepath, title, artist)
        albumhash = self._generate_album_hash(album, artist)

        # Extract file information
        folder = os.path.basename(os.path.dirname(filepath))

        return {
            "title": title,
            "artists": artist,
            "albumartists": artist,
            "album": album,
            "albumhash": albumhash,
            "trackhash": trackhash,
            "filepath": filepath,
            "folder": folder,
            "duration": download_item.get("duration_ms", 0)
            // 1000,  # Convert to seconds
            "bitrate": self._get_bitrate_from_quality(
                download_item.get("quality", "flac")
            ),
            "date": self._parse_date(download_item.get("release_date")),
            "track": download_item.get("track_number", 1),
            "disc": 1,
            "last_mod": int(file_stat.st_mtime),
            "extra": {
                "spotify_id": download_item.get("spotify_id"),
                "source": download_item.get("source", "spotify"),
                "download_date": datetime.now().isoformat(),
            },
        }

    def _create_album_track_data(
        self, download_item: dict[str, Any], track_file: str
    ) -> dict[str, Any]:
        """Create track data for album track"""
        file_stat = os.stat(track_file)

        # Extract filename for title (if metadata not available)
        filename = os.path.splitext(os.path.basename(track_file))[0]

        # Use download item metadata as base
        title = download_item.get("title", filename)
        artist = download_item.get("artist", "Unknown Artist")
        album = download_item.get("album", "Unknown Album")

        # Generate hashes
        trackhash = self._generate_track_hash(track_file, title, artist)
        albumhash = self._generate_album_hash(album, artist)

        # Extract file information
        folder = os.path.basename(os.path.dirname(track_file))

        return {
            "title": title,
            "artists": artist,
            "albumartists": artist,
            "album": album,
            "albumhash": albumhash,
            "trackhash": trackhash,
            "filepath": track_file,
            "folder": folder,
            "duration": download_item.get("duration_ms", 0) // 1000,
            "bitrate": self._get_bitrate_from_quality(
                download_item.get("quality", "flac")
            ),
            "date": self._parse_date(download_item.get("release_date")),
            "track": download_item.get("track_number", 1),
            "disc": 1,
            "last_mod": int(file_stat.st_mtime),
            "extra": {
                "spotify_id": download_item.get("spotify_id"),
                "source": download_item.get("source", "spotify"),
                "download_date": datetime.now().isoformat(),
                "album_download": True,
            },
        }

    def _insert_track(self, track_data: dict[str, Any]):
        """Insert track into database"""
        try:
            with DbEngine.manager(commit=True) as conn:
                conn.execute(TrackTable.insert().values(track_data))

            # Invalidate cache for the new track
            trackhash = track_data.get("trackhash")
            if trackhash:
                on_track_inserted(trackhash)
        except Exception as e:
            logger.error(f"Error inserting track: {e}")
            raise

    def _generate_track_hash(self, filepath: str, title: str, artist: str) -> str:
        """Generate unique track hash"""
        content = f"{filepath}:{title}:{artist}"
        return hashlib.md5(content.encode()).hexdigest()

    def _generate_album_hash(self, album: str, artist: str) -> str:
        """Generate album hash"""
        content = f"{album}:{artist}"
        return hashlib.md5(content.encode()).hexdigest()

    def _get_bitrate_from_quality(self, quality: str) -> int:
        """Get approximate bitrate based on quality"""
        quality_bitrates = {
            "flac": 1411,  # Approximate FLAC bitrate
            "mp3_320": 320,
            "mp3_128": 128,
        }
        return quality_bitrates.get(quality, 320)

    def _parse_date(self, date_str: str | None) -> int | None:
        """Parse date string to timestamp"""
        if not date_str:
            return None

        try:
            # Try various date formats
            formats = ["%Y-%m-%d", "%Y", "%Y-%m"]
            for fmt in formats:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    return int(dt.timestamp())
                except ValueError:
                    continue

            return None
        except Exception:
            return None

    def remove_downloaded_track(self, filepath: str) -> bool:
        """
        Remove a downloaded track from the library

        Args:
            filepath: Path to the track file

        Returns:
            bool: True if successfully removed
        """
        try:
            # Get trackhash before deletion for cache invalidation
            trackhash = None
            with DbEngine.manager() as conn:
                result = conn.execute(
                    TrackTable.select().where(TrackTable.filepath == filepath)
                )
                row = result.scalar()
                if row:
                    trackhash = row.trackhash

            with DbEngine.manager(commit=True) as conn:
                result = conn.execute(
                    TrackTable.delete().where(TrackTable.filepath == filepath)
                )
                success = result.rowcount > 0

            # Invalidate cache after deletion
            if success and trackhash:
                on_track_deleted(trackhash)

            return success
        except Exception as e:
            logger.error(f"Error removing track from library: {e}")
            return False

    def update_track_metadata(self, filepath: str, metadata: dict[str, Any]) -> bool:
        """
        Update metadata for a track in the library

        Args:
            filepath: Path to the track file
            metadata: New metadata to apply

        Returns:
            bool: True if successfully updated
        """
        try:
            # Get trackhash before update for cache invalidation
            trackhash = None
            with DbEngine.manager() as conn:
                result = conn.execute(
                    TrackTable.select().where(TrackTable.filepath == filepath)
                )
                row = result.scalar()
                if row:
                    trackhash = row.trackhash

            with DbEngine.manager(commit=True) as conn:
                result = conn.execute(
                    TrackTable.update()
                    .where(TrackTable.filepath == filepath)
                    .values(metadata)
                )
                success = result.rowcount > 0

            # Invalidate cache after update
            if success and trackhash:
                on_track_updated(trackhash)

            return success
        except Exception as e:
            logger.error(f"Error updating track metadata: {e}")
            return False


# Global instance
library_integrator = LibraryIntegrator()

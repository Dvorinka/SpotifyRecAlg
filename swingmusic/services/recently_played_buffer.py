"""
Recently Played Buffer using DragonflyDB.

Provides instant access to recently played tracks using a fast circular buffer
stored in DragonflyDB. This eliminates the need for database queries for the
most common "recently played" use case.
"""

import json
import logging
import time
from typing import Any

from swingmusic.db.dragonfly_client import get_dragonfly_client

logger = logging.getLogger(__name__)

# Maximum number of tracks to keep in the recently played buffer
MAX_BUFFER_SIZE = 100

# TTL for recently played entries (30 days)
BUFFER_TTL = 30 * 24 * 60 * 60


class RecentlyPlayedBuffer:
    """
    Manages recently played tracks using DragonflyDB lists.

    Uses a circular buffer pattern with Redis lists (LPUSH + LTRIM)
    to maintain a fixed-size buffer of recently played tracks per user.
    """

    def __init__(self, max_size: int = MAX_BUFFER_SIZE):
        self.max_size = max_size
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_dragonfly_client()
        return self._client

    def _get_key(self, userid: int) -> str:
        """Get the Redis key for a user's recently played buffer."""
        return f"recently_played:user:{userid}"

    def add_track(self, userid: int, track_data: dict[str, Any]) -> bool:
        """
        Add a track to the user's recently played buffer.

        Args:
            userid: The user ID
            track_data: Track metadata including trackhash, title, artist, etc.

        Returns:
            True if successful, False otherwise
        """
        if not self.client.is_available():
            return False

        try:
            key = self._get_key(userid)

            # Add timestamp to track data
            entry = {
                **track_data,
                "played_at": int(time.time()),
            }

            # Use pipeline for atomic operations
            pipe = self.client.client.pipeline()

            # Push to front of list
            pipe.lpush(key, json.dumps(entry))

            # Trim to max size (keep only first max_size elements)
            pipe.ltrim(key, 0, self.max_size - 1)

            # Set TTL
            pipe.expire(key, BUFFER_TTL)

            pipe.execute()

            logger.debug(f"Added track to recently played for user {userid}")
            return True

        except Exception as e:
            logger.error(f"Failed to add track to recently played buffer: {e}")
            return False

    def get_recent_tracks(
        self, userid: int, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        Get recently played tracks for a user.

        Args:
            userid: The user ID
            limit: Maximum number of tracks to return
            offset: Number of tracks to skip

        Returns:
            List of track data dictionaries, most recent first
        """
        if not self.client.is_available():
            return []

        try:
            key = self._get_key(userid)

            # Get range from list (LRANGE is 0-indexed, inclusive)
            end = offset + limit - 1
            results = self.client.client.lrange(key, offset, end)

            tracks = []
            for result in results:
                try:
                    tracks.append(json.loads(result))
                except json.JSONDecodeError:
                    continue

            return tracks

        except Exception as e:
            logger.error(f"Failed to get recently played tracks: {e}")
            return []

    def get_track_count(self, userid: int) -> int:
        """Get the number of tracks in the user's recently played buffer."""
        if not self.client.is_available():
            return 0

        try:
            key = self._get_key(userid)
            return self.client.client.llen(key)
        except Exception:
            return 0

    def clear_buffer(self, userid: int) -> bool:
        """Clear the recently played buffer for a user."""
        if not self.client.is_available():
            return False

        try:
            key = self._get_key(userid)
            self.client.client.delete(key)
            return True
        except Exception:
            return False

    def remove_track(self, userid: int, trackhash: str) -> bool:
        """
        Remove a specific track from the buffer.

        Note: This requires reading, filtering, and rewriting the list,
        so it's more expensive than other operations.
        """
        if not self.client.is_available():
            return False

        try:
            key = self._get_key(userid)

            # Get all tracks
            all_tracks = self.client.client.lrange(key, 0, -1)

            # Filter out the track to remove
            filtered = []
            for track_json in all_tracks:
                track = json.loads(track_json)
                if track.get("trackhash") != trackhash:
                    filtered.append(track_json)

            # Delete and rewrite if changed
            if len(filtered) != len(all_tracks):
                pipe = self.client.client.pipeline()
                pipe.delete(key)
                if filtered:
                    pipe.rpush(key, *filtered)
                    pipe.expire(key, BUFFER_TTL)
                pipe.execute()

            return True

        except Exception as e:
            logger.error(f"Failed to remove track from buffer: {e}")
            return False

    def get_last_played_track(self, userid: int) -> dict[str, Any] | None:
        """Get the most recently played track for a user."""
        tracks = self.get_recent_tracks(userid, limit=1)
        return tracks[0] if tracks else None

    def is_track_recently_played(
        self, userid: int, trackhash: str, within_seconds: int = 3600
    ) -> bool:
        """
        Check if a track was played recently (within the specified time).

        Useful for preventing duplicate "recently played" entries.
        """
        tracks = self.get_recent_tracks(userid, limit=10)
        now = int(time.time())

        for track in tracks:
            if track.get("trackhash") == trackhash:
                played_at = track.get("played_at", 0)
                if now - played_at < within_seconds:
                    return True

        return False


# Global instance
recently_played_buffer = RecentlyPlayedBuffer()


def get_recently_played_buffer() -> RecentlyPlayedBuffer:
    """Get the global recently played buffer instance."""
    return recently_played_buffer

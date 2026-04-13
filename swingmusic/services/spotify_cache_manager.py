"""
Spotify Cache Manager with DragonflyDB Integration

Provides intelligent caching for Spotify metadata to:
- Rate limit requests (protect against bans)
- Cache data for 12 hours
- Use DragonflyDB for fast caching
- Fall back to local SQLite if Dragonfly unavailable
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Import native DragonflyDB service
from swingmusic.db.dragonfly_client import get_spotify_cache

logger = logging.getLogger(__name__)


class SpotifyCacheManager:
    """
    Intelligent cache manager for Spotify metadata with DragonflyDB support
    """

    def __init__(self, cache_duration_hours: int = 12):
        self.cache_duration = timedelta(hours=cache_duration_hours)

        # Use native DragonflyDB service
        self.dragonfly_cache = get_spotify_cache()

        # Initialize SQLite as fallback
        self.sqlite_conn = None
        self._init_sqlite_fallback()

        # Rate limiting (only for real Spotify API calls)
        self.min_request_interval = 2.0  # 2 seconds between requests
        self.last_request_time = 0
        self.request_count = 0
        self.max_requests_per_hour = 1000  # Conservative limit

        logger.info(
            f"Spotify cache manager initialized (cache: {cache_duration_hours}h, dragonfly: {self.dragonfly_cache.client.is_available()})"
        )

    def _init_sqlite_fallback(self):
        """Initialize SQLite fallback cache"""
        try:
            cache_dir = Path.home() / ".swingmusic" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            db_path = cache_dir / "spotify_cache.db"
            self.sqlite_conn = sqlite3.connect(str(db_path))
            self._init_sqlite_schema()
            logger.info("✅ SQLite fallback initialized")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite fallback: {e}")

    def _init_sqlite_schema(self):
        """Initialize SQLite cache schema"""
        if not self.sqlite_conn:
            return

        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spotify_cache (
                cache_key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                request_count INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_expires_at ON spotify_cache(expires_at)
        """)

        self.sqlite_conn.commit()

    def _rate_limit(self):
        """Apply rate limiting to prevent Spotify bans"""
        now = time.time()
        elapsed = now - self.last_request_time

        if elapsed < self.min_request_interval:
            wait_time = self.min_request_interval - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.1f}s")
            time.sleep(wait_time)

        self.last_request_time = time.time()
        self.request_count += 1

        # Check if we're approaching hourly limit
        if self.request_count > self.max_requests_per_hour:
            logger.warning(f"Approaching hourly request limit: {self.request_count}")

    def _get_cache_key(self, item_type: str, item_id: str) -> str:
        """Generate cache key for item"""
        return f"spotify:{item_type}:{item_id}"

    def get_cached_data(self, item_type: str, item_id: str) -> dict[str, Any] | None:
        """Get cached data - NO rate limiting for cache access"""
        cache_key = self._get_cache_key(item_type, item_id)

        # Try DragonflyDB first (NO rate limiting)
        if self.dragonfly_cache.client.is_available():
            cached = self.dragonfly_cache.get(cache_key)
            if cached:
                logger.debug(f"Cache hit (DragonflyDB): {cache_key}")
                return cached

        # Fallback to SQLite (NO rate limiting)
        if self.sqlite_conn:
            try:
                cursor = self.sqlite_conn.cursor()
                cursor.execute(
                    """
                    SELECT data FROM spotify_cache
                    WHERE cache_key = ? AND expires_at > datetime('now')
                """,
                    (cache_key,),
                )

                row = cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    logger.debug(f"Cache hit (SQLite): {cache_key}")
                    return data
            except Exception as e:
                logger.debug(f"SQLite cache miss: {e}")

        logger.debug(f"Cache miss: {cache_key}")
        return None

    def cache_data(self, item_type: str, item_id: str, data: dict[str, Any]) -> bool:
        """Cache Spotify data with 12-hour expiration"""
        cache_key = self._get_cache_key(item_type, item_id)

        success = False

        # Try DragonflyDB first (12-hour TTL)
        if self.dragonfly_cache.client.is_available():
            if self.dragonfly_cache.set(cache_key, data, ttl_hours=12):
                logger.debug(f"Cached (DragonflyDB): {cache_key}")
                success = True

        # Fallback to SQLite
        if self.sqlite_conn:
            try:
                cursor = self.sqlite_conn.cursor()
                expires_at = datetime.now() + self.cache_duration
                serialized_data = json.dumps(data)

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO spotify_cache
                    (cache_key, data, expires_at) VALUES (?, ?, ?)
                """,
                    (cache_key, serialized_data, expires_at.isoformat()),
                )

                self.sqlite_conn.commit()
                logger.debug(f"Cached (SQLite): {cache_key}")
                success = True
            except Exception as e:
                logger.debug(f"SQLite cache failed: {e}")

        return success

    def get_or_fetch_track(self, track_id: str, fetch_func) -> dict[str, Any] | None:
        """Get track from cache first, only rate limit real Spotify requests"""
        # Check cache first (NO rate limiting for cache access)
        cached = self.get_cached_data("track", track_id)
        if cached:
            return cached

        # Only apply rate limiting for REAL Spotify API calls
        self._rate_limit()

        # Fetch fresh data
        try:
            data = fetch_func(track_id)
            if data:
                # Cache the result
                self.cache_data("track", track_id, data)
                logger.info(f"Fetched and cached track: {track_id}")
                return data
        except Exception as e:
            logger.error(f"Failed to fetch track {track_id}: {e}")

        return None

    def get_or_fetch_album(self, album_id: str, fetch_func) -> dict[str, Any] | None:
        """Get album from cache first, only rate limit real Spotify requests"""
        # Check cache first (NO rate limiting for cache access)
        cached = self.get_cached_data("album", album_id)
        if cached:
            return cached

        # Only apply rate limiting for REAL Spotify API calls
        self._rate_limit()

        # Fetch fresh data
        try:
            data = fetch_func(album_id)
            if data:
                # Cache the result
                self.cache_data("album", album_id, data)
                logger.info(f"Fetched and cached album: {album_id}")
                return data
        except Exception as e:
            logger.error(f"Failed to fetch album {album_id}: {e}")

        return None

    def get_or_fetch_artist(self, artist_id: str, fetch_func) -> dict[str, Any] | None:
        """Get artist from cache first, only rate limit real Spotify requests"""
        # Check cache first (NO rate limiting for cache access)
        cached = self.get_cached_data("artist", artist_id)
        if cached:
            return cached

        # Only apply rate limiting for REAL Spotify API calls
        self._rate_limit()

        # Fetch fresh data
        try:
            data = fetch_func(artist_id)
            if data:
                # Cache the result
                self.cache_data("artist", artist_id, data)
                logger.info(f"Fetched and cached artist: {artist_id}")
                return data
        except Exception as e:
            logger.error(f"Failed to fetch artist {artist_id}: {e}")

        return None

    def cleanup_expired_cache(self):
        """Clean up expired cache entries"""
        cleaned_count = 0

        # Clean SQLite cache
        if self.sqlite_conn:
            try:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("""
                    DELETE FROM spotify_cache
                    WHERE expires_at <= datetime('now')
                """)
                cleaned_count = cursor.rowcount
                self.sqlite_conn.commit()
                logger.info(f"Cleaned {cleaned_count} expired SQLite cache entries")
            except Exception as e:
                logger.error(f"Failed to clean SQLite cache: {e}")

        # DragonflyDB handles expiration automatically
        logger.debug("DragonflyDB handles expiration automatically")

        return cleaned_count

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        stats = {
            "dragonfly_available": self.dragonfly_cache.client.is_available(),
            "sqlite_available": self.sqlite_conn is not None,
            "request_count": self.request_count,
            "cache_duration_hours": self.cache_duration.total_seconds() / 3600,
            "min_request_interval": self.min_request_interval,
        }

        # Get SQLite cache size
        if self.sqlite_conn:
            try:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM spotify_cache")
                stats["sqlite_cache_size"] = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT COUNT(*) FROM spotify_cache
                    WHERE expires_at > datetime('now')
                """)
                stats["sqlite_valid_cache_size"] = cursor.fetchone()[0]
            except Exception as e:
                logger.debug(f"Failed to get SQLite stats: {e}")

        # Get DragonflyDB cache size
        if self.dragonfly_cache.client.is_available():
            try:
                info = self.dragonfly_cache.client.info()
                stats["dragonfly_used_memory"] = info.get(
                    "used_memory_human", "Unknown"
                )
                stats["dragonfly_connected_clients"] = info.get("connected_clients", 0)
                stats["dragonfly_keys"] = len(
                    self.dragonfly_cache.client.keys("spotify:*")
                )
            except Exception as e:
                logger.debug(f"Failed to get DragonflyDB stats: {e}")

        return stats

    def close(self):
        """Close cache connections"""
        if self.dragonfly_cache.client:
            try:
                self.dragonfly_cache.client.close()
                logger.info("DragonflyDB connection closed")
            except Exception:
                pass

        if self.sqlite_conn:
            try:
                self.sqlite_conn.close()
                logger.info("SQLite connection closed")
            except Exception:
                pass


# Global cache manager instance
_cache_manager: SpotifyCacheManager | None = None


def get_spotify_cache_manager() -> SpotifyCacheManager:
    """Get or create the global Spotify cache manager"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = SpotifyCacheManager()
    return _cache_manager

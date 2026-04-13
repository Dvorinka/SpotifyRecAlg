"""
Native DragonflyDB Client for SwingMusic

Integrated as a native database service like SQLite, providing:
- Ultra-fast caching for all services
- Session management
- User preferences
- Temporary data storage
- Real-time features
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class DragonflyDBClient:
    """
    Native DragonflyDB client integrated into SwingMusic
    Provides Redis-compatible operations with automatic fallback
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        db: int | None = None,
    ):
        self.host = host or os.environ.get("DRAGONFLYDB_HOST", "localhost")
        self.port = port or int(os.environ.get("DRAGONFLYDB_PORT", "6379"))
        self.db = db if db is not None else int(os.environ.get("DRAGONFLYDB_DB", "0"))
        self.client = None
        self.available = False
        self._connect()

    def _connect(self):
        """Connect to DragonflyDB with fallback handling"""
        try:
            import redis

            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                retry_on_timeout=True,
                health_check_interval=30,
            )

            # Test connection
            self.client.ping()
            self.available = True
            logger.info(f"✅ DragonflyDB connected at {self.host}:{self.port}")

        except ImportError:
            logger.warning("❌ Redis library not installed, DragonflyDB unavailable")
            self.available = False
        except Exception as e:
            logger.warning(f"❌ DragonflyDB connection failed: {e}")
            self.available = False

    def is_available(self) -> bool:
        """Check if DragonflyDB is available"""
        if not self.available or not self.client:
            self._connect()
            if not self.available or not self.client:
                return False

        try:
            self.client.ping()
            return True
        except Exception:
            self.available = False
            return False

    def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set a key-value pair with optional TTL"""
        if not self.is_available():
            return False

        try:
            serialized_value = (
                json.dumps(value) if not isinstance(value, str) else value
            )

            if ttl:
                return self.client.setex(key, ttl, serialized_value)
            else:
                return self.client.set(key, serialized_value)
        except Exception as e:
            logger.debug(f"DragonflyDB set failed: {e}")
            return False

    def get(self, key: str) -> Any | None:
        """Get a value by key"""
        if not self.is_available():
            return None

        try:
            value = self.client.get(key)
            if value is None:
                return None

            # Try to deserialize as JSON
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except Exception as e:
            logger.debug(f"DragonflyDB get failed: {e}")
            return None

    def delete(self, key: str) -> bool:
        """Delete a key"""
        if not self.is_available():
            return False

        try:
            return bool(self.client.delete(key))
        except Exception as e:
            logger.debug(f"DragonflyDB delete failed: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists"""
        if not self.is_available():
            return False

        try:
            return bool(self.client.exists(key))
        except Exception as e:
            logger.debug(f"DragonflyDB exists failed: {e}")
            return False

    def expire(self, key: str, ttl: int) -> bool:
        """Set TTL for existing key"""
        if not self.is_available():
            return False

        try:
            return bool(self.client.expire(key, ttl))
        except Exception as e:
            logger.debug(f"DragonflyDB expire failed: {e}")
            return False

    def ttl(self, key: str) -> int:
        """Get TTL for key"""
        if not self.is_available():
            return -1

        try:
            return self.client.ttl(key)
        except Exception as e:
            logger.debug(f"DragonflyDB ttl failed: {e}")
            return -1

    def keys(self, pattern: str = "*") -> list[str]:
        """Get keys matching pattern"""
        if not self.is_available():
            return []

        try:
            return self.client.keys(pattern)
        except Exception as e:
            logger.debug(f"DragonflyDB keys failed: {e}")
            return []

    def incr(self, key: str, amount: int = 1) -> int:
        """Increment value by amount"""
        if not self.is_available():
            return 0

        try:
            return self.client.incr(key, amount)
        except Exception as e:
            logger.debug(f"DragonflyDB incr failed: {e}")
            return 0

    def lpush(self, key: str, *values) -> int:
        """Push values to left of list"""
        if not self.is_available():
            return 0

        try:
            return self.client.lpush(key, *values)
        except Exception as e:
            logger.debug(f"DragonflyDB lpush failed: {e}")
            return 0

    def rpop(self, key: str) -> str | None:
        """Pop value from right of list"""
        if not self.is_available():
            return None

        try:
            return self.client.rpop(key)
        except Exception as e:
            logger.debug(f"DragonflyDB rpop failed: {e}")
            return None

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Get range of list elements"""
        if not self.is_available():
            return []

        try:
            return self.client.lrange(key, start, end)
        except Exception as e:
            logger.debug(f"DragonflyDB lrange failed: {e}")
            return []

    def llen(self, key: str) -> int:
        """Get length of list"""
        if not self.is_available():
            return 0

        try:
            return self.client.llen(key)
        except Exception as e:
            logger.debug(f"DragonflyDB llen failed: {e}")
            return 0

    def lrem(self, key: str, count: int, value: str) -> int:
        """Remove elements from list"""
        if not self.is_available():
            return 0

        try:
            return self.client.lrem(key, count, value)
        except Exception as e:
            logger.debug(f"DragonflyDB lrem failed: {e}")
            return 0

    def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list to range"""
        if not self.is_available():
            return False

        try:
            return self.client.ltrim(key, start, end)
        except Exception as e:
            logger.debug(f"DragonflyDB ltrim failed: {e}")
            return False

    def flushdb(self) -> bool:
        """Clear all keys in current database"""
        if not self.is_available():
            return False

        try:
            return self.client.flushdb()
        except Exception as e:
            logger.debug(f"DragonflyDB flushdb failed: {e}")
            return False

    def ping(self) -> bool:
        """Ping DragonflyDB."""
        if not self.is_available():
            return False

        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.debug(f"DragonflyDB ping failed: {e}")
            self.available = False
            return False

    def info(self) -> dict[str, Any]:
        """Get DragonflyDB server info"""
        if not self.is_available():
            return {}

        try:
            info = self.client.info()
            return {
                "version": info.get("redis_version", "unknown"),
                "used_memory": info.get("used_memory", 0),
                "used_memory_human": info.get("used_memory_human", "0B"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "uptime_in_seconds": info.get("uptime_in_seconds", 0),
            }
        except Exception as e:
            logger.debug(f"DragonflyDB info failed: {e}")
            return {}

    def close(self):
        """Close DragonflyDB connection"""
        if self.client:
            try:
                self.client.close()
                logger.info("DragonflyDB connection closed")
            except Exception:
                pass


# Global DragonflyDB instance (like SQLite)
_dragonfly_client: DragonflyDBClient | None = None


def get_dragonfly_client() -> DragonflyDBClient:
    """Get the global DragonflyDB client instance"""
    global _dragonfly_client
    if _dragonfly_client is None:
        _dragonfly_client = DragonflyDBClient()
    return _dragonfly_client


def init_dragonfly_if_available() -> bool:
    """Initialize DragonflyDB if available"""
    client = get_dragonfly_client()
    return client.is_available()


class DragonflyCache:
    """High-level cache interface using DragonflyDB"""

    def __init__(self, prefix: str = "swingmusic"):
        self.client = get_dragonfly_client()
        self.prefix = prefix

    def _make_key(self, key: str) -> str:
        """Create namespaced key"""
        return f"{self.prefix}:{key}"

    def set(self, key: str, value: Any, ttl_hours: int = 12) -> bool:
        """Set cache value with TTL in hours"""
        ttl_seconds = ttl_hours * 3600
        return self.client.set(self._make_key(key), value, ttl_seconds)

    def get(self, key: str) -> Any | None:
        """Get cache value"""
        return self.client.get(self._make_key(key))

    def delete(self, key: str) -> bool:
        """Delete cache value"""
        return self.client.delete(self._make_key(key))

    def exists(self, key: str) -> bool:
        """Check if cache value exists"""
        return self.client.exists(self._make_key(key))

    def clear_all(self) -> bool:
        """Clear all SwingMusic cache entries"""
        if not self.client.is_available():
            return False

        keys = self.client.keys(f"{self.prefix}:*")
        if keys:
            return self.client.client.delete(*keys) > 0
        return True


# Native cache instances for different purposes
spotify_cache = DragonflyCache("spotify")
session_cache = DragonflyCache("session")
user_cache = DragonflyCache("user")
temp_cache = DragonflyCache("temp")


def get_spotify_cache() -> DragonflyCache:
    """Get Spotify metadata cache"""
    return spotify_cache


def get_session_cache() -> DragonflyCache:
    """Get user session cache"""
    return session_cache


def get_user_cache() -> DragonflyCache:
    """Get user preferences cache"""
    return user_cache


def get_temp_cache() -> DragonflyCache:
    """Get temporary data cache"""
    return temp_cache

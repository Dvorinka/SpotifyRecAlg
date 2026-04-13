"""
Rate Limiting using DragonflyDB.

Provides distributed rate limiting using DragonflyDB's atomic INCR command
with automatic key expiration. This is more efficient than in-memory rate
limiting for distributed deployments and provides persistence across restarts.
"""

import logging
import time

from swingmusic.db.dragonfly_client import get_dragonfly_client

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token bucket / sliding window rate limiter using DragonflyDB.

    Uses atomic Redis operations (INCR, EXPIRE) to implement rate limiting
    that works across multiple server instances.
    """

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_dragonfly_client()
        return self._client

    def _get_key(self, identifier: str, action: str) -> str:
        """Get the Redis key for a rate limit counter."""
        return f"ratelimit:{action}:{identifier}"

    def _get_window_key(self, identifier: str, action: str, window: int) -> str:
        """Get the Redis key for a sliding window rate limit."""
        current_window = int(time.time() // window)
        return f"ratelimit:{action}:{identifier}:{current_window}"

    def is_allowed(
        self, identifier: str, action: str, max_requests: int, window_seconds: int = 60
    ) -> tuple[bool, int, int]:
        """
        Check if a request is allowed under the rate limit.

        Uses a sliding window algorithm with DragonflyDB.

        Args:
            identifier: Unique identifier (e.g., user ID, IP address)
            action: The action being rate limited (e.g., "login", "download")
            max_requests: Maximum number of requests allowed in the window
            window_seconds: Time window in seconds

        Returns:
            Tuple of (is_allowed, current_count, retry_after_seconds)
        """
        if not self.client.is_available():
            # If DragonflyDB is not available, allow the request
            return True, 0, 0

        try:
            key = self._get_window_key(identifier, action, window_seconds)

            # Use pipeline for atomic operations
            pipe = self.client.client.pipeline()

            # Increment counter
            pipe.incr(key)

            # Set expiry on first request (only if key is new)
            pipe.expire(key, window_seconds, nx=True)

            results = pipe.execute()
            current_count = results[0]

            if current_count <= max_requests:
                return True, current_count, 0
            else:
                # Calculate retry after
                ttl = self.client.client.ttl(key)
                retry_after = max(1, ttl) if ttl > 0 else window_seconds
                return False, current_count, retry_after

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            # On error, allow the request
            return True, 0, 0

    def increment(self, identifier: str, action: str, window_seconds: int = 60) -> int:
        """
        Increment the counter for an action without checking the limit.

        Useful for tracking usage without enforcing limits.

        Returns:
            The new counter value
        """
        if not self.client.is_available():
            return 0

        try:
            key = self._get_window_key(identifier, action, window_seconds)

            pipe = self.client.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)

            results = pipe.execute()
            return results[0]

        except Exception as e:
            logger.error(f"Rate limit increment failed: {e}")
            return 0

    def get_count(self, identifier: str, action: str, window_seconds: int = 60) -> int:
        """Get the current count for an action in the current window."""
        if not self.client.is_available():
            return 0

        try:
            key = self._get_window_key(identifier, action, window_seconds)
            value = self.client.get(key)
            return int(value) if value else 0
        except Exception:
            return 0

    def reset(self, identifier: str, action: str) -> bool:
        """Reset the rate limit counter for an identifier and action."""
        if not self.client.is_available():
            return False

        try:
            # Delete all windows for this identifier/action
            pattern = f"ratelimit:{action}:{identifier}:*"
            keys = self.client.client.keys(pattern)

            if keys:
                self.client.client.delete(*keys)

            return True
        except Exception as e:
            logger.error(f"Rate limit reset failed: {e}")
            return False

    def get_remaining(
        self, identifier: str, action: str, max_requests: int, window_seconds: int = 60
    ) -> int:
        """Get the number of remaining requests allowed."""
        current = self.get_count(identifier, action, window_seconds)
        return max(0, max_requests - current)


class LoginRateLimiter(RateLimiter):
    """Rate limiter specifically for login attempts."""

    # Default: 10 login attempts per minute
    MAX_ATTEMPTS = 10
    WINDOW_SECONDS = 60

    def check_login(self, identifier: str) -> tuple[bool, int, int]:
        """Check if login is allowed for the given identifier."""
        return self.is_allowed(
            identifier, "login", self.MAX_ATTEMPTS, self.WINDOW_SECONDS
        )

    def record_failed_login(self, identifier: str) -> int:
        """Record a failed login attempt."""
        return self.increment(identifier, "login", self.WINDOW_SECONDS)

    def clear_failed_logins(self, identifier: str) -> bool:
        """Clear failed login attempts after successful login."""
        return self.reset(identifier, "login")


class DownloadRateLimiter(RateLimiter):
    """Rate limiter specifically for downloads."""

    # Default: 100 downloads per hour
    MAX_DOWNLOADS = 100
    WINDOW_SECONDS = 3600

    def check_download(self, user_id: int) -> tuple[bool, int, int]:
        """Check if download is allowed for the given user."""
        return self.is_allowed(
            str(user_id), "download", self.MAX_DOWNLOADS, self.WINDOW_SECONDS
        )

    def record_download(self, user_id: int) -> int:
        """Record a download."""
        return self.increment(str(user_id), "download", self.WINDOW_SECONDS)


class APIRateLimiter(RateLimiter):
    """Rate limiter for general API endpoints."""

    # Default: 100 requests per minute per user
    MAX_REQUESTS = 100
    WINDOW_SECONDS = 60

    def check_api_request(self, identifier: str) -> tuple[bool, int, int]:
        """Check if API request is allowed."""
        return self.is_allowed(
            identifier, "api", self.MAX_REQUESTS, self.WINDOW_SECONDS
        )


# Global instances
rate_limiter = RateLimiter()
login_rate_limiter = LoginRateLimiter()
download_rate_limiter = DownloadRateLimiter()
api_rate_limiter = APIRateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return rate_limiter


def get_login_rate_limiter() -> LoginRateLimiter:
    """Get the global login rate limiter instance."""
    return login_rate_limiter


def get_download_rate_limiter() -> DownloadRateLimiter:
    """Get the global download rate limiter instance."""
    return download_rate_limiter


def get_api_rate_limiter() -> APIRateLimiter:
    """Get the global API rate limiter instance."""
    return api_rate_limiter

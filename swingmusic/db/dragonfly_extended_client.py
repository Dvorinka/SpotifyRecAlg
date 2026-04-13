"""
Extended DragonflyDB Client for SwingMusic

Comprehensive caching system with 15+ cache services for:
- Track metadata and persistence
- User sessions and preferences
- Mobile offline synchronization
- Real-time features and analytics
- Background job processing
- Search and recommendations
"""

import json
import logging
from typing import Any

from swingmusic.db.dragonfly_client import DragonflyCache, get_dragonfly_client

logger = logging.getLogger(__name__)


class ExtendedDragonflyServices:
    """
    Extended DragonflyDB services for complete SwingMusic integration
    """

    def __init__(self):
        self.client = get_dragonfly_client()

        # Core performance caches
        self.track_cache = DragonflyCache("tracks")
        self.artist_cache = DragonflyCache("artists")
        self.album_cache = DragonflyCache("albums")

        # User experience caches
        self.session_cache = DragonflyCache("sessions")
        self.user_cache = DragonflyCache("users")
        self.search_cache = DragonflyCache("search")
        self.homepage_cache = DragonflyCache("homepage")

        # Mobile and offline caches
        self.mobile_cache = DragonflyCache("mobile")
        self.sync_cache = DragonflyCache("sync")
        self.progress_cache = DragonflyCache("progress")
        self.playlist_cache = DragonflyCache("playlists")

        # Real-time feature caches
        self.playcount_cache = DragonflyCache("playcounts")
        self.recent_cache = DragonflyCache("recent")
        self.favorite_cache = DragonflyCache("favorites")
        self.recommendation_cache = DragonflyCache("recommendations")

        # Background processing caches
        self.job_cache = DragonflyCache("jobs")
        self.lyrics_cache = DragonflyCache("lyrics")
        self.index_cache = DragonflyCache("index")
        self.temp_cache = DragonflyCache("temp")

        logger.info("Extended DragonflyDB services initialized")


class TrackCacheService:
    """High-performance track caching with persistence"""

    def __init__(self):
        self.cache = DragonflyCache("tracks")

    def get_track(self, trackhash: str) -> dict[str, Any] | None:
        """Get track data from cache"""
        return self.cache.get(f"track:{trackhash}")

    def set_track(
        self, trackhash: str, track_data: dict[str, Any], ttl_hours: int = 24
    ):
        """Cache track data"""
        return self.cache.set(f"track:{trackhash}", track_data, ttl_hours)

    def get_track_batch(self, trackhashes: list[str]) -> dict[str, Any]:
        """Get multiple tracks from cache"""
        results = {}
        for trackhash in trackhashes:
            track = self.get_track(trackhash)
            if track:
                results[trackhash] = track
        return results

    def set_track_batch(self, tracks: dict[str, dict[str, Any]], ttl_hours: int = 24):
        """Cache multiple tracks"""
        success_count = 0
        for trackhash, track_data in tracks.items():
            if self.set_track(trackhash, track_data, ttl_hours):
                success_count += 1
        return success_count

    def invalidate_track(self, trackhash: str):
        """Remove track from cache"""
        return self.cache.delete(f"track:{trackhash}")

    def get_stats(self) -> dict[str, Any]:
        """Get track cache statistics"""
        keys = self.cache.client.keys("tracks:track:*")
        return {
            "total_tracks": len(keys),
            "memory_usage": self.cache.client.info().get(
                "used_memory_human", "Unknown"
            ),
        }


class UserSessionService:
    """Ultra-fast user session management"""

    def __init__(self):
        self.cache = DragonflyCache("sessions")
        # Backward compatibility for older call sites.
        self.session_cache = self.cache

    def create_session(
        self, session_token: str, user_data: dict[str, Any], ttl_hours: int = 24
    ):
        """Create user session"""
        return self.cache.set(f"session:{session_token}", user_data, ttl_hours)

    def set_user_session(
        self, userid: int, user_data: dict[str, Any], ttl_seconds: int = 24 * 3600
    ):
        """Store latest session payload by user id for quick lookups."""
        ttl_hours = max(1, int(ttl_seconds // 3600))
        return self.cache.set(f"user_session:{userid}", user_data, ttl_hours)

    def get_user_session(self, userid: int) -> dict[str, Any] | None:
        """Get latest session payload for a user id."""
        return self.cache.get(f"user_session:{userid}")

    def get_session(self, session_token: str) -> dict[str, Any] | None:
        """Get user session"""
        return self.cache.get(f"session:{session_token}")

    def refresh_session(self, session_token: str, ttl_hours: int = 24):
        """Refresh session TTL"""
        return self.cache.expire(f"session:{session_token}", ttl_hours * 3600)

    def invalidate_session(self, session_token: str):
        """Invalidate user session"""
        return self.cache.delete(f"session:{session_token}")

    def invalidate_user_session(self, userid: int):
        """Invalidate latest session payload for a user id."""
        return self.cache.delete(f"user_session:{userid}")

    def get_user_sessions(self, userid: int) -> list[str]:
        """Get all active sessions for user"""
        pattern = "session:*"
        keys = self.cache.client.keys(pattern)
        user_sessions = []

        for key in keys:
            session_data = self.cache.get(key.replace("session:", ""))
            if session_data and session_data.get("userid") == userid:
                user_sessions.append(key)

        return user_sessions


class MobileSyncService:
    """Reliable mobile offline synchronization"""

    def __init__(self):
        self.cache = DragonflyCache("mobile")

    def queue_sync_action(self, userid: int, action: dict[str, Any]):
        """Queue a sync action for mobile device"""
        queue_key = f"sync_queue:user:{userid}"
        return self.cache.client.lpush(queue_key, json.dumps(action))

    def get_sync_actions(self, userid: int, count: int = 10) -> list[dict[str, Any]]:
        """Get pending sync actions for user"""
        queue_key = f"sync_queue:user:{userid}"
        actions_data = self.cache.client.lrange(queue_key, 0, count - 1)

        actions = []
        for action_data in actions_data:
            try:
                actions.append(json.loads(action_data))
            except json.JSONDecodeError:
                continue

        return actions

    def mark_sync_completed(self, userid: int, action_id: str):
        """Mark sync action as completed"""
        # Remove from queue
        queue_key = f"sync_queue:user:{userid}"
        return self.cache.client.lrem(queue_key, 1, action_id)

    def set_sync_state(self, userid: int, device_id: str, state: dict[str, Any]):
        """Set device sync state"""
        state_key = f"sync_state:user:{userid}:device:{device_id}"
        return self.cache.set(state_key, state, ttl_hours=24)

    def get_sync_state(self, userid: int, device_id: str) -> dict[str, Any] | None:
        """Get device sync state"""
        state_key = f"sync_state:user:{userid}:device:{device_id}"
        return self.cache.get(state_key)


class RealTimeFeaturesService:
    """Real-time features like play counts and favorites"""

    def __init__(self):
        self.playcount_cache = DragonflyCache("playcounts")
        self.recent_cache = DragonflyCache("recent")
        self.favorite_cache = DragonflyCache("favorites")

    def increment_playcount(self, trackhash: str, userid: int | None = None):
        """Increment track play count"""
        key = f"plays:{trackhash}"
        if userid:
            key = f"plays:user:{userid}:track:{trackhash}"

        return self.playcount_cache.client.incr(key)

    def get_playcount(self, trackhash: str, userid: int | None = None) -> int:
        """Get track play count"""
        key = f"plays:{trackhash}"
        if userid:
            key = f"plays:user:{userid}:track:{trackhash}"

        count = self.playcount_cache.client.get(key)
        return int(count) if count else 0

    def add_to_recently_played(self, userid: int, trackhash: str, limit: int = 50):
        """Add track to recently played list"""
        key = f"recent:user:{userid}"

        # Add to beginning of list
        self.recent_cache.client.lpush(key, trackhash)

        # Remove duplicates
        self.recent_cache.client.lrem(key, 1, trackhash)

        # Add back to beginning
        self.recent_cache.client.lpush(key, trackhash)

        # Limit list size
        self.recent_cache.client.ltrim(key, 0, limit - 1)

        # Set TTL
        self.recent_cache.client.expire(key, 7 * 24 * 3600)  # 7 days

    def get_recently_played(self, userid: int, limit: int = 50) -> list[str]:
        """Get recently played tracks for user"""
        key = f"recent:user:{userid}"
        return self.recent_cache.client.lrange(key, 0, limit - 1)

    def toggle_favorite(self, userid: int, trackhash: str) -> bool:
        """Toggle favorite status for track"""
        key = f"fav:user:{userid}:track:{trackhash}"

        current = self.favorite_cache.client.get(key)
        if current:
            # Remove favorite
            self.favorite_cache.client.delete(key)
            return False
        else:
            # Add favorite
            self.favorite_cache.client.set(key, True, ttl_hours=24 * 30)  # 30 days
            return True

    def is_favorite(self, userid: int, trackhash: str) -> bool:
        """Check if track is favorited by user"""
        key = f"fav:user:{userid}:track:{trackhash}"
        return bool(self.favorite_cache.client.get(key))

    def get_user_favorites(self, userid: int) -> list[str]:
        """Get all favorite tracks for user"""
        pattern = f"fav:user:{userid}:track:*"
        keys = self.favorite_cache.client.keys(pattern)

        favorites = []
        for key in keys:
            trackhash = key.split(":")[-1]
            favorites.append(trackhash)

        return favorites


class SearchCacheService:
    """High-performance search results caching"""

    def __init__(self):
        self.cache = DragonflyCache("search")

    def cache_search_results(
        self, query: str, results: dict[str, Any], ttl_hours: int = 6
    ):
        """Cache search results"""
        query_hash = hash(query)  # Simple hash for key
        return self.cache.set(f"results:{query_hash}", results, ttl_hours)

    def get_search_results(self, query: str) -> dict[str, Any] | None:
        """Get cached search results"""
        query_hash = hash(query)
        return self.cache.get(f"results:{query_hash}")

    def cache_suggestions(
        self, query_type: str, suggestions: list[str], ttl_hours: int = 12
    ):
        """Cache search suggestions"""
        return self.cache.set(f"suggestions:{query_type}", suggestions, ttl_hours)

    def get_suggestions(self, query_type: str) -> list[str]:
        """Get cached search suggestions"""
        suggestions = self.cache.get(f"suggestions:{query_type}")
        return suggestions if suggestions else []

    def invalidate_search_cache(self, pattern: str = "*"):
        """Invalidate search cache"""
        keys = self.cache.client.keys(f"search:{pattern}")
        if keys:
            return self.cache.client.delete(*keys)
        return True


class JobQueueService:
    """High-performance background job processing"""

    def __init__(self):
        self.cache = DragonflyCache("jobs")

    def enqueue_job(self, queue: str, job_data: dict[str, Any]):
        """Add job to queue"""
        job_json = json.dumps(job_data)
        return self.cache.client.lpush(f"queue:{queue}", job_json)

    def dequeue_job(self, queue: str) -> dict[str, Any] | None:
        """Get next job from queue"""
        job_json = self.cache.client.rpop(f"queue:{queue}")
        if job_json:
            try:
                return json.loads(job_json)
            except json.JSONDecodeError:
                return None
        return None

    def get_queue_size(self, queue: str) -> int:
        """Get number of jobs in queue"""
        return self.cache.client.llen(f"queue:{queue}")

    def peek_jobs(self, queue: str, count: int = 10) -> list[dict[str, Any]]:
        """Peek at jobs in queue without removing them"""
        jobs_data = self.cache.client.lrange(f"queue:{queue}", 0, count - 1)

        jobs = []
        for job_data in jobs_data:
            try:
                jobs.append(json.loads(job_data))
            except json.JSONDecodeError:
                continue

        return jobs

    def clear_queue(self, queue: str):
        """Clear all jobs from queue"""
        return self.cache.client.delete(f"queue:{queue}")


# Global service instances
_track_cache_service: TrackCacheService | None = None
_user_session_service: UserSessionService | None = None
_mobile_sync_service: MobileSyncService | None = None
_realtime_service: RealTimeFeaturesService | None = None
_search_cache_service: SearchCacheService | None = None
_job_queue_service: JobQueueService | None = None


def get_track_cache_service() -> TrackCacheService:
    """Get track cache service instance"""
    global _track_cache_service
    if _track_cache_service is None:
        _track_cache_service = TrackCacheService()
    return _track_cache_service


def get_user_session_service() -> UserSessionService:
    """Get user session service instance"""
    global _user_session_service
    if _user_session_service is None:
        _user_session_service = UserSessionService()
    return _user_session_service


def get_mobile_sync_service() -> MobileSyncService:
    """Get mobile sync service instance"""
    global _mobile_sync_service
    if _mobile_sync_service is None:
        _mobile_sync_service = MobileSyncService()
    return _mobile_sync_service


def get_realtime_service() -> RealTimeFeaturesService:
    """Get real-time features service instance"""
    global _realtime_service
    if _realtime_service is None:
        _realtime_service = RealTimeFeaturesService()
    return _realtime_service


def get_search_cache_service() -> SearchCacheService:
    """Get search cache service instance"""
    global _search_cache_service
    if _search_cache_service is None:
        _search_cache_service = SearchCacheService()
    return _search_cache_service


def get_job_queue_service() -> JobQueueService:
    """Get job queue service instance"""
    global _job_queue_service
    if _job_queue_service is None:
        _job_queue_service = JobQueueService()
    return _job_queue_service


def get_all_dragonfly_services() -> dict[str, Any]:
    """Get all DragonflyDB services for monitoring"""
    return {
        "track_cache": get_track_cache_service(),
        "user_sessions": get_user_session_service(),
        "mobile_sync": get_mobile_sync_service(),
        "realtime": get_realtime_service(),
        "search_cache": get_search_cache_service(),
        "job_queue": get_job_queue_service(),
    }

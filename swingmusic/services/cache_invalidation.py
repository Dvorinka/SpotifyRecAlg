"""
Cache invalidation hooks for DragonflyDB caches.

This module provides centralized cache invalidation when data is updated,
ensuring cache consistency across all DragonflyDB cache namespaces.
"""

import logging

from swingmusic.db.dragonfly_extended_client import (
    get_homepage_cache_service,
    get_mobile_sync_service,
    get_realtime_service,
    get_search_cache_service,
    get_track_cache_service,
    get_user_session_service,
)

logger = logging.getLogger(__name__)


class CacheInvalidationService:
    """Centralized cache invalidation for DragonflyDB caches."""

    def invalidate_track(self, trackhash: str) -> None:
        """Invalidate all caches related to a track."""
        # Track metadata cache
        track_cache = get_track_cache_service()
        if track_cache.cache.client.is_available():
            try:
                track_cache.invalidate_track(trackhash)
                logger.debug(f"Invalidated track cache for {trackhash}")
            except Exception as e:
                logger.debug(f"Failed to invalidate track cache: {e}")

        # Homepage cache (track may appear in recent/featured)
        homepage_cache = get_homepage_cache_service()
        if homepage_cache.cache.client.is_available():
            try:
                # Invalidate all homepage caches that might contain this track
                homepage_cache.invalidate_user_homepage(0)  # Global cache
                logger.debug("Invalidated homepage cache")
            except Exception as e:
                logger.debug(f"Failed to invalidate homepage cache: {e}")

        # Search cache (track may appear in search results)
        search_cache = get_search_cache_service()
        if search_cache.cache.client.is_available():
            try:
                search_cache.clear_search_cache()
                logger.debug("Invalidated search cache")
            except Exception as e:
                logger.debug(f"Failed to invalidate search cache: {e}")

    def invalidate_tracks(self, trackhashes: list[str]) -> None:
        """Invalidate caches for multiple tracks."""
        for trackhash in trackhashes:
            self.invalidate_track(trackhash)

    def invalidate_album(self, albumhash: str) -> None:
        """Invalidate all caches related to an album."""
        # Homepage cache
        homepage_cache = get_homepage_cache_service()
        if homepage_cache.cache.client.is_available():
            try:
                homepage_cache.invalidate_user_homepage(0)
                logger.debug(f"Invalidated homepage cache for album {albumhash}")
            except Exception as e:
                logger.debug(f"Failed to invalidate homepage cache: {e}")

        # Search cache
        search_cache = get_search_cache_service()
        if search_cache.cache.client.is_available():
            try:
                search_cache.clear_search_cache()
                logger.debug("Invalidated search cache")
            except Exception as e:
                logger.debug(f"Failed to invalidate search cache: {e}")

    def invalidate_artist(self, artisthash: str) -> None:
        """Invalidate all caches related to an artist."""
        # Homepage cache
        homepage_cache = get_homepage_cache_service()
        if homepage_cache.cache.client.is_available():
            try:
                homepage_cache.invalidate_user_homepage(0)
                logger.debug(f"Invalidated homepage cache for artist {artisthash}")
            except Exception as e:
                logger.debug(f"Failed to invalidate homepage cache: {e}")

        # Search cache
        search_cache = get_search_cache_service()
        if search_cache.cache.client.is_available():
            try:
                search_cache.clear_search_cache()
                logger.debug("Invalidated search cache")
            except Exception as e:
                logger.debug(f"Failed to invalidate search cache: {e}")

    def invalidate_user_session(self, userid: int) -> None:
        """Invalidate user session cache."""
        session_service = get_user_session_service()
        if session_service.session_cache.client.is_available():
            try:
                session_service.invalidate_session(userid)
                logger.debug(f"Invalidated session for user {userid}")
            except Exception as e:
                logger.debug(f"Failed to invalidate session: {e}")

    def invalidate_user_homepage(self, userid: int) -> None:
        """Invalidate homepage cache for a specific user."""
        homepage_cache = get_homepage_cache_service()
        if homepage_cache.cache.client.is_available():
            try:
                homepage_cache.invalidate_user_homepage(userid)
                logger.debug(f"Invalidated homepage for user {userid}")
            except Exception as e:
                logger.debug(f"Failed to invalidate homepage: {e}")

    def invalidate_mobile_sync(self, device_id: str) -> None:
        """Invalidate mobile sync cache for a device."""
        sync_service = get_mobile_sync_service()
        if sync_service.sync_cache.client.is_available():
            try:
                sync_service.clear_device_sync_queue(device_id)
                logger.debug(f"Invalidated sync cache for device {device_id}")
            except Exception as e:
                logger.debug(f"Failed to invalidate sync cache: {e}")

    def invalidate_favorite_status(self, userid: int, trackhash: str) -> None:
        """Invalidate favorite status cache for a track."""
        realtime = get_realtime_service()
        if realtime.favorite_cache.client.is_available():
            try:
                realtime.toggle_favorite(userid, trackhash)
                logger.debug(f"Invalidated favorite status for {trackhash}")
            except Exception as e:
                logger.debug(f"Failed to invalidate favorite status: {e}")

    def invalidate_playcount(self, trackhash: str) -> None:
        """Invalidate playcount cache for a track."""
        realtime = get_realtime_service()
        if realtime.playcount_cache.client.is_available():
            try:
                # Clear playcount cache entry
                key = f"playcounts:{trackhash}"
                realtime.playcount_cache.client.delete(key)
                logger.debug(f"Invalidated playcount for {trackhash}")
            except Exception as e:
                logger.debug(f"Failed to invalidate playcount: {e}")

    def invalidate_all_caches(self) -> None:
        """Invalidate all caches - use sparingly."""
        services = [
            get_track_cache_service(),
            get_search_cache_service(),
            get_homepage_cache_service(),
            get_user_session_service(),
            get_mobile_sync_service(),
            get_realtime_service(),
        ]

        for service in services:
            try:
                if hasattr(service, "cache") and service.cache.client.is_available():
                    service.cache.client.flushdb()
                    logger.info(f"Flushed cache for {service.__class__.__name__}")
                elif (
                    hasattr(service, "session_cache")
                    and service.session_cache.client.is_available()
                ):
                    service.session_cache.client.flushdb()
                    logger.info(f"Flushed cache for {service.__class__.__name__}")
                elif (
                    hasattr(service, "playcount_cache")
                    and service.playcount_cache.client.is_available()
                ):
                    service.playcount_cache.client.flushdb()
                    logger.info(f"Flushed cache for {service.__class__.__name__}")
            except Exception as e:
                logger.error(f"Failed to flush cache: {e}")


# Global instance
cache_invalidation = CacheInvalidationService()


def on_track_inserted(trackhash: str) -> None:
    """Hook called when a new track is inserted."""
    # Invalidate search and homepage caches
    cache_invalidation.invalidate_track(trackhash)


def on_track_updated(trackhash: str) -> None:
    """Hook called when a track is updated."""
    cache_invalidation.invalidate_track(trackhash)


def on_track_deleted(trackhash: str) -> None:
    """Hook called when a track is deleted."""
    cache_invalidation.invalidate_track(trackhash)


def on_album_updated(albumhash: str) -> None:
    """Hook called when an album is updated."""
    cache_invalidation.invalidate_album(albumhash)


def on_artist_updated(artisthash: str) -> None:
    """Hook called when an artist is updated."""
    cache_invalidation.invalidate_artist(artisthash)


def on_user_updated(userid: int) -> None:
    """Hook called when user data is updated."""
    cache_invalidation.invalidate_user_session(userid)
    cache_invalidation.invalidate_user_homepage(userid)


def on_playlist_updated(playlist_id: int, userid: int) -> None:
    """Hook called when a playlist is updated."""
    cache_invalidation.invalidate_user_homepage(userid)


def on_library_scan_completed() -> None:
    """Hook called when a library scan completes."""
    # Invalidate all caches since library content changed
    cache_invalidation.invalidate_all_caches()

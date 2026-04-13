"""
Cached Spotify Web Player Client with Rate Limiting and DragonflyDB

Enhanced Spotify client with intelligent caching to:
- Rate limit requests (2 second intervals, 1000/hour max)
- Cache data for 12 hours in DragonflyDB/SQLite
- Protect against Spotify API bans
- Provide fast response times for cached data
"""

import logging
from typing import Any

from swingmusic.services.spotify_cache_manager import get_spotify_cache_manager
from swingmusic.services.spotify_web_player_client import (
    SpotifyTrack,
    get_spotify_web_player_client,
)

logger = logging.getLogger(__name__)


class CachedSpotifyClient:
    """
    Enhanced Spotify client with intelligent caching and rate limiting
    """

    def __init__(self, cache_duration_hours: int = 12):
        self.cache_manager = get_spotify_cache_manager()
        self.spotify_client = get_spotify_web_player_client()

        logger.info(
            f"Cached Spotify client initialized (cache: {cache_duration_hours}h)"
        )

    def get_track(self, track_id: str) -> SpotifyTrack | None:
        """Get track with caching and rate limiting"""

        def fetch_track(track_id: str) -> dict[str, Any] | None:
            track = self.spotify_client.get_track(track_id)
            if track:
                return {
                    "id": track.id,
                    "name": track.name,
                    "artists": track.artists,
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "playcount": track.playcount,
                    "popularity": track.popularity,
                    "preview_url": track.preview_url,
                    "explicit": track.explicit,
                    "external_urls": track.external_urls,
                    "track_number": track.track_number,
                    "disc_number": track.disc_number,
                }
            return None

        # Get from cache or fetch
        cached_data = self.cache_manager.get_or_fetch_track(track_id, fetch_track)

        if cached_data:
            return SpotifyTrack(**cached_data)

        return None

    def get_album(self, album_id: str) -> dict[str, Any] | None:
        """Get album with caching and rate limiting"""

        def fetch_album(album_id: str) -> dict[str, Any] | None:
            album = self.spotify_client.get_album(album_id)
            if album:
                return {
                    "id": album.id,
                    "name": album.name,
                    "artists": album.artists,
                    "release_date": album.release_date,
                    "total_tracks": album.total_tracks,
                    "popularity": album.popularity,
                    "images": album.images,
                    "external_urls": album.external_urls,
                    "available_markets": album.available_markets,
                    "album_type": album.album_type,
                    "tracks": album.tracks,
                }
            return None

        return self.cache_manager.get_or_fetch_album(album_id, fetch_album)

    def get_artist(self, artist_id: str) -> dict[str, Any] | None:
        """Get artist with caching and rate limiting"""

        def fetch_artist(artist_id: str) -> dict[str, Any] | None:
            artist = self.spotify_client.get_artist(artist_id)
            if artist:
                return {
                    "id": artist.id,
                    "name": artist.name,
                    "followers": artist.followers,
                    "popularity": artist.popularity,
                    "genres": artist.genres,
                    "images": artist.images,
                    "external_urls": artist.external_urls,
                }
            return None

        return self.cache_manager.get_or_fetch_artist(artist_id, fetch_artist)

    def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        """Get playlist with caching and rate limiting"""

        def fetch_playlist(playlist_id: str) -> dict[str, Any] | None:
            playlist = self.spotify_client.get_playlist(playlist_id)
            if playlist:
                return {
                    "id": playlist.id,
                    "name": playlist.name,
                    "description": playlist.description,
                    "owner": playlist.owner,
                    "public": playlist.public,
                    "collaborative": playlist.collaborative,
                    "tracks": playlist.tracks,
                    "images": playlist.images,
                    "external_urls": playlist.external_urls,
                }
            return None

        return self.cache_manager.get_or_fetch_track(
            f"playlist:{playlist_id}", fetch_playlist
        )

    def search(
        self, query: str, search_type: str = "track", limit: int = 20
    ) -> dict[str, Any]:
        """Search with minimal caching (search results change frequently)"""
        # Apply rate limiting for search
        self.cache_manager._rate_limit()

        try:
            return self.spotify_client.search(query, search_type, limit)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {"tracks": [], "albums": [], "artists": []}

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache and rate limiting statistics"""
        stats = self.cache_manager.get_cache_stats()
        stats.update(
            {
                "spotify_token_valid": self.spotify_client._token is not None,
                "spotify_client_token_valid": (
                    self.spotify_client._token.client_token is not None
                    if self.spotify_client._token
                    else False
                ),
            }
        )
        return stats

    def cleanup_cache(self) -> int:
        """Clean up expired cache entries"""
        return self.cache_manager.cleanup_expired_cache()

    def preload_popular_data(self, track_ids: list[str]) -> dict[str, bool]:
        """Preload popular tracks to cache (for faster startup)"""
        results = {}

        logger.info(f"Preloading {len(track_ids)} popular tracks...")

        for i, track_id in enumerate(track_ids):
            logger.info(f"Preloading track {i + 1}/{len(track_ids)}: {track_id}")

            # Check if already cached
            if self.cache_manager.get_cached_data("track", track_id):
                results[track_id] = True
                continue

            # Fetch and cache
            track = self.get_track(track_id)
            results[track_id] = track is not None

            # Small delay between preloads to be respectful
            if i < len(track_ids) - 1:
                import time

                time.sleep(0.5)

        success_count = sum(1 for success in results.values() if success)
        logger.info(f"Preloaded {success_count}/{len(track_ids)} tracks successfully")

        return results


# Global cached client instance
_cached_client: CachedSpotifyClient | None = None


def get_cached_spotify_client(cache_duration_hours: int = 12) -> CachedSpotifyClient:
    """Get or create the global cached Spotify client"""
    global _cached_client
    if _cached_client is None:
        _cached_client = CachedSpotifyClient(cache_duration_hours)
    return _cached_client

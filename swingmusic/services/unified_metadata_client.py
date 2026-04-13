"""
Unified Metadata Client - Combines Spotify, MusicBrainz, and optional services

This client provides a single interface for all music metadata needs:
- Spotify: Core metadata (names, artists, albums, durations, play counts)
- MusicBrainz: Genre enrichment, ISRC codes, cover art
- Song.link: Cross-platform streaming URLs
- Last.fm: Optional social features (can be disabled)
- Caching: 12-hour intelligent caching with rate limiting
"""

import logging
from typing import Any

from swingmusic.services.cached_spotify_client import get_cached_spotify_client
from swingmusic.services.musicbrainz_client import get_musicbrainz_client
from swingmusic.services.songlink_client import get_songlink_client

logger = logging.getLogger(__name__)


class UnifiedMetadataClient:
    """
    Unified metadata client that combines multiple music services with intelligent caching.

    Core Services (Always Available):
    - Spotify Web Player API: Primary metadata source (cached for 12 hours)
    - MusicBrainz: Genre enrichment and ISRC matching
    - Song.link: Cross-platform streaming URLs

    Optional Services (User Configurable):
    - Last.fm: Social features and scrobbling

    Features:
    - Rate limiting (2 second intervals, 1000/hour max)
    - 12-hour caching with DragonflyDB/SQLite
    - Protection against Spotify API bans
    - Fast response times for cached data
    """

    def __init__(self, enable_lastfm: bool = False, cache_duration_hours: int = 12):
        """Initialize unified client with optional services and caching"""
        # Core services (always available with caching)
        self.spotify = get_cached_spotify_client(cache_duration_hours)
        self.musicbrainz = get_musicbrainz_client()
        self.songlink = get_songlink_client()

        # Optional services
        self.enable_lastfm = enable_lastfm
        self.lastfm = None

        if enable_lastfm:
            try:
                from swingmusic.plugins.lastfm import LastFmPlugin

                # Note: This would need user configuration
                self.lastfm = LastFmPlugin(
                    current_userid=1
                )  # Would need proper user ID
                if not self.lastfm.active:
                    self.lastfm = None
                    logger.warning("Last.fm not configured, disabling")
            except Exception as e:
                logger.warning(f"Failed to initialize Last.fm: {e}")
                self.lastfm = None

        logger.info(
            f"Unified client initialized (cache: {cache_duration_hours}h, lastfm: {enable_lastfm})"
        )

    def get_track_with_enrichment(self, track_id: str) -> dict[str, Any]:
        """
        Get comprehensive track data with enrichment from multiple sources.
        Uses intelligent caching for fast response times.

        Returns:
            {
                "spotify_id": str,
                "name": str,
                "artists": list,
                "album": dict,
                "duration_ms": int,
                "play_count": int,           # From Spotify (cached)
                "popularity": int,            # From Spotify (not available in Web Player API)
                "genres": list[str],          # From MusicBrainz
                "isrc": str | None,           # From Spotify/MusicBrainz
                "cover_art": str | None,      # From MusicBrainz
                "streaming_urls": dict,       # From Song.link
                "lastfm_stats": dict | None,  # Optional: From Last.fm
                "cached": bool,               # Whether data was from cache
            }
        """
        result = {"cached": False}

        # 1. Get core data from Spotify (with caching)
        spotify_track = self.spotify.get_track(track_id)
        if not spotify_track:
            logger.error(f"Failed to get Spotify data for track {track_id}")
            return {}

        # Mark if data was cached (very fast response)
        # This is handled internally by the cached client

        # Extract Spotify data
        result.update(
            {
                "spotify_id": spotify_track.id,
                "name": spotify_track.name,
                "artists": spotify_track.artists,
                "album": spotify_track.album,
                "duration_ms": spotify_track.duration_ms,
                "play_count": getattr(
                    spotify_track, "playcount", 0
                ),  # Real Spotify play count
                "popularity": getattr(
                    spotify_track, "popularity", 0
                ),  # Not available in Web Player API
                "explicit": spotify_track.explicit,
                "preview_url": spotify_track.preview_url,
            }
        )

        # 2. Enrich with MusicBrainz data (if ISRC available)
        isrc = getattr(spotify_track, "isrc", None)
        if isrc:
            try:
                mb_recording = self.musicbrainz.get_by_isrc(isrc)
                if mb_recording:
                    result.update(
                        {
                            "genres": mb_recording.genres or [],
                            "isrc": mb_recording.isrc,
                            "cover_art": mb_recording.cover_art,
                            "release_date": mb_recording.release_date,
                            "country": mb_recording.country,
                            "tags": mb_recording.tags or [],
                        }
                    )
            except Exception as e:
                logger.debug(f"MusicBrainz enrichment failed: {e}")

        # 3. Add cross-platform streaming URLs (rate limited)
        try:
            cross_platform = self.songlink.get_links_from_spotify_id(track_id)
            if cross_platform:
                result["streaming_urls"] = {
                    "tidal": cross_platform.tidal_url,
                    "qobuz": cross_platform.qobuz_url,
                    "amazon": cross_platform.amazon_url,
                    "deezer": cross_platform.deezer_url,
                    "apple": cross_platform.apple_url,
                    "youtube": cross_platform.youtube_url,
                    "youtube_music": cross_platform.youtube_music_url,
                }
        except Exception as e:
            logger.debug(f"Song.link enrichment failed: {e}")

        # 4. Add Last.fm stats (optional)
        if self.lastfm and self.lastfm.active:
            try:
                # Get Last.fm play count and stats
                track_name = result["name"]
                artist_name = result["artists"][0]["name"] if result["artists"] else ""

                if track_name and artist_name:
                    lastfm_data = self.lastfm.get_track_info(artist_name, track_name)
                    if lastfm_data:
                        result["lastfm_stats"] = {
                            "playcount": lastfm_data.get("playcount", 0),
                            "listeners": lastfm_data.get("listeners", 0),
                            "userplaycount": lastfm_data.get("userplaycount", 0),
                            "loved": lastfm_data.get("userloved", 0),
                        }
            except Exception as e:
                logger.debug(f"Last.fm enrichment failed: {e}")

        return result

    def get_album_with_enrichment(self, album_id: str) -> dict[str, Any]:
        """Get album data with enrichment (cached)"""
        result = {}

        # Get core album data from Spotify (with caching)
        spotify_album = self.spotify.get_album(album_id)
        if not spotify_album:
            return {}

        result.update(
            {
                "spotify_id": spotify_album.id,
                "name": spotify_album.name,
                "artists": spotify_album.artists,
                "total_tracks": spotify_album.total_tracks,
                "release_date": spotify_album.release_date,
                "album_type": spotify_album.album_type,
                "images": spotify_album.images,
            }
        )

        # Enrich with MusicBrainz if we have artist info
        if spotify_album.artists:
            artist_name = spotify_album.artists[0].get("name", "")
            if artist_name:
                try:
                    mb_artist = self.musicbrainz.search_artist(artist_name, limit=1)
                    if mb_artist:
                        result["musicbrainz_artist"] = {
                            "mbid": mb_artist.mbid,
                            "genres": mb_artist.genres or [],
                            "country": mb_artist.country,
                            "rating": mb_artist.rating,
                        }
                except Exception as e:
                    logger.debug(f"MusicBrainz artist enrichment failed: {e}")

        return result

    def get_artist_with_enrichment(self, artist_id: str) -> dict[str, Any]:
        """Get artist data with enrichment (cached)"""
        result = {}

        # Get core artist data from Spotify (with caching)
        spotify_artist = self.spotify.get_artist(artist_id)
        if not spotify_artist:
            return {}

        result.update(
            {
                "spotify_id": spotify_artist.id,
                "name": spotify_artist.name,
                "followers": spotify_artist.followers,
                "popularity": spotify_artist.popularity,
                "genres": spotify_artist.genres or [],
                "images": spotify_artist.images,
            }
        )

        # Enrich with MusicBrainz
        try:
            mb_artist = self.musicbrainz.search_artist(spotify_artist.name, limit=1)
            if mb_artist:
                result["musicbrainz_data"] = {
                    "mbid": mb_artist.mbid,
                    "sort_name": mb_artist.sort_name,
                    "country": mb_artist.country,
                    "life_span": mb_artist.life_span,
                    "tags": mb_artist.tags or [],
                    "rating": mb_artist.rating,
                }

                # Merge genres from both sources
                spotify_genres = result.get("genres", []) or []
                mb_genres = mb_artist.genres or []
                combined_genres = list(set(spotify_genres + mb_genres))
                result["genres"] = combined_genres
        except Exception as e:
            logger.debug(f"MusicBrainz artist enrichment failed: {e}")

        return result

    def search_with_enrichment(
        self, query: str, search_type: str = "track"
    ) -> dict[str, Any]:
        """Search with enrichment from multiple sources (cached)"""
        results = {"spotify": [], "enriched": []}

        # Search Spotify (with rate limiting)
        try:
            if search_type == "track":
                spotify_results = self.spotify.search(query, "track", limit=20)
                results["spotify"] = spotify_results.get("tracks", [])
            elif search_type == "album":
                spotify_results = self.spotify.search(query, "album", limit=20)
                results["spotify"] = spotify_results.get("albums", [])
            elif search_type == "artist":
                spotify_results = self.spotify.search(query, "artist", limit=20)
                results["spotify"] = spotify_results.get("artists", [])
        except Exception as e:
            logger.error(f"Spotify search failed: {e}")
            results["spotify"] = []

        # Enrich top results with additional data (cached)
        try:
            for item in results["spotify"][:5]:  # Enrich top 5 results
                if search_type == "track" and item.get("id"):
                    enriched = self.get_track_with_enrichment(item["id"])
                    results["enriched"].append(enriched)
                elif search_type == "album" and item.get("id"):
                    enriched = self.get_album_with_enrichment(item["id"])
                    results["enriched"].append(enriched)
                elif search_type == "artist" and item.get("id"):
                    enriched = self.get_artist_with_enrichment(item["id"])
                    results["enriched"].append(enriched)
        except Exception as e:
            logger.error(f"Enrichment failed: {e}")

        return results

    def get_cache_stats(self) -> dict[str, Any]:
        """Get comprehensive cache and service statistics"""
        stats = self.spotify.get_cache_stats()

        stats.update(
            {
                "musicbrainz_available": self.musicbrainz is not None,
                "songlink_available": self.songlink is not None,
                "lastfm_enabled": self.enable_lastfm,
                "lastfm_active": self.lastfm is not None and self.lastfm.active
                if self.lastfm
                else False,
            }
        )

        return stats

    def cleanup_cache(self) -> int:
        """Clean up expired cache entries"""
        return self.spotify.cleanup_cache()

    def preload_popular_tracks(self, track_ids: list[str]) -> dict[str, bool]:
        """Preload popular tracks to cache for faster startup"""
        return self.spotify.preload_popular_tracks(track_ids)


# Singleton instance for easy access
_unified_client: UnifiedMetadataClient | None = None


def get_unified_metadata_client(
    enable_lastfm: bool = False, cache_duration_hours: int = 12
) -> UnifiedMetadataClient:
    """Get or create the unified metadata client"""
    global _unified_client
    if _unified_client is None:
        _unified_client = UnifiedMetadataClient(
            enable_lastfm=enable_lastfm, cache_duration_hours=cache_duration_hours
        )
    return _unified_client

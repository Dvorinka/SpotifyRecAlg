"""
Spotify Metadata Client for SwingMusic
Handles fetching metadata from Spotify for catalog browsing and downloads

UPDATED: Now uses Spotify Web Player API (NO ACCOUNT REQUIRED)
Based on SpotiFLAC approach - reverse-engineered Web Player authentication

This replaces the deprecated Spotify Web API which now requires Premium subscription.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from swingmusic.logger import log as logger

# Import the new Web Player client (no account required)
from swingmusic.services.spotify_web_player_client import (
    SpotifyWebPlayerClient,
    get_spotify_web_player_client,
)


@dataclass
class SpotifyTrack:
    """Spotify track metadata"""

    id: str
    name: str
    artists: list[dict[str, Any]]
    album: dict[str, Any]
    duration_ms: int
    popularity: int
    preview_url: str | None
    explicit: bool
    external_urls: dict[str, str]
    track_number: int
    disc_number: int
    available_markets: list[str]


@dataclass
class SpotifyAlbum:
    """Spotify album metadata"""

    id: str
    name: str
    artists: list[dict[str, Any]]
    release_date: str
    total_tracks: int
    popularity: int
    images: list[dict[str, str]]
    external_urls: dict[str, str]
    available_markets: list[str]
    album_type: str  # album, single, compilation
    tracks: list[dict[str, Any]] = field(default_factory=list)  # Track list


@dataclass
class SpotifyArtist:
    """Spotify artist metadata"""

    id: str
    name: str
    popularity: int
    followers: dict[str, int]
    genres: list[str]
    images: list[dict[str, str]]
    external_urls: dict[str, str]


@dataclass
class SpotifyPlaylist:
    """Spotify playlist metadata"""

    id: str
    name: str
    description: str | None
    owner: dict[str, Any]
    public: bool
    collaborative: bool
    tracks: dict[str, Any]  # Contains href, total, limit
    images: list[dict[str, str]]
    external_urls: dict[str, str]


class SpotifyMetadataClient:
    """
    Client for accessing Spotify metadata - NO ACCOUNT REQUIRED

    Uses the Spotify Web Player API (reverse-engineered) which doesn't require
    any authentication or Premium subscription. This is the same approach used
    by SpotiFLAC and other open-source tools.

    The old Spotify Web API is deprecated as it now requires Premium subscription.
    """

    def __init__(self):
        # Use the new Web Player client (no account required)
        self._web_player_client: SpotifyWebPlayerClient | None = None

        # Legacy API support (deprecated, requires Premium)
        self.client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        self.access_token = None
        self.token_expires_at = 0
        self.base_url = "https://api.spotify.com/v1"
        self.rate_limit_remaining = 0
        self.rate_limit_reset = 0

        # Always use Web Player client (no account needed)
        self.use_demo_mode = False
        self._use_web_player = True

        # Use local logger if global logger is not available
        local_logger = logger or logging.getLogger(__name__)
        local_logger.info(
            "SpotifyMetadataClient initialized with Web Player API (no account required)"
        )

    def _get_web_player_client(self) -> SpotifyWebPlayerClient:
        """Get or create the Web Player client"""
        if self._web_player_client is None:
            self._web_player_client = get_spotify_web_player_client()
        return self._web_player_client

    def _get_access_token(self) -> str | None:
        """Get access token - now using Web Player client (no account required)"""
        # Web Player client handles its own authentication
        # This method is kept for backward compatibility
        return "web_player_token"

    def _make_request(
        self, endpoint: str, params: dict[str, Any] = None
    ) -> dict[str, Any] | None:
        """
        Make request to Spotify - now using Web Player client (no account required)

        This method is kept for backward compatibility but routes through
        the Web Player client which doesn't require any authentication.
        """
        # Parse endpoint to determine what to fetch
        endpoint = endpoint.lstrip("/")

        client = self._get_web_player_client()

        # Handle track endpoints
        if endpoint.startswith("tracks/"):
            track_id = endpoint.split("/")[1]
            track = client.get_track(track_id)
            if track:
                return self._track_to_dict(track)
            return None

        # Handle album endpoints
        if endpoint.startswith("albums/"):
            parts = endpoint.split("/")
            album_id = parts[1]
            if len(parts) > 2 and parts[2] == "tracks":
                # Album tracks request
                album = client.get_album(album_id)
                if album:
                    return {"items": [self._track_to_dict(t) for t in album.tracks]}
            else:
                album = client.get_album(album_id)
                if album:
                    return self._album_to_dict(album)
            return None

        # Handle artist endpoints
        if endpoint.startswith("artists/"):
            parts = endpoint.split("/")
            artist_id = parts[1]
            if len(parts) > 2:
                sub_endpoint = parts[2]
                endpoint_map = {
                    "albums": {"items": []},
                    "top-tracks": {"tracks": []},
                    "related-artists": {"artists": []},
                }
                return endpoint_map.get(sub_endpoint)
            else:
                artist = client.get_artist(artist_id)
                if artist:
                    return self._artist_to_dict(artist)
            return None

        # Handle playlist endpoints
        if endpoint.startswith("playlists/"):
            parts = endpoint.split("/")
            playlist_id = parts[1]
            if len(parts) > 2 and parts[2] == "tracks":
                playlist = client.get_playlist(playlist_id)
                if playlist:
                    return {
                        "items": [
                            {"track": self._track_to_dict(t)} for t in playlist.tracks
                        ]
                    }
            else:
                playlist = client.get_playlist(playlist_id)
                if playlist:
                    return self._playlist_to_dict(playlist)
            return None

        # Handle search
        if endpoint == "search":
            query = params.get("q", "") if params else ""
            search_type = params.get("type", "track") if params else "track"
            # Search would need additional implementation
            logger.info(f"Search for '{query}' type={search_type}")
            return {
                "tracks": {"items": []},
                "albums": {"items": []},
                "artists": {"items": []},
            }

        logger.warning(f"Unknown endpoint: {endpoint}")
        return None

    def _track_to_dict(self, track) -> dict:
        """Convert SpotifyTrack to dict format expected by legacy code"""
        return {
            "id": track.id,
            "name": track.name,
            "artists": track.artists,
            "album": track.album,
            "duration_ms": track.duration_ms,
            "popularity": track.popularity,
            "preview_url": track.preview_url,
            "explicit": track.explicit,
            "external_urls": track.external_urls,
            "track_number": track.track_number,
            "disc_number": track.disc_number,
            "available_markets": [],
        }

    def _album_to_dict(self, album) -> dict:
        """Convert SpotifyAlbum to dict format"""
        return {
            "id": album.id,
            "name": album.name,
            "artists": album.artists,
            "release_date": str(album.release_date),
            "total_tracks": album.total_tracks,
            "popularity": 0,
            "images": album.images,
            "external_urls": album.external_urls,
            "available_markets": [],
            "album_type": album.album_type,
            "tracks": {"items": [self._track_to_dict(t) for t in album.tracks]},
        }

    def _artist_to_dict(self, artist) -> dict:
        """Convert SpotifyArtist to dict format"""
        return {
            "id": artist.id,
            "name": artist.name,
            "popularity": artist.popularity,
            "followers": {"total": artist.followers},
            "genres": artist.genres,
            "images": artist.images,
            "external_urls": artist.external_urls,
        }

    def _playlist_to_dict(self, playlist) -> dict:
        """Convert SpotifyPlaylist to dict format"""
        return {
            "id": playlist.id,
            "name": playlist.name,
            "description": playlist.description,
            "owner": playlist.owner,
            "public": False,
            "collaborative": False,
            "tracks": {"total": playlist.total_tracks},
            "images": playlist.images,
            "external_urls": playlist.external_urls,
        }

    def _demo_response(
        self, endpoint: str, params: dict[str, Any] = None
    ) -> dict[str, Any] | None:
        """DEPRECATED: Demo responses are no longer used - Web Player client provides real data"""
        logger.warning(f"Demo mode called but deprecated - endpoint: {endpoint}")
        return None

    def get_track(self, track_id: str) -> SpotifyTrack | None:
        """Get track by ID"""
        data = self._make_request(f"tracks/{track_id}")
        if not data:
            return None

        return SpotifyTrack(
            id=data["id"],
            name=data["name"],
            artists=data["artists"],
            album=data["album"],
            duration_ms=data["duration_ms"],
            popularity=data["popularity"],
            preview_url=data.get("preview_url"),
            explicit=data["explicit"],
            external_urls=data["external_urls"],
            track_number=data["track_number"],
            disc_number=data.get("disc_number", 1),
            available_markets=data.get("available_markets", []),
        )

    def get_album(self, album_id: str) -> SpotifyAlbum | None:
        """Get album by ID"""
        data = self._make_request(f"albums/{album_id}")
        if not data:
            return None

        return SpotifyAlbum(
            id=data["id"],
            name=data["name"],
            artists=data["artists"],
            release_date=data["release_date"],
            total_tracks=data["total_tracks"],
            popularity=data.get("popularity", 0),
            images=data["images"],
            external_urls=data["external_urls"],
            available_markets=data.get("available_markets", []),
            album_type=data["album_type"],
        )

    def get_album_tracks(
        self, album_id: str, limit: int = 50, offset: int = 0
    ) -> list[SpotifyTrack]:
        """Get tracks from album"""
        data = self._make_request(
            f"albums/{album_id}/tracks", {"limit": limit, "offset": offset}
        )

        if not data or "items" not in data:
            return []

        tracks = []
        for item in data["items"]:
            # Get full track details for each track
            track = self.get_track(item["id"])
            if track:
                tracks.append(track)

        return tracks

    def get_artist(self, artist_id: str) -> SpotifyArtist | None:
        """Get artist by ID"""
        data = self._make_request(f"artists/{artist_id}")
        if not data:
            return None

        return SpotifyArtist(
            id=data["id"],
            name=data["name"],
            popularity=data["popularity"],
            followers=data["followers"],
            genres=data["genres"],
            images=data["images"],
            external_urls=data["external_urls"],
        )

    def get_artist_albums(
        self,
        artist_id: str,
        limit: int = 20,
        include_groups: str = "album,single",
        offset: int = 0,
    ) -> list[SpotifyAlbum]:
        """Get artist albums"""
        albums = []
        page_offset = max(0, int(offset))
        remaining = max(1, int(limit))

        # Spotify API page size upper bound.
        while remaining > 0:
            page_size = min(50, remaining)
            data = self._make_request(
                f"artists/{artist_id}/albums",
                {
                    "limit": page_size,
                    "offset": page_offset,
                    "include_groups": include_groups,
                },
            )

            if not data or "items" not in data:
                break

            items = data["items"]
            if not items:
                break

            for item in items:
                album = SpotifyAlbum(
                    id=item["id"],
                    name=item["name"],
                    artists=item["artists"],
                    release_date=item["release_date"],
                    total_tracks=item["total_tracks"],
                    popularity=item.get("popularity", 0),
                    images=item["images"],
                    external_urls=item["external_urls"],
                    available_markets=item.get("available_markets", []),
                    album_type=item["album_type"],
                )
                albums.append(album)

            fetched = len(items)
            remaining -= fetched
            page_offset += fetched

            # Last page reached.
            if fetched < page_size:
                break

        return albums

    def get_artist_top_tracks(
        self, artist_id: str, market: str = "US"
    ) -> list[SpotifyTrack]:
        """Get artist's top tracks"""
        data = self._make_request(f"artists/{artist_id}/top-tracks", {"market": market})

        if not data or "tracks" not in data:
            return []

        tracks = []
        for item in data["tracks"]:
            track = SpotifyTrack(
                id=item["id"],
                name=item["name"],
                artists=item["artists"],
                album=item["album"],
                duration_ms=item["duration_ms"],
                popularity=item["popularity"],
                preview_url=item.get("preview_url"),
                explicit=item["explicit"],
                external_urls=item["external_urls"],
                track_number=item.get("track_number", 1),
                disc_number=item.get("disc_number", 1),
                available_markets=item.get("available_markets", []),
            )
            tracks.append(track)

        return tracks

    def get_related_artists(self, artist_id: str) -> list[SpotifyArtist]:
        """Get related artists"""
        data = self._make_request(f"artists/{artist_id}/related-artists")

        if not data or "artists" not in data:
            return []

        artists = []
        for item in data["artists"]:
            artist = SpotifyArtist(
                id=item["id"],
                name=item["name"],
                popularity=item["popularity"],
                followers=item["followers"],
                genres=item["genres"],
                images=item["images"],
                external_urls=item["external_urls"],
            )
            artists.append(artist)

        return artists

    def get_playlist(self, playlist_id: str) -> SpotifyPlaylist | None:
        """Get playlist by ID"""
        data = self._make_request(f"playlists/{playlist_id}")
        if not data:
            return None

        return SpotifyPlaylist(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            owner=data.get("owner", {}),
            public=bool(data.get("public", False)),
            collaborative=bool(data.get("collaborative", False)),
            tracks=data.get("tracks", {}),
            images=data.get("images", []),
            external_urls=data.get("external_urls", {}),
        )

    def get_playlist_tracks(
        self,
        playlist_id: str,
        limit: int = 100,
        offset: int = 0,
        market: str = "US",
    ) -> list[SpotifyTrack]:
        """Get playlist tracks"""
        tracks: list[SpotifyTrack] = []
        page_offset = max(0, int(offset))
        remaining = max(1, int(limit))

        while remaining > 0:
            page_size = min(100, remaining)
            data = self._make_request(
                f"playlists/{playlist_id}/tracks",
                {
                    "limit": page_size,
                    "offset": page_offset,
                    "market": market,
                },
            )

            if not data or "items" not in data:
                break

            items = data["items"]
            if not items:
                break

            for item in items:
                track_data = item.get("track") if isinstance(item, dict) else None
                if not isinstance(track_data, dict):
                    continue

                track_id = track_data.get("id")
                if not track_id:
                    continue

                track = SpotifyTrack(
                    id=track_id,
                    name=track_data.get("name", ""),
                    artists=track_data.get("artists", []),
                    album=track_data.get("album", {}),
                    duration_ms=int(track_data.get("duration_ms") or 0),
                    popularity=int(track_data.get("popularity") or 0),
                    preview_url=track_data.get("preview_url"),
                    explicit=bool(track_data.get("explicit", False)),
                    external_urls=track_data.get("external_urls", {}),
                    track_number=int(track_data.get("track_number") or 0),
                    disc_number=int(track_data.get("disc_number") or 1),
                    available_markets=track_data.get("available_markets", []),
                )
                tracks.append(track)

            fetched = len(items)
            remaining -= fetched
            page_offset += fetched

            if fetched < page_size:
                break

        return tracks

    def search(
        self,
        query: str,
        search_type: str = "track",
        limit: int = 20,
        offset: int = 0,
        market: str = "US",
    ) -> dict[str, list]:
        """Search for content"""
        types = (
            search_type
            if search_type in ["track", "album", "artist", "playlist"]
            else "track"
        )

        data = self._make_request(
            "search",
            {
                "q": query,
                "type": types,
                "limit": limit,
                "offset": offset,
                "market": market,
            },
        )

        if not data:
            return {"tracks": [], "albums": [], "artists": [], "playlists": []}

        result = {"tracks": [], "albums": [], "artists": [], "playlists": []}

        # Process tracks
        if "tracks" in data and "items" in data["tracks"]:
            for item in data["tracks"]["items"]:
                track = SpotifyTrack(
                    id=item["id"],
                    name=item["name"],
                    artists=item["artists"],
                    album=item["album"],
                    duration_ms=item["duration_ms"],
                    popularity=item["popularity"],
                    preview_url=item.get("preview_url"),
                    explicit=item["explicit"],
                    external_urls=item["external_urls"],
                    track_number=item.get("track_number", 1),
                    disc_number=item.get("disc_number", 1),
                    available_markets=item.get("available_markets", []),
                )
                result["tracks"].append(track)

        # Process albums
        if "albums" in data and "items" in data["albums"]:
            for item in data["albums"]["items"]:
                album = SpotifyAlbum(
                    id=item["id"],
                    name=item["name"],
                    artists=item["artists"],
                    release_date=item["release_date"],
                    total_tracks=item["total_tracks"],
                    popularity=item.get("popularity", 0),
                    images=item["images"],
                    external_urls=item["external_urls"],
                    available_markets=item.get("available_markets", []),
                    album_type=item["album_type"],
                )
                result["albums"].append(album)

        # Process artists
        if "artists" in data and "items" in data["artists"]:
            for item in data["artists"]["items"]:
                artist = SpotifyArtist(
                    id=item["id"],
                    name=item["name"],
                    popularity=item["popularity"],
                    followers=item["followers"],
                    genres=item["genres"],
                    images=item["images"],
                    external_urls=item["external_urls"],
                )
                result["artists"].append(artist)

        # Process playlists
        if "playlists" in data and "items" in data["playlists"]:
            for item in data["playlists"]["items"]:
                playlist = SpotifyPlaylist(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description"),
                    owner=item["owner"],
                    public=item.get("public", False),
                    collaborative=item.get("collaborative", False),
                    tracks=item["tracks"],
                    images=item.get("images", []),
                    external_urls=item["external_urls"],
                )
                result["playlists"].append(playlist)

        return result


# Global instance - lazy initialization
spotify_metadata_client = None


def get_spotify_metadata_client():
    """Get or create the Spotify metadata client instance"""
    global spotify_metadata_client
    if spotify_metadata_client is None:
        spotify_metadata_client = SpotifyMetadataClient()
    return spotify_metadata_client

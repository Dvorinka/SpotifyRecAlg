"""
Music Catalog Service for SwingMusic
Provides Spotify-like browsing of global music catalog with download capabilities
"""

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import aiohttp

from swingmusic import logger
from swingmusic.settings import Paths


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(Paths().app_db_path)
    try:
        yield conn
    finally:
        conn.close()


class CatalogItemType(Enum):
    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"


@dataclass
class CatalogItem:
    """Represents an item in the global music catalog"""

    spotify_id: str
    item_type: CatalogItemType
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    popularity: int | None = None
    preview_url: str | None = None
    image_url: str | None = None
    release_date: str | None = None
    explicit: bool = False
    data: dict[str, Any] | None = None
    cached_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass
class ArtistInfo:
    """Extended artist information with top tracks"""

    spotify_id: str
    name: str
    image_url: str | None = None
    followers: int | None = None
    popularity: int | None = None
    genres: list[str] | None = None
    top_tracks: list[CatalogItem] | None = None
    albums: list[CatalogItem] | None = None
    related_artists: list[dict] | None = None


@dataclass
class SearchResult:
    """Global search result across all content types"""

    tracks: list[CatalogItem]
    albums: list[CatalogItem]
    artists: list[CatalogItem]
    playlists: list[CatalogItem]
    total: int
    query: str


class MusicCatalogService:
    """Service for managing global music catalog with caching"""

    def __init__(self):
        self.cache_ttl = 3600  # 1 hour default cache TTL
        self.max_top_tracks = 15
        # Discography should be effectively complete; configurable for resource control.
        self.max_albums_per_artist = max(
            20, min(int(os.getenv("SWINGMUSIC_MAX_ARTIST_DISCOGRAPHY", "200")), 500)
        )
        self.session = None

    async def _get_session(self):
        """Get or create aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    def _get_spotify_client(self):
        """Get Spotify metadata client"""
        try:
            from swingmusic.services.spotify_metadata_client import (
                get_spotify_metadata_client,
            )

            return get_spotify_metadata_client()
        except ImportError:
            logger.warning("Spotify metadata client not available for catalog service")
            return None

    @staticmethod
    def _serialize_catalog_item(item: CatalogItem) -> dict[str, Any]:
        payload = asdict(item)
        item_type = payload.get("item_type")
        if isinstance(item_type, Enum):
            payload["item_type"] = item_type.value
        return payload

    @staticmethod
    def _deserialize_catalog_item(payload: dict[str, Any]) -> CatalogItem:
        item_type = payload.get("item_type")
        if isinstance(item_type, str):
            try:
                payload = {**payload, "item_type": CatalogItemType(item_type)}
            except ValueError:
                payload = {**payload, "item_type": CatalogItemType.TRACK}
        return CatalogItem(**payload)

    async def get_artist_top_tracks(
        self, artist_id: str, limit: int = 15
    ) -> list[CatalogItem]:
        """
        Get artist's most popular tracks

        Args:
            artist_id: Spotify artist ID
            limit: Maximum number of tracks to return

        Returns:
            List of popular tracks
        """
        try:
            # Check cache first
            cached_tracks = await self._get_cached_artist_top_tracks(artist_id, limit)
            if cached_tracks:
                return cached_tracks

            # Fetch from Spotify API
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            # This would integrate with the existing Spotify metadata client
            # For now, return empty list - integration point
            tracks_data = await self._fetch_artist_top_tracks_from_spotify(
                artist_id, limit
            )

            # Cache the results
            await self._cache_artist_top_tracks(artist_id, tracks_data)

            return tracks_data

        except Exception as e:
            logger.error(f"Error getting artist top tracks: {e}")
            return []

    async def get_artist_discography(self, artist_id: str) -> list[CatalogItem]:
        """
        Get complete artist discography with albums

        Args:
            artist_id: Spotify artist ID

        Returns:
            List of artist albums
        """
        try:
            # Check cache first
            cached_albums = await self._get_cached_artist_albums(artist_id)
            if cached_albums:
                return cached_albums

            # Fetch from Spotify API
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            albums_data = await self._fetch_artist_albums_from_spotify(artist_id)

            # Cache the results
            await self._cache_artist_albums(artist_id, albums_data)

            return albums_data

        except Exception as e:
            logger.error(f"Error getting artist discography: {e}")
            return []

    async def get_album_details(self, album_id: str) -> CatalogItem | None:
        """
        Get full album information with tracklist

        Args:
            album_id: Spotify album ID

        Returns:
            Album details with tracklist
        """
        try:
            # Check cache first
            cached_album = await self._get_cached_album(album_id)
            if cached_album:
                return cached_album

            # Fetch from Spotify API
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return None

            album_data = await self._fetch_album_details_from_spotify(album_id)

            # Cache the result
            await self._cache_album(album_id, album_data)

            return album_data

        except Exception as e:
            logger.error(f"Error getting album details: {e}")
            return None

    async def get_playlist_details(
        self, playlist_id: str, limit: int = 200
    ) -> CatalogItem | None:
        """
        Get full playlist information with tracklist

        Args:
            playlist_id: Spotify playlist ID
            limit: Maximum number of tracks to include

        Returns:
            Playlist details with tracks
        """
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return None

            playlist = spotify_client.get_playlist(playlist_id)
            if not playlist:
                return None

            tracks = spotify_client.get_playlist_tracks(
                playlist_id,
                limit=min(max(int(limit), 1), 300),
            )

            track_items: list[dict[str, Any]] = []
            for track in tracks:
                track_items.append(
                    {
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
                    }
                )

            owner = playlist.owner or {}
            owner_name = (
                owner.get("display_name", "") if isinstance(owner, dict) else ""
            )

            return CatalogItem(
                spotify_id=playlist.id,
                item_type=CatalogItemType.PLAYLIST,
                title=playlist.name,
                artist=owner_name,
                popularity=0,
                image_url=playlist.images[0]["url"] if playlist.images else None,
                explicit=False,
                data={
                    "description": playlist.description,
                    "owner": owner,
                    "public": playlist.public,
                    "collaborative": playlist.collaborative,
                    "tracks_total": (playlist.tracks or {}).get("total"),
                    "external_urls": playlist.external_urls,
                    "tracks": track_items,
                },
            )
        except Exception as e:
            logger.error(f"Error getting playlist details: {e}")
            return None

    async def search_global_catalog(
        self, query: str, item_type: str = "all", limit: int = 20
    ) -> SearchResult:
        """
        Search across all music types in global catalog

        Args:
            query: Search query
            item_type: Type of content to search (all, tracks, albums, artists, playlists)
            limit: Maximum results per type

        Returns:
            Search results across specified types
        """
        try:
            # Check cache first
            cache_key = f"search:{query}:{item_type}:{limit}"
            cached_result = await self._get_cached_search(cache_key)
            if cached_result:
                return cached_result

            # Search different types based on request
            tracks = []
            albums = []
            artists = []
            playlists = []

            spotify_client = self._get_spotify_client()
            if spotify_client:
                if item_type in ["all", "tracks"]:
                    tracks = await self._search_tracks(query, limit)
                if item_type in ["all", "albums"]:
                    albums = await self._search_albums(query, limit)
                if item_type in ["all", "artists"]:
                    artists = await self._search_artists(query, limit)
                if item_type in ["all", "playlists"]:
                    playlists = await self._search_playlists(query, limit)

            result = SearchResult(
                tracks=tracks,
                albums=albums,
                artists=artists,
                playlists=playlists,
                total=len(tracks) + len(albums) + len(artists) + len(playlists),
                query=query,
            )

            # Cache the search result
            await self._cache_search(cache_key, result)

            return result

        except Exception as e:
            logger.error(f"Error searching global catalog: {e}")
            return SearchResult([], [], [], [], 0, query)

    async def get_artist_info(self, artist_id: str) -> ArtistInfo | None:
        """
        Get comprehensive artist information including top tracks and albums

        Args:
            artist_id: Spotify artist ID

        Returns:
            Complete artist information
        """
        try:
            # Check cache first
            cached_info = await self._get_cached_artist_info(artist_id)
            if cached_info:
                return cached_info

            # Fetch all artist data concurrently
            top_tracks_task = self.get_artist_top_tracks(artist_id, self.max_top_tracks)
            albums_task = self.get_artist_discography(artist_id)
            basic_info_task = self._get_artist_basic_info(artist_id)

            top_tracks, albums, basic_info = await asyncio.gather(
                top_tracks_task, albums_task, basic_info_task, return_exceptions=True
            )

            if isinstance(basic_info, Exception):
                logger.error(f"Error getting basic artist info: {basic_info}")
                return None

            artist_info = ArtistInfo(
                spotify_id=artist_id,
                name=basic_info.get("name", ""),
                image_url=basic_info.get("image_url"),
                followers=basic_info.get("followers"),
                popularity=basic_info.get("popularity"),
                genres=basic_info.get("genres", []),
                top_tracks=top_tracks if not isinstance(top_tracks, Exception) else [],
                albums=albums if not isinstance(albums, Exception) else [],
                related_artists=basic_info.get("related_artists", []),
            )

            # Cache the complete artist info
            await self._cache_artist_info(artist_id, artist_info)

            return artist_info

        except Exception as e:
            logger.error(f"Error getting artist info: {e}")
            return None

    # Cache management methods
    async def _get_cached_artist_top_tracks(
        self, artist_id: str, limit: int
    ) -> list[CatalogItem] | None:
        """Get cached top tracks for artist"""
        try:
            with get_db_connection() as conn:
                query = """
                SELECT data FROM global_catalog_cache
                WHERE spotify_id = ? AND item_type = 'artist_top_tracks'
                AND expires_at > datetime('now')
                ORDER BY cached_at DESC LIMIT 1
                """
                cursor = conn.execute(query, (artist_id,))
                row = cursor.fetchone()

                if row:
                    data = json.loads(row[0])
                    return [
                        self._deserialize_catalog_item(item)
                        for item in data.get("tracks", [])[:limit]
                    ]

        except Exception as e:
            logger.error(f"Error getting cached artist top tracks: {e}")

        return None

    async def _cache_artist_top_tracks(self, artist_id: str, tracks: list[CatalogItem]):
        """Cache artist top tracks"""
        try:
            expires_at = datetime.now() + timedelta(seconds=self.cache_ttl)

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_catalog_cache
                    (spotify_id, item_type, title, artist, explicit, data, cached_at, expires_at)
                    VALUES (?, 'artist_top_tracks', ?, ?, 0, ?, datetime('now'), ?)
                """,
                    (
                        artist_id,
                        f"Top tracks for {artist_id}",
                        "",
                        json.dumps(
                            {
                                "tracks": [
                                    self._serialize_catalog_item(track)
                                    for track in tracks
                                ]
                            }
                        ),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Error caching artist top tracks: {e}")

    async def _get_cached_artist_albums(
        self, artist_id: str
    ) -> list[CatalogItem] | None:
        """Get cached albums for artist"""
        try:
            with get_db_connection() as conn:
                query = """
                SELECT data FROM global_catalog_cache
                WHERE spotify_id = ? AND item_type = 'artist_albums'
                AND expires_at > datetime('now')
                ORDER BY cached_at DESC LIMIT 1
                """
                cursor = conn.execute(query, (artist_id,))
                row = cursor.fetchone()

                if row:
                    data = json.loads(row[0])
                    return [
                        self._deserialize_catalog_item(item)
                        for item in data.get("albums", [])
                    ]

        except Exception as e:
            logger.error(f"Error getting cached artist albums: {e}")

        return None

    async def _cache_artist_albums(self, artist_id: str, albums: list[CatalogItem]):
        """Cache artist albums"""
        try:
            expires_at = datetime.now() + timedelta(seconds=self.cache_ttl)

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_catalog_cache
                    (spotify_id, item_type, title, artist, explicit, data, cached_at, expires_at)
                    VALUES (?, 'artist_albums', ?, ?, 0, ?, datetime('now'), ?)
                """,
                    (
                        artist_id,
                        f"Albums for {artist_id}",
                        "",
                        json.dumps(
                            {
                                "albums": [
                                    self._serialize_catalog_item(album)
                                    for album in albums
                                ]
                            }
                        ),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Error caching artist albums: {e}")

    async def _get_cached_album(self, album_id: str) -> CatalogItem | None:
        """Get cached album details"""
        try:
            with get_db_connection() as conn:
                query = """
                SELECT * FROM global_catalog_cache
                WHERE spotify_id = ? AND item_type = 'album'
                AND expires_at > datetime('now')
                ORDER BY cached_at DESC LIMIT 1
                """
                cursor = conn.execute(query, (album_id,))
                row = cursor.fetchone()

                if row:
                    return CatalogItem(
                        spotify_id=row[1],
                        item_type=CatalogItemType(row[2]),
                        title=row[3],
                        artist=row[4],
                        album=row[5],
                        duration_ms=row[6],
                        popularity=row[7],
                        preview_url=row[8],
                        image_url=row[9],
                        release_date=row[10],
                        explicit=bool(row[11]),
                        data=json.loads(row[12]) if row[12] else None,
                    )

        except Exception as e:
            logger.error(f"Error getting cached album: {e}")

        return None

    async def _cache_album(self, album_id: str, album: CatalogItem):
        """Cache album details"""
        try:
            expires_at = datetime.now() + timedelta(seconds=self.cache_ttl)

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_catalog_cache
                    (spotify_id, item_type, title, artist, album, duration_ms,
                     popularity, preview_url, image_url, release_date, explicit, data, cached_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                """,
                    (
                        album.spotify_id,
                        album.item_type.value,
                        album.title,
                        album.artist,
                        album.album,
                        album.duration_ms,
                        album.popularity,
                        album.preview_url,
                        album.image_url,
                        album.release_date,
                        album.explicit,
                        json.dumps(album.data) if album.data else None,
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Error caching album: {e}")

    async def _get_cached_search(self, cache_key: str) -> SearchResult | None:
        """Get cached search results"""
        try:
            with get_db_connection() as conn:
                query = """
                SELECT data FROM global_catalog_cache
                WHERE spotify_id = ? AND item_type = 'search'
                AND expires_at > datetime('now')
                ORDER BY cached_at DESC LIMIT 1
                """
                cursor = conn.execute(query, (cache_key,))
                row = cursor.fetchone()

                if row:
                    data = json.loads(row[0])
                    return SearchResult(
                        tracks=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("tracks", [])
                        ],
                        albums=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("albums", [])
                        ],
                        artists=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("artists", [])
                        ],
                        playlists=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("playlists", [])
                        ],
                        total=data.get("total", 0),
                        query=data.get("query", ""),
                    )

        except Exception as e:
            logger.error(f"Error getting cached search: {e}")

        return None

    async def _cache_search(self, cache_key: str, result: SearchResult):
        """Cache search results"""
        try:
            expires_at = datetime.now() + timedelta(
                seconds=self.cache_ttl // 2
            )  # Shorter cache for searches

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_catalog_cache
                    (spotify_id, item_type, title, artist, explicit, data, cached_at, expires_at)
                    VALUES (?, 'search', ?, ?, 0, ?, datetime('now'), ?)
                """,
                    (
                        cache_key,
                        f"Search: {result.query}",
                        "",
                        json.dumps(
                            {
                                "tracks": [
                                    self._serialize_catalog_item(track)
                                    for track in result.tracks
                                ],
                                "albums": [
                                    self._serialize_catalog_item(album)
                                    for album in result.albums
                                ],
                                "artists": [
                                    self._serialize_catalog_item(artist)
                                    for artist in result.artists
                                ],
                                "playlists": [
                                    self._serialize_catalog_item(playlist)
                                    for playlist in result.playlists
                                ],
                                "total": result.total,
                                "query": result.query,
                            }
                        ),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Error caching search: {e}")

    async def _get_cached_artist_info(self, artist_id: str) -> ArtistInfo | None:
        """Get cached complete artist info"""
        try:
            with get_db_connection() as conn:
                query = """
                SELECT data FROM global_catalog_cache
                WHERE spotify_id = ? AND item_type = 'artist_info'
                AND expires_at > datetime('now')
                ORDER BY cached_at DESC LIMIT 1
                """
                cursor = conn.execute(query, (artist_id,))
                row = cursor.fetchone()

                if row:
                    data = json.loads(row[0])
                    return ArtistInfo(
                        spotify_id=data["spotify_id"],
                        name=data["name"],
                        image_url=data.get("image_url"),
                        followers=data.get("followers"),
                        popularity=data.get("popularity"),
                        genres=data.get("genres", []),
                        top_tracks=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("top_tracks", [])
                        ],
                        albums=[
                            self._deserialize_catalog_item(item)
                            for item in data.get("albums", [])
                        ],
                        related_artists=data.get("related_artists", []),
                    )

        except Exception as e:
            logger.error(f"Error getting cached artist info: {e}")

        return None

    async def _cache_artist_info(self, artist_id: str, artist_info: ArtistInfo):
        """Cache complete artist info"""
        try:
            expires_at = datetime.now() + timedelta(seconds=self.cache_ttl)

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_catalog_cache
                    (spotify_id, item_type, title, artist, explicit, data, cached_at, expires_at)
                    VALUES (?, 'artist_info', ?, ?, 0, ?, datetime('now'), ?)
                """,
                    (
                        artist_id,
                        f"Artist info: {artist_info.name}",
                        "",
                        json.dumps(
                            {
                                "spotify_id": artist_info.spotify_id,
                                "name": artist_info.name,
                                "image_url": artist_info.image_url,
                                "followers": artist_info.followers,
                                "popularity": artist_info.popularity,
                                "genres": artist_info.genres,
                                "top_tracks": [
                                    self._serialize_catalog_item(track)
                                    for track in artist_info.top_tracks or []
                                ],
                                "albums": [
                                    self._serialize_catalog_item(album)
                                    for album in artist_info.albums or []
                                ],
                                "related_artists": artist_info.related_artists or [],
                            }
                        ),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Error caching artist info: {e}")

    # Spotify API integration methods
    async def _fetch_artist_top_tracks_from_spotify(
        self, artist_id: str, limit: int
    ) -> list[CatalogItem]:
        """Fetch artist top tracks from Spotify API"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            tracks = spotify_client.get_artist_top_tracks(artist_id, market="US")

            catalog_items = []
            for track in tracks[:limit]:
                catalog_item = CatalogItem(
                    spotify_id=track.id,
                    item_type=CatalogItemType.TRACK,
                    title=track.name,
                    artist=", ".join([artist["name"] for artist in track.artists]),
                    album=track.album["name"] if track.album else None,
                    duration_ms=track.duration_ms,
                    popularity=track.popularity,
                    preview_url=track.preview_url,
                    image_url=track.album["images"][0]["url"]
                    if track.album and track.album.get("images")
                    else None,
                    explicit=track.explicit,
                    data={
                        "artists": track.artists,
                        "album": track.album,
                        "external_urls": track.external_urls,
                        "track_number": track.track_number,
                        "disc_number": track.disc_number,
                        "available_markets": track.available_markets,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error fetching artist top tracks from Spotify: {e}")
            return []

    async def _fetch_artist_albums_from_spotify(
        self, artist_id: str
    ) -> list[CatalogItem]:
        """Fetch artist albums from Spotify API"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            albums = spotify_client.get_artist_albums(
                artist_id, limit=self.max_albums_per_artist
            )

            catalog_items = []
            for album in albums:
                catalog_item = CatalogItem(
                    spotify_id=album.id,
                    item_type=CatalogItemType.ALBUM,
                    title=album.name,
                    artist=", ".join([artist["name"] for artist in album.artists]),
                    album=album.name,
                    popularity=album.popularity,
                    image_url=album.images[0]["url"] if album.images else None,
                    release_date=album.release_date,
                    explicit=False,  # Albums don't have explicit flag in API
                    data={
                        "artists": album.artists,
                        "total_tracks": album.total_tracks,
                        "external_urls": album.external_urls,
                        "available_markets": album.available_markets,
                        "album_type": album.album_type,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error fetching artist albums from Spotify: {e}")
            return []

    async def _fetch_album_details_from_spotify(
        self, album_id: str
    ) -> CatalogItem | None:
        """Fetch album details from Spotify API"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return None

            album = spotify_client.get_album(album_id)
            if not album:
                return None

            # Get album tracks
            tracks = spotify_client.get_album_tracks(album_id)

            catalog_item = CatalogItem(
                spotify_id=album.id,
                item_type=CatalogItemType.ALBUM,
                title=album.name,
                artist=", ".join([artist["name"] for artist in album.artists]),
                album=album.name,
                popularity=album.popularity,
                image_url=album.images[0]["url"] if album.images else None,
                release_date=album.release_date,
                explicit=False,
                data={
                    "artists": album.artists,
                    "total_tracks": album.total_tracks,
                    "external_urls": album.external_urls,
                    "available_markets": album.available_markets,
                    "album_type": album.album_type,
                    "tracks": [
                        {
                            "id": track.id,
                            "name": track.name,
                            "artists": track.artists,
                            "duration_ms": track.duration_ms,
                            "track_number": track.track_number,
                            "disc_number": track.disc_number,
                            "explicit": track.explicit,
                            "preview_url": track.preview_url,
                        }
                        for track in tracks
                    ],
                },
            )

            return catalog_item

        except Exception as e:
            logger.error(f"Error fetching album details from Spotify: {e}")
            return None

    async def _search_tracks(self, query: str, limit: int) -> list[CatalogItem]:
        """Search tracks in Spotify catalog"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            search_results = spotify_client.search(query, "track", limit=limit)

            catalog_items = []
            for track in search_results.get("tracks", []):
                catalog_item = CatalogItem(
                    spotify_id=track.id,
                    item_type=CatalogItemType.TRACK,
                    title=track.name,
                    artist=", ".join([artist["name"] for artist in track.artists]),
                    album=track.album["name"] if track.album else None,
                    duration_ms=track.duration_ms,
                    popularity=track.popularity,
                    preview_url=track.preview_url,
                    image_url=track.album["images"][0]["url"]
                    if track.album and track.album.get("images")
                    else None,
                    explicit=track.explicit,
                    data={
                        "artists": track.artists,
                        "album": track.album,
                        "external_urls": track.external_urls,
                        "track_number": track.track_number,
                        "disc_number": track.disc_number,
                        "available_markets": track.available_markets,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error searching tracks: {e}")
            return []

    async def _search_albums(self, query: str, limit: int) -> list[CatalogItem]:
        """Search albums in Spotify catalog"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            search_results = spotify_client.search(query, "album", limit=limit)

            catalog_items = []
            for album in search_results.get("albums", []):
                catalog_item = CatalogItem(
                    spotify_id=album.id,
                    item_type=CatalogItemType.ALBUM,
                    title=album.name,
                    artist=", ".join([artist["name"] for artist in album.artists]),
                    album=album.name,
                    popularity=album.popularity,
                    image_url=album.images[0]["url"] if album.images else None,
                    release_date=album.release_date,
                    explicit=False,
                    data={
                        "artists": album.artists,
                        "total_tracks": album.total_tracks,
                        "external_urls": album.external_urls,
                        "available_markets": album.available_markets,
                        "album_type": album.album_type,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error searching albums: {e}")
            return []

    async def _search_artists(self, query: str, limit: int) -> list[CatalogItem]:
        """Search artists in Spotify catalog"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            search_results = spotify_client.search(query, "artist", limit=limit)

            catalog_items = []
            for artist in search_results.get("artists", []):
                catalog_item = CatalogItem(
                    spotify_id=artist.id,
                    item_type=CatalogItemType.ARTIST,
                    title=artist.name,
                    artist=artist.name,
                    popularity=artist.popularity,
                    image_url=artist.images[0]["url"] if artist.images else None,
                    explicit=False,
                    data={
                        "followers": artist.followers,
                        "genres": artist.genres,
                        "external_urls": artist.external_urls,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error searching artists: {e}")
            return []

    async def _search_playlists(self, query: str, limit: int) -> list[CatalogItem]:
        """Search playlists in Spotify catalog"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return []

            search_results = spotify_client.search(query, "playlist", limit=limit)

            catalog_items = []
            for playlist in search_results.get("playlists", []):
                catalog_item = CatalogItem(
                    spotify_id=playlist.id,
                    item_type=CatalogItemType.PLAYLIST,
                    title=playlist.name,
                    artist=playlist.owner["display_name"] if playlist.owner else "",
                    popularity=0,  # Playlists don't have popularity
                    image_url=playlist.images[0]["url"] if playlist.images else None,
                    explicit=False,
                    data={
                        "description": playlist.description,
                        "owner": playlist.owner,
                        "public": playlist.public,
                        "collaborative": playlist.collaborative,
                        "tracks": playlist.tracks,
                        "external_urls": playlist.external_urls,
                    },
                )
                catalog_items.append(catalog_item)

            return catalog_items

        except Exception as e:
            logger.error(f"Error searching playlists: {e}")
            return []

    async def _get_artist_basic_info(self, artist_id: str) -> dict[str, Any] | None:
        """Get basic artist information from Spotify API"""
        try:
            spotify_client = self._get_spotify_client()
            if not spotify_client:
                return None

            artist = spotify_client.get_artist(artist_id)
            if not artist:
                return None

            # Get related artists
            related_artists = spotify_client.get_related_artists(artist_id)

            return {
                "name": artist.name,
                "image_url": artist.images[0]["url"] if artist.images else None,
                "followers": artist.followers.get("total", 0)
                if artist.followers
                else 0,
                "popularity": artist.popularity,
                "genres": artist.genres,
                "related_artists": [
                    {
                        "id": related.id,
                        "name": related.name,
                        "popularity": related.popularity,
                        "image_url": related.images[0]["url"]
                        if related.images
                        else None,
                    }
                    for related in related_artists[:10]  # Limit to 10 related artists
                ],
            }

        except Exception as e:
            logger.error(f"Error getting basic artist info: {e}")
            return None

    def cleanup_expired_cache(self):
        """Clean up expired cache entries"""
        try:
            with get_db_connection() as conn:
                conn.execute("""
                    DELETE FROM global_catalog_cache
                    WHERE expires_at < datetime('now')
                """)
                conn.commit()
                logger.info("Cleaned up expired catalog cache entries")

        except Exception as e:
            logger.error(f"Error cleaning up expired cache: {e}")


# Global instance
music_catalog_service = MusicCatalogService()

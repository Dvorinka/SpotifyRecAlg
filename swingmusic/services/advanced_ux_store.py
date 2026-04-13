"""Lightweight advanced UX helpers backed by existing in-memory stores."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from swingmusic.db.engine import DbEngine
from swingmusic.lib import searchlib
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore

DEFAULT_PREFERENCES = {
    "enable_personalization": True,
    "discovery_mode": "balanced",
    "prefer_local_library": True,
    "show_explicit_content": True,
}


def _track_to_item(track) -> dict[str, Any]:
    artists = getattr(track, "artists", []) or []
    artist_name = (
        artists[0].get("name")
        if artists and isinstance(artists[0], dict)
        else "Unknown Artist"
    )
    return {
        "id": track.trackhash,
        "type": "track",
        "title": track.title,
        "subtitle": artist_name,
        "album": track.album,
        "image": track.image,
        "play_count": int(getattr(track, "playcount", 0) or 0),
    }


def _artist_to_item(artist) -> dict[str, Any]:
    return {
        "id": artist.artisthash,
        "type": "artist",
        "title": artist.name,
        "subtitle": f"{int(getattr(artist, 'trackcount', 0) or 0)} tracks",
        "image": artist.image,
        "play_count": int(getattr(artist, "playcount", 0) or 0),
    }


def _album_to_item(album) -> dict[str, Any]:
    album_artists = getattr(album, "albumartists", []) or []
    artist_name = (
        album_artists[0].get("name")
        if album_artists and isinstance(album_artists[0], dict)
        else "Unknown Artist"
    )
    return {
        "id": album.albumhash,
        "type": "album",
        "title": album.title,
        "subtitle": artist_name,
        "image": album.image,
        "play_count": int(getattr(album, "playcount", 0) or 0),
    }


class AdvancedUXStore:
    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self):
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS ux_behavior_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        event_payload TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS ux_preferences (
                        user_id INTEGER PRIMARY KEY,
                        preferences_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

    def get_preferences(self, user_id: int) -> dict[str, Any]:
        with DbEngine.manager() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT preferences_json
                    FROM ux_preferences
                    WHERE user_id = :user_id
                    """
                    ),
                    {"user_id": int(user_id)},
                )
                .mappings()
                .first()
            )

        if not row:
            return DEFAULT_PREFERENCES.copy()

        try:
            decoded = json.loads(row["preferences_json"])
            if not isinstance(decoded, dict):
                return DEFAULT_PREFERENCES.copy()
            return {**DEFAULT_PREFERENCES, **decoded}
        except json.JSONDecodeError:
            return DEFAULT_PREFERENCES.copy()

    def update_preferences(self, user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_preferences(user_id)
        current.update({k: v for k, v in patch.items() if k in DEFAULT_PREFERENCES})

        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO ux_preferences (user_id, preferences_json, updated_at)
                    VALUES (:user_id, :preferences_json, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        preferences_json = excluded.preferences_json,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "user_id": int(user_id),
                    "preferences_json": json.dumps(current),
                },
            )

        return current

    def track_behavior(
        self, user_id: int, event_type: str, payload: dict[str, Any]
    ) -> bool:
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO ux_behavior_events (user_id, event_type, event_payload, created_at)
                    VALUES (:user_id, :event_type, :event_payload, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "user_id": int(user_id),
                    "event_type": str(event_type or "unknown"),
                    "event_payload": json.dumps(payload or {}),
                },
            )
        return True

    def get_behavior_profile(self, user_id: int) -> dict[str, Any]:
        with DbEngine.manager() as session:
            rows = (
                session.execute(
                    text(
                        """
                    SELECT event_type, event_payload, created_at
                    FROM ux_behavior_events
                    WHERE user_id = :user_id
                    ORDER BY id DESC
                    LIMIT 200
                    """
                    ),
                    {"user_id": int(user_id)},
                )
                .mappings()
                .all()
            )

        search_queries: list[str] = []
        event_counts: dict[str, int] = {}

        for row in rows:
            event_type = str(row["event_type"])
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            try:
                payload = json.loads(row["event_payload"])
            except json.JSONDecodeError:
                payload = {}

            if event_type in {"search", "search_query"}:
                query = payload.get("query") or payload.get("q")
                if isinstance(query, str) and query.strip():
                    search_queries.append(query.strip())

        top_artists = sorted(
            ArtistStore.get_flat_list(),
            key=lambda a: int(getattr(a, "playcount", 0) or 0),
            reverse=True,
        )[:10]
        top_genres = []
        genre_counter: dict[str, int] = {}
        for track in TrackStore.get_flat_list()[:5000]:
            genres = getattr(track, "genres", []) or []
            for entry in genres:
                name = entry.get("name") if isinstance(entry, dict) else entry
                if not isinstance(name, str):
                    continue
                normalized = name.strip().lower()
                if not normalized:
                    continue
                genre_counter[normalized] = genre_counter.get(normalized, 0) + 1

        top_genres = [
            name
            for name, _ in sorted(
                genre_counter.items(), key=lambda x: x[1], reverse=True
            )[:10]
        ]

        return {
            "user_id": int(user_id),
            "favorite_genres": top_genres,
            "favorite_artists": [artist.name for artist in top_artists],
            "listening_patterns": {
                "top_event_types": event_counts,
            },
            "download_preferences": {},
            "interaction_patterns": event_counts,
            "last_updated": rows[0]["created_at"] if rows else None,
            "search_history_count": len(search_queries),
            "recent_searches": search_queries[:20],
        }

    def search_suggestions(
        self, query: str, context: str, limit: int
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        limit = max(1, min(limit, 30))

        suggestions: list[dict[str, Any]] = []

        if not query:
            for track in sorted(
                TrackStore.get_flat_list(),
                key=lambda t: int(getattr(t, "playcount", 0) or 0),
                reverse=True,
            )[:limit]:
                suggestions.append(_track_to_item(track))
            return suggestions

        try:
            results = searchlib.TopResults().search(query, limit=max(limit, 5))
            if isinstance(results, dict):
                top = results.get("top_result")
                if isinstance(top, dict):
                    suggestions.append(
                        {
                            "id": top.get("trackhash")
                            or top.get("albumhash")
                            or top.get("artisthash")
                            or top.get("id"),
                            "type": top.get("type", "item"),
                            "title": top.get("title")
                            or top.get("name")
                            or "Top result",
                            "subtitle": top.get("artist") or top.get("album") or "",
                        }
                    )

                for key, item_type in (
                    ("tracks", "track"),
                    ("artists", "artist"),
                    ("albums", "album"),
                ):
                    for item in results.get(key) or []:
                        suggestions.append(
                            {
                                "id": item.get("trackhash")
                                or item.get("artisthash")
                                or item.get("albumhash")
                                or item.get("id"),
                                "type": item_type,
                                "title": item.get("title") or item.get("name") or "",
                                "subtitle": item.get("artist")
                                or item.get("album")
                                or "",
                                "image": item.get("image", ""),
                            }
                        )
        except Exception:
            pass

        seen = set()
        deduped = []
        for item in suggestions:
            key = (item.get("type"), item.get("id"), item.get("title"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break

        return deduped

    def get_recommendations(
        self, recommendation_type: str, limit: int
    ) -> list[dict[str, Any]]:
        recommendation_type = (recommendation_type or "mixed").lower()
        limit = max(1, min(limit, 50))

        tracks = sorted(
            TrackStore.get_flat_list(),
            key=lambda t: int(getattr(t, "playcount", 0) or 0),
            reverse=True,
        )
        artists = sorted(
            ArtistStore.get_flat_list(),
            key=lambda a: int(getattr(a, "playcount", 0) or 0),
            reverse=True,
        )
        albums = sorted(
            AlbumStore.get_flat_list(),
            key=lambda a: int(getattr(a, "playcount", 0) or 0),
            reverse=True,
        )

        if recommendation_type == "tracks":
            return [_track_to_item(track) for track in tracks[:limit]]

        if recommendation_type == "artists":
            return [_artist_to_item(artist) for artist in artists[:limit]]

        if recommendation_type == "albums":
            return [_album_to_item(album) for album in albums[:limit]]

        mixed: list[dict[str, Any]] = []
        for idx in range(limit):
            if idx < len(tracks):
                mixed.append(_track_to_item(tracks[idx]))
            if len(mixed) >= limit:
                break
            if idx < len(artists):
                mixed.append(_artist_to_item(artists[idx]))
            if len(mixed) >= limit:
                break
            if idx < len(albums):
                mixed.append(_album_to_item(albums[idx]))
            if len(mixed) >= limit:
                break

        return mixed[:limit]

    def get_contextual_suggestions(
        self, track_id: str, context_type: str, limit: int
    ) -> list[dict[str, Any]]:
        track_id = str(track_id or "").strip()
        context_type = str(context_type or "similar").lower()
        limit = max(1, min(limit, 30))

        if not track_id:
            return []

        track_list = TrackStore.get_tracks_by_trackhashes([track_id])
        if not track_list:
            return []

        base_track = track_list[0]
        suggestions: list[dict[str, Any]] = []

        if context_type == "album":
            for track in TrackStore.get_tracks_by_albumhash(base_track.albumhash):
                if track.trackhash == base_track.trackhash:
                    continue
                suggestions.append(_track_to_item(track))
                if len(suggestions) >= limit:
                    break
            return suggestions

        # default: similar by primary artist
        primary_artist = None
        artists = getattr(base_track, "artists", []) or []
        if artists and isinstance(artists[0], dict):
            primary_artist = artists[0].get("artisthash")

        if not primary_artist:
            return []

        for track in TrackStore.get_tracks_by_artisthash(primary_artist):
            if track.trackhash == base_track.trackhash:
                continue
            suggestions.append(_track_to_item(track))
            if len(suggestions) >= limit:
                break

        return suggestions

    def get_download_suggestions(self, query: str, limit: int) -> list[dict[str, Any]]:
        suggestions = self.search_suggestions(
            query=query, context="download", limit=limit
        )
        return [item for item in suggestions if item.get("type") in {"track", "album"}]

    def get_search_filters(self) -> list[dict[str, Any]]:
        filters = [
            {"key": "type", "label": "Type", "options": ["track", "album", "artist"]},
            {
                "key": "sort",
                "label": "Sort",
                "options": ["relevance", "popular", "recent"],
            },
            {"key": "explicit", "label": "Explicit", "options": ["include", "exclude"]},
        ]
        return filters

    def get_trending(
        self, item_type: str, timeframe: str, limit: int
    ) -> list[dict[str, Any]]:
        return self.get_recommendations(item_type, limit)

    def advanced_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or payload.get("q") or "").strip()
        limit = int(payload.get("limit") or 20)
        limit = max(1, min(limit, 100))

        if not query:
            return {
                "query": query,
                "results": {
                    "tracks": [],
                    "albums": [],
                    "artists": [],
                    "playlists": [],
                },
            }

        try:
            tracks = searchlib.SearchTracks(query)(limit=limit)
            albums = searchlib.SearchAlbums(query)(limit=limit)
            artists = searchlib.SearchArtists(query)(limit=limit)
        except Exception:
            tracks, albums, artists = [], [], []

        return {
            "query": query,
            "results": {
                "tracks": [_track_to_item(track) for track in tracks[:limit]],
                "albums": [_album_to_item(album) for album in albums[:limit]],
                "artists": [_artist_to_item(artist) for artist in artists[:limit]],
                "playlists": [],
            },
            "total_count": min(limit * 3, len(tracks) + len(albums) + len(artists)),
        }

    def quick_suggestions(
        self, suggestion_type: str, limit: int
    ) -> list[dict[str, Any]]:
        suggestion_type = (suggestion_type or "search").lower()
        limit = max(1, min(limit, 20))

        if suggestion_type == "trending":
            return self.get_trending("mixed", "week", limit)

        return self.search_suggestions(query="", context=suggestion_type, limit=limit)


advanced_ux_store = AdvancedUXStore()

"""
Enhanced Search API for SwingMusic
Integrates global music catalog search with existing local search
"""

import asyncio
import logging
from typing import Any

from flask import Blueprint, jsonify, request

from swingmusic.api.search import search_items as local_search
from swingmusic.db.spotify import UserCatalogPreferencesTable
from swingmusic.services.music_catalog import music_catalog_service

logger = logging.getLogger(__name__)

# Create blueprint
enhanced_search_bp = Blueprint("enhanced_search", __name__, url_prefix="/api/search")


@enhanced_search_bp.route("/global", methods=["POST"])
def global_search():
    """
    Search across global music catalog (Spotify)

    Request body:
    {
        "query": "search query",
        "type": "all|tracks|albums|artists|playlists",
        "limit": 20,
        "user_id": 1
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("query"):
            return jsonify({"error": "Search query is required"}), 400

        query = data["query"].strip()
        search_type = data.get("type", "all")
        limit = min(data.get("limit", 20), 50)  # Cap at 50
        user_id = data.get("user_id")

        # Get user preferences if available
        user_prefs = None
        if user_id:
            user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)
            limit = min(limit, user_prefs.max_search_results)

        # Run async search
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                music_catalog_service.search_global_catalog(query, search_type, limit)
            )
        finally:
            loop.close()

        # Filter based on user preferences
        if user_prefs and not user_prefs.show_explicit:
            result.tracks = [track for track in result.tracks if not track.explicit]
            result.albums = [album for album in result.albums if not album.explicit]

        # Convert to dict for JSON response
        response_data = {
            "query": result.query,
            "total": result.total,
            "tracks": [_catalog_item_to_dict(track) for track in result.tracks],
            "albums": [_catalog_item_to_dict(album) for album in result.albums],
            "artists": [_catalog_item_to_dict(artist) for artist in result.artists],
            "playlists": [
                _catalog_item_to_dict(playlist) for playlist in result.playlists
            ],
            "source": "global_catalog",
            "cache_info": {
                "from_cache": False,  # Cache detection would require tracking query timestamps
                "expires_at": None,
            },
        }

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error in global search: {e}")
        return jsonify({"error": "Search failed"}), 500


@enhanced_search_bp.route("/combined", methods=["POST"])
def combined_search():
    """
    Search both local library and global catalog

    Request body:
    {
        "query": "search query",
        "include_local": true,
        "include_global": true,
        "type": "all|tracks|albums|artists",
        "limit": 20,
        "user_id": 1
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("query"):
            return jsonify({"error": "Search query is required"}), 400

        query = data["query"].strip()
        include_local = data.get("include_local", True)
        include_global = data.get("include_global", True)
        search_type = data.get("type", "all")
        limit = min(data.get("limit", 20), 50)
        user_id = data.get("user_id")

        results = {
            "query": query,
            "local": {"tracks": [], "albums": [], "artists": []},
            "global": {"tracks": [], "albums": [], "artists": [], "playlists": []},
            "total": 0,
        }

        # Search local library
        if include_local:
            try:
                # Use existing local search
                local_results = local_search(query, search_type)
                results["local"] = (
                    local_results
                    if local_results
                    else {"tracks": [], "albums": [], "artists": []}
                )
            except Exception as e:
                logger.error(f"Error in local search: {e}")

        # Search global catalog
        if include_global:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                global_results = loop.run_until_complete(
                    music_catalog_service.search_global_catalog(
                        query, search_type, limit
                    )
                )

                # Filter based on user preferences
                user_prefs = None
                if user_id:
                    user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)
                    if not user_prefs.show_explicit:
                        global_results.tracks = [
                            track
                            for track in global_results.tracks
                            if not track.explicit
                        ]
                        global_results.albums = [
                            album
                            for album in global_results.albums
                            if not album.explicit
                        ]

                results["global"] = {
                    "tracks": [
                        _catalog_item_to_dict(track) for track in global_results.tracks
                    ],
                    "albums": [
                        _catalog_item_to_dict(album) for album in global_results.albums
                    ],
                    "artists": [
                        _catalog_item_to_dict(artist)
                        for artist in global_results.artists
                    ],
                    "playlists": [
                        _catalog_item_to_dict(playlist)
                        for playlist in global_results.playlists
                    ],
                }

            finally:
                loop.close()

        # Calculate total
        results["total"] = (
            len(results["local"].get("tracks", []))
            + len(results["local"].get("albums", []))
            + len(results["local"].get("artists", []))
            + len(results["global"].get("tracks", []))
            + len(results["global"].get("albums", []))
            + len(results["global"].get("artists", []))
            + len(results["global"].get("playlists", []))
        )

        return jsonify(results)

    except Exception as e:
        logger.error(f"Error in combined search: {e}")
        return jsonify({"error": "Search failed"}), 500


@enhanced_search_bp.route("/suggestions", methods=["GET"])
def search_suggestions():
    """
    Get search suggestions based on query and user preferences

    Query parameters:
    - q: search query
    - type: tracks|albums|artists|all
    - limit: number of suggestions (default 10)
    - user_id: user ID for preferences
    """
    try:
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify({"suggestions": []})

        search_type = request.args.get("type", "all")
        limit = min(int(request.args.get("limit", 10)), 20)
        user_id = request.args.get("user_id")

        # Get user preferences
        user_prefs = None
        if user_id:
            user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)
            limit = min(limit, user_prefs.max_search_results)

        # Search cached items for fast suggestions
        item_types = None
        if search_type != "all":
            item_types = [search_type]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # For suggestions, search both cache and live
            suggestions = []

            # Search cached items first (fast)
            from swingmusic.db.spotify import GlobalCatalogCacheTable

            cached_items = GlobalCatalogCacheTable.search_cached(
                query, item_types, limit
            )

            for item in cached_items:
                if user_prefs and not user_prefs.show_explicit and item.explicit:
                    continue

                suggestion = {
                    "id": item.spotify_id,
                    "type": item.item_type,
                    "title": item.title,
                    "artist": item.artist,
                    "album": item.album,
                    "image_url": item.image_url,
                    "popularity": item.popularity,
                    "source": "cache",
                }
                suggestions.append(suggestion)

            # If we need more suggestions, search global catalog
            if len(suggestions) < limit:
                remaining = limit - len(suggestions)
                global_results = loop.run_until_complete(
                    music_catalog_service.search_global_catalog(
                        query, search_type, remaining
                    )
                )

                for track in global_results.tracks[:remaining]:
                    if user_prefs and not user_prefs.show_explicit and track.explicit:
                        continue

                    suggestion = {
                        "id": track.spotify_id,
                        "type": "track",
                        "title": track.title,
                        "artist": track.artist,
                        "album": track.album,
                        "image_url": track.image_url,
                        "popularity": track.popularity,
                        "source": "global",
                    }
                    suggestions.append(suggestion)

            return jsonify({"suggestions": suggestions[:limit]})

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error in search suggestions: {e}")
        return jsonify({"suggestions": []})


@enhanced_search_bp.route("/artist/<artist_id>", methods=["GET"])
def get_artist_info(artist_id: str):
    """
    Get comprehensive artist information including top tracks and albums

    Path parameters:
    - artist_id: Spotify artist ID

    Query parameters:
    - user_id: user ID for preferences
    """
    try:
        user_id = request.args.get("user_id")

        # Get user preferences
        user_prefs = None
        if user_id:
            user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            artist_info = loop.run_until_complete(
                music_catalog_service.get_artist_info(artist_id)
            )

            if not artist_info:
                return jsonify({"error": "Artist not found"}), 404

            # Filter based on user preferences
            if user_prefs and not user_prefs.show_explicit:
                artist_info.top_tracks = [
                    track
                    for track in artist_info.top_tracks or []
                    if not track.explicit
                ]
                artist_info.albums = [
                    album for album in artist_info.albums or [] if not album.explicit
                ]

            response_data = {
                "spotify_id": artist_info.spotify_id,
                "name": artist_info.name,
                "image_url": artist_info.image_url,
                "followers": artist_info.followers,
                "popularity": artist_info.popularity,
                "genres": artist_info.genres or [],
                "top_tracks": [
                    _catalog_item_to_dict(track)
                    for track in (artist_info.top_tracks or [])
                ],
                "albums": [
                    _catalog_item_to_dict(album) for album in (artist_info.albums or [])
                ],
                "related_artists": artist_info.related_artists or [],
            }

            return jsonify(response_data)

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error getting artist info: {e}")
        return jsonify({"error": "Failed to get artist info"}), 500


@enhanced_search_bp.route("/album/<album_id>", methods=["GET"])
def get_album_details(album_id: str):
    """
    Get detailed album information with tracklist

    Path parameters:
    - album_id: Spotify album ID

    Query parameters:
    - user_id: user ID for preferences
    """
    try:
        user_id = request.args.get("user_id")

        # Get user preferences
        user_prefs = None
        if user_id:
            user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            album = loop.run_until_complete(
                music_catalog_service.get_album_details(album_id)
            )

            if not album:
                return jsonify({"error": "Album not found"}), 404

            # Filter based on user preferences
            if user_prefs and not user_prefs.show_explicit and album.explicit:
                return jsonify({"error": "Explicit content filtered"}), 403

            response_data = _catalog_item_to_dict(album)

            # Add tracklist if available in data
            if album.data and "tracks" in album.data:
                response_data["tracks"] = [
                    _catalog_item_to_dict(track) for track in album.data["tracks"]
                ]

            return jsonify(response_data)

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error getting album details: {e}")
        return jsonify({"error": "Failed to get album details"}), 500


@enhanced_search_bp.route("/preferences/<int:user_id>", methods=["GET", "POST"])
def user_preferences(user_id: int):
    """Get or update user catalog search preferences"""
    try:
        if request.method == "GET":
            prefs = UserCatalogPreferencesTable.get_or_create(user_id)
            return jsonify(
                {
                    "user_id": prefs.user_id,
                    "show_explicit": prefs.show_explicit,
                    "default_quality": prefs.default_quality,
                    "auto_download": prefs.auto_download,
                    "show_suggestions": prefs.show_suggestions,
                    "preferred_genres": prefs.preferred_genres or [],
                    "excluded_genres": prefs.excluded_genres or [],
                    "max_search_results": prefs.max_search_results,
                    "cache_ttl_preference": prefs.cache_ttl_preference,
                }
            )

        elif request.method == "POST":
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400

            # Update only provided fields
            update_data = {}
            allowed_fields = [
                "show_explicit",
                "default_quality",
                "auto_download",
                "show_suggestions",
                "preferred_genres",
                "excluded_genres",
                "max_search_results",
                "cache_ttl_preference",
            ]

            for field in allowed_fields:
                if field in data:
                    update_data[field] = data[field]

            if update_data:
                UserCatalogPreferencesTable.update_preferences(user_id, update_data)

            return jsonify({"message": "Preferences updated successfully"})

    except Exception as e:
        logger.error(f"Error handling user preferences: {e}")
        return jsonify({"error": "Failed to handle preferences"}), 500


def _catalog_item_to_dict(item) -> dict[str, Any]:
    """Convert CatalogItem to dictionary for JSON response"""
    if hasattr(item, "__dict__"):
        # It's a dataclass instance
        return {
            "spotify_id": item.spotify_id,
            "type": item.item_type.value
            if hasattr(item.item_type, "value")
            else str(item.item_type),
            "title": item.title,
            "artist": item.artist,
            "album": item.album,
            "duration_ms": item.duration_ms,
            "popularity": item.popularity,
            "preview_url": item.preview_url,
            "image_url": item.image_url,
            "release_date": item.release_date,
            "explicit": item.explicit,
            "data": item.data,
        }
    else:
        # It's likely a database model
        return {
            "spotify_id": getattr(item, "spotify_id", None),
            "type": getattr(item, "item_type", None),
            "title": getattr(item, "title", None),
            "artist": getattr(item, "artist", None),
            "album": getattr(item, "album", None),
            "duration_ms": getattr(item, "duration_ms", None),
            "popularity": getattr(item, "popularity", None),
            "preview_url": getattr(item, "preview_url", None),
            "image_url": getattr(item, "image_url", None),
            "release_date": getattr(item, "release_date", None),
            "explicit": getattr(item, "explicit", False),
            "data": getattr(item, "data", None),
        }


def register_enhanced_search_api(app):
    """Register enhanced search API with Flask app"""
    app.register_blueprint(enhanced_search_bp)
    logger.info("Enhanced search API registered")

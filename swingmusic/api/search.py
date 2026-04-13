"""
Contains all the search routes.
"""

import hashlib
import logging
from typing import Any, Literal

from flask_openapi3 import APIBlueprint, Tag
from pydantic import Field
from unidecode import unidecode

from swingmusic import models
from swingmusic.api.apischemas import GenericLimitSchema

# DragonflyDB integration for search caching
from swingmusic.db.dragonfly_extended_client import get_search_cache_service
from swingmusic.lib import searchlib
from swingmusic.serializers.artist import serialize_for_cards
from swingmusic.services.user_library_scope import (
    get_available_trackhashes,
    get_visible_albums,
    get_visible_artists,
)
from swingmusic.settings import Defaults
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.auth import get_current_userid

logger = logging.getLogger(__name__)

tag = Tag(name="Search", description="Search for tracks, albums and artists")
api = APIBlueprint("search", __name__, url_prefix="/search", abp_tags=[tag])

SEARCH_COUNT = 30
"""
The max amount of items to return per request
"""


class SearchQuery(GenericLimitSchema):
    q: str = Field(
        description="The search query",
        json_schema_extra={"example": "Fleetwood Mac"},
    )
    start: int = Field(description="The index to start from", default=0)
    limit: int = Field(
        description="The number of items to return", default=SEARCH_COUNT
    )


class TopResultsQuery(SearchQuery):
    limit: int = Field(
        description="The number of items to return", default=Defaults.API_CARD_LIMIT
    )


class SearchLoadMoreQuery(SearchQuery):
    itemtype: Literal["tracks", "albums", "artists"] = Field(
        description="The type of search",
        json_schema_extra={"example": "tracks"},
    )


class Search:
    def __init__(self, query: str) -> None:
        self.tracks: list[models.Track] = []
        self.query = unidecode(query)

    def search_tracks(self):
        """
        Calls :class:`SearchTracks` which returns the tracks that fuzzily match
        the search terms. Then adds them to the `SearchResults` store.
        """
        self.tracks = TrackStore.get_flat_list()
        return searchlib.TopResults().search(self.query, tracks_only=True)

    def search_artists(self):
        """Calls :class:`SearchArtists` which returns the artists that fuzzily match
        the search term. Then adds them to the `SearchResults` store.
        """
        artists = searchlib.SearchArtists(self.query)()
        return serialize_for_cards(artists)

    def search_albums(self):
        """Calls :class:`SearchAlbums` which returns the albums that fuzzily match
        the search term. Then adds them to the `SearchResults` store.
        """
        return searchlib.TopResults().search(self.query, albums_only=True)

    def get_top_results(
        self,
        limit: int,
    ):
        finder = searchlib.TopResults()
        return finder.search(self.query, limit=limit)


def _get_visible_hash_sets(userid: int):
    return {
        "tracks": get_available_trackhashes(userid),
        "albums": {album.albumhash for album in get_visible_albums(userid)},
        "artists": {artist.artisthash for artist in get_visible_artists(userid)},
    }


def _filter_track_items(items: list[dict], allowed_trackhashes: set[str]) -> list[dict]:
    return [item for item in items if item.get("trackhash") in allowed_trackhashes]


def _filter_album_items(items: list[dict], allowed_albumhashes: set[str]) -> list[dict]:
    return [item for item in items if item.get("albumhash") in allowed_albumhashes]


def _filter_artist_items(
    items: list[dict], allowed_artisthashes: set[str]
) -> list[dict]:
    return [item for item in items if item.get("artisthash") in allowed_artisthashes]


def _is_top_result_visible(top_result: dict, visible: dict[str, set[str]]) -> bool:
    item_type = (top_result.get("type") or "").lower()
    if item_type == "track":
        return top_result.get("trackhash") in visible["tracks"]
    if item_type == "album":
        return top_result.get("albumhash") in visible["albums"]
    if item_type == "artist":
        return top_result.get("artisthash") in visible["artists"]
    return False


def _fallback_top_result(results: dict) -> dict | None:
    for key in ("tracks", "albums", "artists"):
        items = results.get(key) or []
        if items:
            top = dict(items[0])
            if "type" not in top:
                top["type"] = key[:-1]
            return top
    return None


def _get_cache_key(query: str, item_type: str, userid: int) -> str:
    """Generate a cache key for search results"""
    normalized = unidecode(query).lower().strip()
    hash_input = f"{normalized}:{item_type}:{userid}"
    return hashlib.md5(hash_input.encode()).hexdigest()


def _try_get_cached_results(query: str, item_type: str, userid: int) -> dict | None:
    """Try to get cached search results from DragonflyDB"""
    cache = get_search_cache_service()
    if not cache.cache.client.is_available():
        return None

    cache_key = _get_cache_key(query, item_type, userid)
    cached = cache.get_search_results(cache_key)

    if cached:
        logger.debug(f"Search cache hit for '{query}' ({item_type})")
        return cached

    return None


def _cache_search_results(
    query: str, item_type: str, userid: int, results: dict, ttl_hours: int = 1
):
    """Cache search results in DragonflyDB"""
    cache = get_search_cache_service()
    if not cache.cache.client.is_available():
        return

    cache_key = _get_cache_key(query, item_type, userid)
    cache.cache_search_results(cache_key, results, ttl_hours=ttl_hours)
    logger.debug(f"Cached search results for '{query}' ({item_type})")


@api.get("/top")
def get_top_results(query: TopResultsQuery):
    """
    Get top results

    Returns the top results for the given query.
    """
    if not query.q:
        return {"error": "No query provided"}, 400

    userid = get_current_userid()

    # Try to get cached results first
    cached = _try_get_cached_results(query.q, "top", userid)
    if cached:
        return cached

    visible = _get_visible_hash_sets(userid)
    results = Search(query.q).get_top_results(limit=query.limit)

    if not isinstance(results, dict):
        return results

    results["tracks"] = _filter_track_items(
        results.get("tracks") or [], visible["tracks"]
    )
    results["albums"] = _filter_album_items(
        results.get("albums") or [], visible["albums"]
    )
    results["artists"] = _filter_artist_items(
        results.get("artists") or [], visible["artists"]
    )

    top_result = results.get("top_result")
    if (
        top_result
        and not _is_top_result_visible(top_result, visible)
        or top_result is None
    ):
        results["top_result"] = _fallback_top_result(results)

    # Cache the results for 1 hour (search results change frequently)
    _cache_search_results(query.q, "top", userid, results, ttl_hours=1)

    return results


@api.get("/")
def search_items(query: SearchLoadMoreQuery):
    """
    Find tracks, albums or artists from a search query.
    """
    userid = get_current_userid()

    # Try to get cached results first
    cached = _try_get_cached_results(query.q, query.itemtype, userid)
    if cached:
        # Apply pagination to cached results
        results = cached.get("results", [])
        return {
            "results": results[query.start : query.start + query.limit],
            "more": len(results) > query.start + query.limit,
        }

    results: Any = []
    visible = _get_visible_hash_sets(userid)

    match query.itemtype:
        case "tracks":
            results = Search(query.q).search_tracks()
            results = _filter_track_items(results, visible["tracks"])
        case "albums":
            results = Search(query.q).search_albums()
            results = _filter_album_items(results, visible["albums"])
        case "artists":
            results = Search(query.q).search_artists()
            results = _filter_artist_items(results, visible["artists"])
        case _:
            return {
                "error": "Invalid item type. Valid types are 'tracks', 'albums' and 'artists'"
            }, 400

    # Cache the full results for 1 hour
    _cache_search_results(
        query.q, query.itemtype, userid, {"results": results}, ttl_hours=1
    )

    return {
        "results": results[query.start : query.start + query.limit],
        "more": len(results) > query.start + query.limit,
    }


# Note: Generators are not used here because:
# 1. Results are already materialized (loaded from store)
# 2. Pagination requires knowing total count for "more" flag
# 3. Filtering operations need full list access

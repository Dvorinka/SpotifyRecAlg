"""Music Catalog API with per-user availability and recommendation blocks."""

from __future__ import annotations

import asyncio
import random
from dataclasses import asdict, is_dataclass
from typing import Any

import requests
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import and_, func, select

from swingmusic import logger
from swingmusic.config import UserConfig
from swingmusic.db.engine import DbEngine
from swingmusic.db.spotify import UserCatalogPreferencesTable
from swingmusic.db.userdata import ScrobbleTable
from swingmusic.services.library_projection import get_track_availability_map
from swingmusic.services.music_catalog import music_catalog_service
from swingmusic.services.user_library_scope import get_available_trackhashes
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.hashing import create_hash

music_catalog_bp = Blueprint("music_catalog", __name__, url_prefix="/api/catalog")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _current_userid() -> int:
    try:
        identity = get_jwt_identity()
        if isinstance(identity, dict) and identity.get("id") is not None:
            return int(identity["id"])
    except Exception:
        pass

    return get_current_userid()


def _item_to_dict(item: Any) -> dict[str, Any]:
    if item is None:
        return {}

    if is_dataclass(item):
        payload = asdict(item)
    elif hasattr(item, "__dict__"):
        payload = dict(item.__dict__)
    else:
        payload = dict(item)

    item_type = payload.get("item_type")
    if hasattr(item_type, "value"):
        payload["item_type"] = item_type.value

    return payload


def _spotify_url(item_type: str, spotify_id: str | None) -> str | None:
    if not spotify_id:
        return None

    type_map = {
        "track": "track",
        "album": "album",
        "artist": "artist",
        "playlist": "playlist",
    }
    normalized = type_map.get(item_type, item_type)
    return f"https://open.spotify.com/{normalized}/{spotify_id}"


def _navigation_payload(item: dict[str, Any]) -> dict[str, Any]:
    item_type = str(item.get("item_type") or "").lower()
    spotify_id = item.get("spotify_id")
    data = item.get("data") or {}

    payload: dict[str, Any] = {
        "item_type": item_type or "unknown",
        "spotify_id": spotify_id,
        "spotify_url": _spotify_url(item_type or "track", spotify_id),
    }

    if item_type == "artist":
        payload["target"] = {"route": "global-artist", "params": {"id": spotify_id}}
        return payload

    if item_type == "album":
        payload["target"] = {"route": "global-album", "params": {"id": spotify_id}}
        return payload

    if item_type == "playlist":
        payload["target"] = {"route": "global-playlist", "params": {"id": spotify_id}}
        return payload

    # Track navigation: prefer album page, fallback to artist.
    album_data = data.get("album") if isinstance(data, dict) else None
    artists_data = data.get("artists") if isinstance(data, dict) else None
    album_id = album_data.get("id") if isinstance(album_data, dict) else None
    artist_id = None
    if isinstance(artists_data, list) and artists_data:
        first_artist = artists_data[0]
        if isinstance(first_artist, dict):
            artist_id = first_artist.get("id")

    if album_id:
        payload["target"] = {"route": "global-album", "params": {"id": album_id}}
    elif artist_id:
        payload["target"] = {"route": "global-artist", "params": {"id": artist_id}}
    else:
        payload["target"] = {"route": "spotify-track", "params": {"id": spotify_id}}

    if artist_id:
        payload["artist_target"] = {
            "route": "global-artist",
            "params": {"id": artist_id},
        }
    if album_id:
        payload["album_target"] = {"route": "global-album", "params": {"id": album_id}}
    return payload


def _decorate_navigation(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        item["navigation"] = _navigation_payload(item)
    return items


def _get_lastfm_seed_artist_names(limit: int = 12) -> list[str]:
    api_key = UserConfig().lastfmApiKey
    if not api_key:
        return []

    try:
        response = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "chart.gettopartists",
                "api_key": api_key,
                "format": "json",
                "limit": limit,
            },
            timeout=8,
        )
        if not response.ok:
            return []
        payload = response.json()
        artists = payload.get("artists", {}).get("artist", [])
        names = [
            artist.get("name", "").strip() for artist in artists if artist.get("name")
        ]
        return [name for name in names if name]
    except Exception:
        return []


def _build_local_fallback_recommendations(
    limit: int = 18, userid: int | None = None
) -> list[dict[str, Any]]:
    userid = userid or get_current_userid()
    available = get_available_trackhashes(userid)
    if not available:
        return []

    seen = set()
    items: list[dict[str, Any]] = []

    for track in TrackStore.get_flat_list():
        if track.trackhash not in available:
            continue

        for artist in track.artists:
            name = artist.get("name")
            artisthash = artist.get("artisthash")
            if not name or not artisthash or artisthash in seen:
                continue
            seen.add(artisthash)
            items.append(
                {
                    "spotify_id": f"local-{artisthash}",
                    "title": name,
                    "name": name,
                    "source": "local",
                    "navigation": {
                        "item_type": "local_artist",
                        "target": {
                            "route": "ArtistView",
                            "params": {"hash": artisthash},
                        },
                    },
                }
            )
            if len(items) >= limit:
                return items
    return items


def _trackhash_from_catalog_track(track: dict[str, Any]) -> str | None:
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    album = (track.get("album") or "").strip()

    if not title or not artist:
        return None

    return create_hash(title, album, artist)


def _normalize_catalog_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _build_local_album_availability(
    artist_name: str | None, userid: int
) -> dict[str, int]:
    available = get_available_trackhashes(userid)
    if not available:
        return {}

    target_artist = _normalize_catalog_text(artist_name)
    album_counts: dict[str, int] = {}

    for track in TrackStore.get_flat_list():
        if track.trackhash not in available:
            continue

        if target_artist:
            artist_names = [
                _normalize_catalog_text(a.get("name"))
                for a in (track.artists or [])
                if a.get("name")
            ]
            if target_artist not in artist_names:
                continue

        album_key = _normalize_catalog_text(track.album)
        if not album_key:
            continue

        album_counts[album_key] = album_counts.get(album_key, 0) + 1

    return album_counts


def _album_availability_payload(
    album_title: str | None, local_counts: dict[str, int]
) -> dict[str, Any]:
    album_key = _normalize_catalog_text(album_title)
    available_count = int(local_counts.get(album_key, 0)) if album_key else 0

    if available_count > 0:
        return {
            "state": "available",
            "available_tracks": available_count,
            "download_action": {
                "type": "download",
                "label": "Download album",
                "enabled": True,
            },
        }

    return {
        "state": "missing",
        "available_tracks": 0,
        "download_action": {
            "type": "download",
            "label": "Download album",
            "enabled": True,
        },
    }


def _stable_track_signal(trackhash: str | None) -> float:
    value = trackhash or ""
    if not value:
        return 0.0
    try:
        sample = int(value[:8], 16)
    except ValueError:
        sample = sum(ord(ch) for ch in value)
    return (sample % 1000) / 1000.0


def _get_user_track_signals(
    trackhashes: set[str], userid: int
) -> dict[str, dict[str, float]]:
    if not trackhashes:
        return {}

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(
                ScrobbleTable.trackhash,
                func.count(ScrobbleTable.id).label("plays"),
                func.max(ScrobbleTable.timestamp).label("last_played"),
            )
            .where(
                and_(
                    ScrobbleTable.userid == userid,
                    ScrobbleTable.trackhash.in_(trackhashes),
                )
            )
            .group_by(ScrobbleTable.trackhash)
        )
        rows = result.fetchall()

    signals: dict[str, dict[str, float]] = {}
    for row in rows:
        signals[row.trackhash] = {
            "plays": float(row.plays or 0.0),
            "last_played": float(row.last_played or 0.0),
        }
    return signals


def _rank_catalog_tracks_for_user(
    tracks: list[dict[str, Any]], userid: int
) -> list[dict[str, Any]]:
    if not tracks:
        return []

    trackhashes: set[str] = set()
    for track in tracks:
        trackhash = track.get("trackhash") or _trackhash_from_catalog_track(track)
        if trackhash:
            track["trackhash"] = trackhash
            trackhashes.add(trackhash)

    signals = _get_user_track_signals(trackhashes, userid)
    max_plays = max((signal["plays"] for signal in signals.values()), default=0.0)
    last_played_values = [
        signal["last_played"]
        for signal in signals.values()
        if signal["last_played"] > 0
    ]
    min_last_played = min(last_played_values) if last_played_values else 0.0
    max_last_played = max(last_played_values) if last_played_values else 0.0
    max_popularity = max(
        (float(track.get("popularity") or 0.0) for track in tracks), default=0.0
    )

    def _score(track: dict[str, Any]) -> float:
        trackhash = track.get("trackhash")
        signal = signals.get(trackhash or "", {"plays": 0.0, "last_played": 0.0})
        popularity = float(track.get("popularity") or 0.0)
        popularity_norm = (
            (popularity / max_popularity)
            if max_popularity > 0
            else _stable_track_signal(trackhash)
        )

        if max_plays <= 0:
            return (0.75 * popularity_norm) + (0.25 * _stable_track_signal(trackhash))

        plays_norm = signal["plays"] / max_plays if max_plays > 0 else 0.0
        if max_last_played > min_last_played and signal["last_played"] > 0:
            recency_norm = (signal["last_played"] - min_last_played) / (
                max_last_played - min_last_played
            )
        elif signal["last_played"] > 0:
            recency_norm = 1.0
        else:
            recency_norm = 0.0

        return (0.55 * plays_norm) + (0.25 * recency_norm) + (0.20 * popularity_norm)

    ranked = sorted(
        tracks,
        key=lambda track: (_score(track), float(track.get("popularity") or 0.0)),
        reverse=True,
    )
    return ranked


def _decorate_tracks_with_availability(
    tracks: list[dict[str, Any]], userid: int
) -> list[dict[str, Any]]:
    hashes: list[str] = []
    hash_by_index: dict[int, str] = {}

    for index, track in enumerate(tracks):
        trackhash = _trackhash_from_catalog_track(track)
        if not trackhash:
            continue

        hashes.append(trackhash)
        hash_by_index[index] = trackhash
        track["trackhash"] = trackhash

    availability_map = get_track_availability_map(hashes, userid=userid)
    signals = _get_user_track_signals(set(hashes), userid)

    for index, trackhash in hash_by_index.items():
        availability = availability_map.get(trackhash) or {}
        signal = signals.get(trackhash) or {}
        tracks[index]["availability"] = availability
        tracks[index]["import_available"] = bool(availability.get("import_available"))
        tracks[index]["import_action"] = availability.get("import_action")
        tracks[index]["download_action"] = availability.get("download_action")
        tracks[index]["quality_badge"] = availability.get("quality_badge")
        tracks[index]["is_available"] = availability.get("state") == "available"
        tracks[index]["play_count"] = int(signal.get("plays") or 0)
        tracks[index]["last_played"] = int(signal.get("last_played") or 0)
        tracks[index]["navigation"] = _navigation_payload(tracks[index])

    return tracks


def _build_artist_radio(related_artists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for related in related_artists[:6]:
        related_id = related.get("id")
        if not related_id:
            continue

        try:
            tracks = _run_async(
                music_catalog_service.get_artist_top_tracks(related_id, 4)
            )
        except Exception as error:
            logger.warning(
                "Failed to build radio for related artist %s: %s", related_id, error
            )
            continue

        candidates.extend([_item_to_dict(track) for track in tracks])

    deduped: list[dict[str, Any]] = []
    seen = set()
    for track in candidates:
        track_id = track.get("spotify_id")
        if not track_id or track_id in seen:
            continue
        seen.add(track_id)
        deduped.append(track)

    return deduped[:50]


def _build_this_is(top_tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    ordered: list[dict[str, Any]] = []
    for track in top_tracks:
        key = track.get("spotify_id") or track.get("trackhash")
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(track)
    return ordered[:40]


@music_catalog_bp.route("/artist/<artist_id>/top-tracks", methods=["GET"])
def get_artist_top_tracks(artist_id: str):
    try:
        # Product contract: artist pages always expose top 15 tracks.
        limit = min(max(request.args.get("limit", 15, type=int), 1), 15)
        userid = _current_userid()

        tracks = _run_async(
            music_catalog_service.get_artist_top_tracks(artist_id, limit)
        )
        payload = [_item_to_dict(track) for track in tracks]
        payload = _decorate_tracks_with_availability(payload, userid)

        return jsonify({"tracks": payload, "total": len(payload)})
    except Exception as error:
        logger.error("Error getting artist top tracks: %s", error)
        return jsonify({"error": "Failed to get artist top tracks"}), 500


@music_catalog_bp.route("/artist/<artist_id>/albums", methods=["GET"])
def get_artist_discography(artist_id: str):
    try:
        userid = _current_userid()
        albums = _run_async(music_catalog_service.get_artist_discography(artist_id))
        payload = [_item_to_dict(album) for album in albums]
        payload = _decorate_navigation(payload)
        local_album_counts: dict[str, int] = {}

        sections = {
            "albums": [],
            "singles": [],
            "compilations": [],
            "other": [],
        }

        if payload:
            first_artist = (payload[0].get("artist") or "").strip()
            local_album_counts = _build_local_album_availability(first_artist, userid)

        for album in payload:
            album_type = (album.get("data") or {}).get("album_type", "album")
            lowered = str(album_type).lower()

            if lowered == "album":
                sections["albums"].append(album)
            elif lowered in {"single", "ep"}:
                sections["singles"].append(album)
            elif lowered == "compilation":
                sections["compilations"].append(album)
            else:
                sections["other"].append(album)

            album["availability"] = _album_availability_payload(
                album.get("title"), local_album_counts
            )

        return jsonify(
            {
                "albums": payload,
                "sections": sections,
                "total": len(payload),
                "userid": userid,
            }
        )
    except Exception as error:
        logger.error("Error getting artist discography: %s", error)
        return jsonify({"error": "Failed to get artist discography"}), 500


@music_catalog_bp.route("/artist/<artist_id>", methods=["GET"])
def get_artist_info(artist_id: str):
    try:
        userid = _current_userid()
        artist_info = _run_async(music_catalog_service.get_artist_info(artist_id))

        if not artist_info:
            return jsonify({"error": "Artist not found"}), 404

        top_tracks = [
            _item_to_dict(track) for track in (artist_info.top_tracks or [])[:15]
        ]
        top_tracks = _decorate_tracks_with_availability(top_tracks, userid)
        top_tracks = _rank_catalog_tracks_for_user(top_tracks, userid)

        albums = [_item_to_dict(album) for album in (artist_info.albums or [])]
        albums = _decorate_navigation(albums)
        local_album_counts = _build_local_album_availability(artist_info.name, userid)
        related = artist_info.related_artists or []
        for related_artist in related:
            related_artist["navigation"] = {
                "item_type": "artist",
                "target": {
                    "route": "global-artist",
                    "params": {"id": related_artist.get("id")},
                },
                "spotify_url": _spotify_url("artist", related_artist.get("id")),
            }

        this_is_tracks = _decorate_tracks_with_availability(
            _build_this_is(top_tracks), userid
        )
        this_is_tracks = _rank_catalog_tracks_for_user(this_is_tracks, userid)
        radio_tracks = _decorate_tracks_with_availability(
            _build_artist_radio(related), userid
        )
        radio_tracks = _rank_catalog_tracks_for_user(radio_tracks, userid)

        sections = {
            "albums": [],
            "singles": [],
            "compilations": [],
            "other": [],
        }
        for album in albums:
            album["availability"] = _album_availability_payload(
                album.get("title"), local_album_counts
            )
            album_type = (album.get("data") or {}).get("album_type", "album")
            lowered = str(album_type).lower()
            if lowered == "album":
                sections["albums"].append(album)
            elif lowered in {"single", "ep"}:
                sections["singles"].append(album)
            elif lowered == "compilation":
                sections["compilations"].append(album)
            else:
                sections["other"].append(album)

        return jsonify(
            {
                "spotify_id": artist_info.spotify_id,
                "name": artist_info.name,
                "image_url": artist_info.image_url,
                "followers": artist_info.followers,
                "popularity": artist_info.popularity,
                "genres": artist_info.genres or [],
                "top_tracks": top_tracks,
                "albums": albums,
                "discography_sections": sections,
                "related_artists": related,
                "this_is_artist": this_is_tracks,
                "artist_radio": radio_tracks,
            }
        )
    except Exception as error:
        logger.error("Error getting artist info: %s", error)
        return jsonify({"error": "Failed to get artist info"}), 500


@music_catalog_bp.route("/album/<album_id>", methods=["GET"])
def get_album_details(album_id: str):
    try:
        userid = _current_userid()
        album = _run_async(music_catalog_service.get_album_details(album_id))

        if not album:
            return jsonify({"error": "Album not found"}), 404

        payload = _item_to_dict(album)
        payload["navigation"] = _navigation_payload(payload)
        tracks = (payload.get("data") or {}).get("tracks") or []

        normalized_tracks = []
        for track in tracks:
            artists = track.get("artists") or []
            if artists and isinstance(artists[0], dict):
                artist_name = ", ".join(
                    a.get("name", "") for a in artists if a.get("name")
                )
            else:
                artist_name = payload.get("artist") or ""

            normalized_tracks.append(
                {
                    "spotify_id": track.get("id"),
                    "item_type": "track",
                    "title": track.get("name"),
                    "artist": artist_name,
                    "album": payload.get("title"),
                    "duration_ms": track.get("duration_ms"),
                    "explicit": bool(track.get("explicit")),
                    "preview_url": track.get("preview_url"),
                    "track_number": track.get("track_number"),
                    "disc_number": track.get("disc_number"),
                }
            )

        normalized_tracks = _decorate_tracks_with_availability(
            normalized_tracks, userid
        )
        payload["tracks"] = normalized_tracks

        return jsonify(payload)
    except Exception as error:
        logger.error("Error getting album details: %s", error)
        return jsonify({"error": "Failed to get album details"}), 500


@music_catalog_bp.route("/playlist/<playlist_id>", methods=["GET"])
def get_playlist_details(playlist_id: str):
    try:
        userid = _current_userid()
        limit = min(max(request.args.get("limit", 200, type=int), 1), 300)
        playlist = _run_async(
            music_catalog_service.get_playlist_details(playlist_id, limit)
        )

        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        payload = _item_to_dict(playlist)
        payload["navigation"] = _navigation_payload(payload)
        data = payload.get("data") or {}
        tracks = data.get("tracks") or []

        normalized_tracks = []
        for track in tracks:
            artists = track.get("artists") or []
            artist_name = ", ".join(
                artist.get("name", "")
                for artist in artists
                if isinstance(artist, dict) and artist.get("name")
            )
            album = track.get("album") or {}
            album_name = album.get("name") if isinstance(album, dict) else None

            normalized_tracks.append(
                {
                    "spotify_id": track.get("id"),
                    "item_type": "track",
                    "title": track.get("name"),
                    "artist": artist_name,
                    "album": album_name,
                    "duration_ms": track.get("duration_ms"),
                    "explicit": bool(track.get("explicit")),
                    "preview_url": track.get("preview_url"),
                    "track_number": track.get("track_number"),
                    "disc_number": track.get("disc_number"),
                    "popularity": track.get("popularity"),
                }
            )

        normalized_tracks = _decorate_tracks_with_availability(
            normalized_tracks, userid
        )
        payload["tracks"] = normalized_tracks
        payload["owner"] = data.get("owner")
        payload["description"] = data.get("description")
        payload["tracks_total"] = data.get("tracks_total")
        payload["public"] = bool(data.get("public"))
        payload["collaborative"] = bool(data.get("collaborative"))

        return jsonify(payload)
    except Exception as error:
        logger.error("Error getting playlist details: %s", error)
        return jsonify({"error": "Failed to get playlist details"}), 500


@music_catalog_bp.route("/search", methods=["POST"])
def search_catalog():
    try:
        data = request.get_json() or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "Search query is required"}), 400

        search_type = data.get("type", "all")
        limit = min(max(int(data.get("limit", 20)), 1), 50)
        offset = max(int(data.get("offset", 0)), 0)
        userid = _current_userid()

        # Spotify search APIs are page-based. We over-fetch then slice so
        # clients can request deterministic offset-based pages.
        fetch_limit = min(max(offset + limit, limit), 120)
        result = _run_async(
            music_catalog_service.search_global_catalog(query, search_type, fetch_limit)
        )

        tracks_all = _decorate_tracks_with_availability(
            [_item_to_dict(track) for track in result.tracks], userid
        )
        albums_all = _decorate_navigation(
            [_item_to_dict(album) for album in result.albums]
        )
        artists_all = _decorate_navigation(
            [_item_to_dict(artist) for artist in result.artists]
        )
        playlists_all = _decorate_navigation(
            [_item_to_dict(playlist) for playlist in result.playlists]
        )

        def _paginate(
            items: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], int, bool]:
            total_items = len(items)
            page = items[offset : offset + limit]
            has_more = total_items > offset + limit
            return page, total_items, has_more

        tracks, tracks_total, tracks_has_more = _paginate(tracks_all)
        albums, albums_total, albums_has_more = _paginate(albums_all)
        artists, artists_total, artists_has_more = _paginate(artists_all)
        playlists, playlists_total, playlists_has_more = _paginate(playlists_all)

        has_more_payload: bool | dict[str, bool]
        item_total: int | None = None
        if search_type == "tracks":
            has_more_payload = tracks_has_more
            item_total = tracks_total
        elif search_type == "albums":
            has_more_payload = albums_has_more
            item_total = albums_total
        elif search_type == "artists":
            has_more_payload = artists_has_more
            item_total = artists_total
        elif search_type == "playlists":
            has_more_payload = playlists_has_more
            item_total = playlists_total
        else:
            has_more_payload = {
                "tracks": tracks_has_more,
                "albums": albums_has_more,
                "artists": artists_has_more,
                "playlists": playlists_has_more,
            }

        return jsonify(
            {
                "tracks": tracks,
                "albums": albums,
                "artists": artists,
                "playlists": playlists,
                "total": result.total,
                "query": result.query,
                "type": search_type,
                "offset": offset,
                "limit": limit,
                "has_more": has_more_payload,
                "totals": {
                    "tracks": tracks_total,
                    "albums": albums_total,
                    "artists": artists_total,
                    "playlists": playlists_total,
                },
                "item_total": item_total,
            }
        )
    except Exception as error:
        logger.error("Error searching catalog: %s", error)
        return jsonify({"error": "Failed to search catalog"}), 500


@music_catalog_bp.route("/trending", methods=["GET"])
def get_trending_content():
    try:
        content_type = request.args.get("type", "tracks")
        limit = min(max(request.args.get("limit", 20, type=int), 1), 50)
        userid = _current_userid()

        valid_types = {"tracks", "albums", "artists"}
        if content_type not in valid_types:
            return jsonify(
                {"error": f"Invalid type. Must be one of: {sorted(valid_types)}"}
            ), 400

        trending_queries = {
            "tracks": "popular songs",
            "albums": "popular albums",
            "artists": "popular artists",
        }
        query = trending_queries.get(content_type, "popular")

        service_type_map = {
            "tracks": "tracks",
            "albums": "albums",
            "artists": "artists",
        }
        result = _run_async(
            music_catalog_service.search_global_catalog(
                query,
                service_type_map.get(content_type, "tracks"),
                limit,
            )
        )

        response = {
            "type": content_type,
            "query": query,
        }

        if content_type == "tracks":
            tracks = _decorate_tracks_with_availability(
                [_item_to_dict(track) for track in result.tracks], userid
            )
            response["tracks"] = tracks
            response["total"] = len(tracks)
        elif content_type == "albums":
            albums = _decorate_navigation(
                [_item_to_dict(album) for album in result.albums]
            )
            response["albums"] = albums
            response["total"] = len(albums)
        else:
            artists = _decorate_navigation(
                [_item_to_dict(artist) for artist in result.artists]
            )
            response["artists"] = artists
            response["total"] = len(artists)

        return jsonify(response)
    except Exception as error:
        logger.error("Error getting trending content: %s", error)
        return jsonify({"error": "Failed to get trending content"}), 500


@music_catalog_bp.route("/recommendations", methods=["POST"])
def get_recommendations():
    try:
        data = request.get_json() or {}
        seed_artists = data.get("seed_artists", [])
        seed_tracks = data.get("seed_tracks", [])
        seed_genres = data.get("seed_genres", [])
        limit = min(max(int(data.get("limit", 20)), 1), 50)
        userid = _current_userid()

        if not any([seed_artists, seed_tracks, seed_genres]):
            # Cold-start recommendation fallback.
            trend = _run_async(
                music_catalog_service.search_global_catalog(
                    "popular artists", "artists", 6
                )
            )
            seed_artists = [artist.spotify_id for artist in trend.artists[:5]]

        recommendations = []
        for artist_id in seed_artists[:6]:
            tracks = _run_async(
                music_catalog_service.get_artist_top_tracks(artist_id, 6)
            )
            recommendations.extend([_item_to_dict(track) for track in tracks])

        seen = set()
        unique_recommendations: list[dict[str, Any]] = []
        for track in recommendations:
            track_id = track.get("spotify_id")
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            unique_recommendations.append(track)
            if len(unique_recommendations) >= limit:
                break

        unique_recommendations = _decorate_tracks_with_availability(
            unique_recommendations, userid
        )

        return jsonify(
            {
                "tracks": unique_recommendations,
                "total": len(unique_recommendations),
                "seeds": {
                    "artists": seed_artists,
                    "tracks": seed_tracks,
                    "genres": seed_genres,
                },
            }
        )
    except Exception as error:
        logger.error("Error getting recommendations: %s", error)
        return jsonify({"error": "Failed to get recommendations"}), 500


@music_catalog_bp.route("/home/recommendations", methods=["GET"])
def get_home_recommendations():
    """
    Default dashboard recommendations for cold-start users.
    """
    try:
        userid = _current_userid()
        recent_scrobbles = list(ScrobbleTable.get_all(0, 60, userid=userid))

        strategy = "spotify_lastfm_cold_start"
        artist_candidates = []

        if recent_scrobbles:
            strategy = "listening_blend"
            local_artist_names: list[str] = []
            for scrobble in recent_scrobbles:
                entry = TrackStore.trackhashmap.get(scrobble.trackhash)
                if not entry:
                    continue

                for artist in entry.tracks[0].artists:
                    name = artist.get("name")
                    if name and name not in local_artist_names:
                        local_artist_names.append(name)

            for name in local_artist_names[:6]:
                result = _run_async(
                    music_catalog_service.search_global_catalog(name, "artists", 8)
                )
                artist_candidates.extend(
                    [_item_to_dict(artist) for artist in result.artists]
                )

        if not artist_candidates:
            # Cold-start: Last.fm chart seeds first, then Spotify query fallback.
            lastfm_names = _get_lastfm_seed_artist_names(limit=10)
            if lastfm_names:
                strategy = "lastfm_seeded"
                for name in lastfm_names[:8]:
                    result = _run_async(
                        music_catalog_service.search_global_catalog(name, "artists", 8)
                    )
                    artist_candidates.extend(
                        [_item_to_dict(artist) for artist in result.artists]
                    )

        if not artist_candidates:
            strategy = "spotify_seeded"
            seed_queries = [
                "jazz",
                "indie",
                "electronic",
                "hip hop",
                "rock",
                "soul",
                "classical",
                "pop",
            ]
            random.Random(userid).shuffle(seed_queries)
            for query in seed_queries[:4]:
                result = _run_async(
                    music_catalog_service.search_global_catalog(query, "artists", 10)
                )
                artist_candidates.extend(
                    [_item_to_dict(artist) for artist in result.artists]
                )

        unique = []
        seen = set()
        for artist in artist_candidates:
            artist_id = artist.get("spotify_id")
            if not artist_id or artist_id in seen:
                continue
            seen.add(artist_id)
            unique.append(artist)

        random.Random(userid + len(unique)).shuffle(unique)
        selected = _decorate_navigation(unique[:18])

        if not selected:
            strategy = "local_library_fallback"
            selected = _build_local_fallback_recommendations(limit=18, userid=userid)

        return jsonify(
            {
                "strategy": strategy,
                "artists": selected,
                "total": len(selected),
            }
        )
    except Exception as error:
        logger.error("Error getting home recommendations: %s", error)
        return jsonify({"error": "Failed to get home recommendations"}), 500


@music_catalog_bp.route("/preferences/<int:user_id>", methods=["GET", "POST"])
def user_catalog_preferences(user_id: int):
    try:
        userid = _current_userid()
        if userid != user_id:
            return jsonify({"error": "Forbidden"}), 403

        if request.method == "GET":
            user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)
            return jsonify(
                {
                    "user_id": user_id,
                    "max_search_results": user_prefs.max_search_results,
                    "max_top_tracks": user_prefs.max_top_tracks,
                    "max_albums_per_artist": user_prefs.max_albums_per_artist,
                    "max_trending_results": user_prefs.max_trending_results,
                    "max_recommendations": user_prefs.max_recommendations,
                    "show_explicit": user_prefs.show_explicit,
                    "preferred_markets": user_prefs.preferred_markets or [],
                }
            )

        data = request.get_json() or {}
        user_prefs = UserCatalogPreferencesTable.get_or_create(user_id)

        if "max_search_results" in data:
            user_prefs.max_search_results = min(int(data["max_search_results"]), 100)
        if "max_top_tracks" in data:
            user_prefs.max_top_tracks = min(int(data["max_top_tracks"]), 50)
        if "max_albums_per_artist" in data:
            user_prefs.max_albums_per_artist = min(
                int(data["max_albums_per_artist"]), 100
            )
        if "max_trending_results" in data:
            user_prefs.max_trending_results = min(
                int(data["max_trending_results"]), 100
            )
        if "max_recommendations" in data:
            user_prefs.max_recommendations = min(int(data["max_recommendations"]), 100)
        if "show_explicit" in data:
            user_prefs.show_explicit = bool(data["show_explicit"])
        if "preferred_markets" in data:
            user_prefs.preferred_markets = data["preferred_markets"]

        user_prefs.save()
        return jsonify({"message": "Preferences updated successfully"})
    except Exception as error:
        logger.error("Error handling catalog preferences: %s", error)
        return jsonify({"error": "Failed to handle preferences"}), 500

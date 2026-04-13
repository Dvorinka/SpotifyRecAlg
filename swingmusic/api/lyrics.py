import json
import logging

from flask_openapi3 import APIBlueprint, Tag
from pydantic import Field

from swingmusic.api.apischemas import TrackHashSchema

# DragonflyDB integration for lyrics caching
from swingmusic.db.dragonfly_client import get_dragonfly_client
from swingmusic.lib.lyrics import (
    Lyrics as Lyrics_class,
)
from swingmusic.lib.lyrics import (
    get_lyrics_file,
    get_lyrics_from_duplicates,
    get_lyrics_from_tags,
)
from swingmusic.plugins.lyrics import Lyrics
from swingmusic.store.tracks import TrackStore

logger = logging.getLogger(__name__)

bp_tag = Tag(name="Lyrics", description="Get lyrics")
api = APIBlueprint("lyrics", __name__, url_prefix="/lyrics", abp_tags=[bp_tag])


class SendLyricsBody(TrackHashSchema):
    filepath: str = Field(description="The path to the file")


@api.post("")
def send_lyrics(body: SendLyricsBody):
    """
    Returns the lyrics for a track
    """
    # 1. try to get lyrics by .lrc / .elrc file
    # 2. try to get lyrics by extra key
    # 3. try to get by duplicates
    # 4. iter plugins

    filepath = body.filepath
    trackhash = body.trackhash

    # Try DragonflyDB cache first
    cache = get_dragonfly_client()
    cache_key = f"lyrics:{trackhash}"

    if cache.is_available():
        try:
            cached = cache.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for lyrics {trackhash}")
                return json.loads(cached)
        except Exception:
            pass  # Cache miss is fine

    # get copyright first
    copyright = ""
    if entry := TrackStore.trackhashmap.get(trackhash, None):
        for track in entry.tracks:
            copyright = track.copyright

            if copyright:
                break

    lyrics = get_lyrics_file(filepath)

    if not lyrics:
        lyrics = get_lyrics_from_tags(trackhash)  # type: ignore

    if not lyrics:
        lyrics = get_lyrics_from_duplicates(filepath, trackhash)

    # check lyrics plugins
    if not lyrics:
        try:
            # Get track metadata for plugin search
            entry = TrackStore.trackhashmap.get(trackhash, None)
            if entry and len(entry.tracks) > 0:
                track = entry.tracks[0]  # Use first track for metadata
                title = getattr(track, "title", "") or ""
                artist = ""
                if hasattr(track, "artists") and track.artists:
                    artist = (
                        track.artists[0].name
                        if hasattr(track.artists[0], "name")
                        else str(track.artists[0])
                    )
                album = ""
                if hasattr(track, "album") and track.album:
                    album = (
                        track.album.name
                        if hasattr(track.album, "name")
                        else str(track.album)
                    )

                # Only proceed if we have basic metadata
                if title and artist:
                    # Initialize lyrics plugin
                    lyrics_plugin = Lyrics()
                    if lyrics_plugin.enabled:
                        # LRCLIB-first metadata retrieval with provider fallback.
                        lrc_content = lyrics_plugin.download_lyrics_by_metadata(
                            title=title,
                            artist=artist,
                            path=filepath,
                            album=album,
                        )

                        # Fallback to provider search result track IDs when metadata fetch fails.
                        if not lrc_content:
                            search_results = (
                                lyrics_plugin.search_lyrics_by_title_and_artist(
                                    title, artist
                                )
                            )
                            if search_results and len(search_results) > 0:
                                perfect_match = search_results[0]
                                if album:
                                    for result in search_results:
                                        result_title = result.get("title", "").lower()
                                        result_album = result.get("album", "").lower()
                                        if (
                                            result_title == title.lower()
                                            and result_album == album.lower()
                                        ):
                                            perfect_match = result
                                            break

                                track_id = perfect_match.get("track_id")
                                if track_id:
                                    lrc_content = lyrics_plugin.download_lyrics(
                                        track_id, filepath
                                    )

                        if lrc_content and len(lrc_content.strip()) > 0:
                            lyrics = Lyrics_class(lrc_content)
        except Exception:
            # Log error but don't break the lyrics fetching process
            # In production, you might want to log this error
            pass

    if not lyrics:
        return {"error": "No lyrics found"}

    if lyrics.is_synced:
        text = lyrics.format_synced_lyrics()
    else:
        text = lyrics.format_unsynced_lyrics()

    result = {"lyrics": text, "synced": lyrics.is_synced, "copyright": copyright}

    # Cache lyrics for 24 hours (lyrics rarely change)
    if cache.is_available():
        import contextlib

        with contextlib.suppress(Exception):
            cache.set(cache_key, json.dumps(result), ex=86400)

    return result, 200


@api.post("/check")
def check_lyrics(body: SendLyricsBody):
    """
    Checks if lyrics file or tag exists for a track
    """
    result = send_lyrics(body)

    if "error" in result:
        return {"exists": False}
    else:
        return {"exists": True}, 200

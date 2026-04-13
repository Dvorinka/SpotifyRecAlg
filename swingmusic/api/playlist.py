"""
All playlist-related routes.
"""

import json
import logging
import pathlib
from datetime import datetime
from typing import Any

from flask_openapi3 import APIBlueprint, Tag
from flask_openapi3 import FileStorage as _FileStorage
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, GetCoreSchemaHandler
from pydantic_core import core_schema

from swingmusic import models
from swingmusic.api.apischemas import GenericLimitSchema

# DragonflyDB integration for playlist caching
from swingmusic.db.dragonfly_client import get_dragonfly_client
from swingmusic.db.userdata import PlaylistTable
from swingmusic.lib import playlistlib
from swingmusic.lib.albumslib import sort_by_track_no
from swingmusic.lib.home.recentlyadded import get_recently_added_playlist
from swingmusic.lib.home.recentlyplayed import get_recently_played_playlist
from swingmusic.lib.sortlib import sort_tracks
from swingmusic.models.playlist import Playlist
from swingmusic.serializers.playlist import serialize_for_card
from swingmusic.serializers.track import serialize_tracks
from swingmusic.services.user_library_scope import (
    filter_trackhashes_for_user,
    get_available_trackhashes,
)
from swingmusic.settings import Paths
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.dates import create_new_date, date_string_to_time_passed

logger = logging.getLogger(__name__)

tag = Tag(name="Playlists", description="Get and manage playlists")
api = APIBlueprint("playlists", __name__, url_prefix="/playlists", abp_tags=[tag])


def insert_playlist(name: str, image: str = None):
    playlist = {
        "image": image,
        "last_updated": create_new_date(),
        "name": name,
        "trackhashes": [],
        "settings": {
            "has_gif": False,
            "banner_pos": 50,
            "square_img": bool(image),
            "pinned": False,
        },
    }

    rowid = PlaylistTable.add_one(playlist)
    if rowid:
        playlist["id"] = rowid
        return Playlist(**playlist)

    return None


def get_path_trackhashes(
    path: str, tracksortby: str, reverse: bool, userid: int | None = None
):
    """
    Returns a list of trackhashes in a folder.
    """
    tracks = TrackStore.get_tracks_in_path(path)
    tracks = sort_tracks(tracks, key=tracksortby, reverse=reverse)
    return filter_trackhashes_for_user([t.trackhash for t in tracks], userid=userid)


def get_album_trackhashes(albumhash: str, userid: int | None = None):
    """
    Returns a list of trackhashes in an album.
    """
    tracks = TrackStore.get_tracks_by_albumhash(albumhash)
    tracks = sort_by_track_no(tracks)

    return filter_trackhashes_for_user([t.trackhash for t in tracks], userid=userid)


def get_artist_trackhashes(artisthash: str, userid: int | None = None):
    """
    Returns a list of trackhashes for an artist.
    """
    tracks = TrackStore.get_tracks_by_artisthash(artisthash)
    tracks = sort_tracks(tracks, key="playcount", reverse=True)
    return filter_trackhashes_for_user([t.trackhash for t in tracks], userid=userid)


def format_custom_playlist(playlist: models.Playlist, tracks: list[models.Track]):
    playlist.duration = sum(t.duration for t in tracks)
    playlist.count = len(tracks)

    return {
        "info": serialize_for_card(playlist),
        "tracks": serialize_tracks(tracks),
    }


class SendAllPlaylistsQuery(BaseModel):
    no_images: bool = Field(False, description="Whether to include images")


@api.get("")
def send_all_playlists(query: SendAllPlaylistsQuery):
    """
    Gets all the playlists.
    """
    playlists = PlaylistTable.get_all()
    playlists = sorted(
        playlists,
        key=lambda p: datetime.strptime(p.last_updated, "%Y-%m-%d %H:%M:%S"),
        reverse=True,
    )
    available_trackhashes = get_available_trackhashes(get_current_userid())

    for playlist in playlists:
        visible_trackhashes = [
            trackhash
            for trackhash in playlist.trackhashes
            if trackhash in available_trackhashes
        ]
        playlist.count = len(visible_trackhashes)

        if not playlist.has_image:
            playlist.images = playlistlib.get_first_4_images(
                trackhashes=visible_trackhashes
            )

        playlist.clear_lists()

    # playlists.sort(
    #     key=lambda p: datetime.strptime(p.last_updated, "%Y-%m-%d %H:%M:%S"),
    #     reverse=True,
    # )

    return {"data": playlists}


class CreatePlaylistBody(BaseModel):
    name: str = Field(..., description="The name of the playlist")


@api.post("/new")
def create_playlist(body: CreatePlaylistBody):
    """
    New playlist

    Creates a new playlist. Accepts POST method with a JSON body.
    """
    exists = PlaylistTable.check_exists_by_name(body.name)

    if exists:
        return {"error": "Playlist already exists"}, 409

    playlist = insert_playlist(body.name)

    if playlist is None:
        return {"error": "Playlist could not be created"}, 500

    return {"playlist": playlist}, 201


class PlaylistIDPath(BaseModel):
    # INFO: playlistid string examples: "recentlyadded"
    playlistid: str = Field(..., description="The ID of the playlist")


class AddItemToPlaylistBody(BaseModel):
    itemtype: str = Field(
        default="tracks",
        description="The type of item to add",
        examples=["tracks", "folder", "album", "artist"],
    )
    sortoptions: dict = Field(
        default=None,
        description="The sort options for the tracks",
    )
    itemhash: str = Field(..., description="The hash of the item to add")


@api.post("/<playlistid>/add")
def add_item_to_playlist(path: PlaylistIDPath, body: AddItemToPlaylistBody):
    """
    Add to playlist.

    If itemtype is not "tracks", itemhash is expected to be a folder, album or artist hash.
    """
    itemtype = body.itemtype
    itemhash = body.itemhash
    playlist_id = int(path.playlistid)
    sortoptions = body.sortoptions
    userid = get_current_userid()

    if itemtype == "tracks":
        trackhashes = itemhash.split(",")
        trackhashes = filter_trackhashes_for_user(trackhashes, userid=userid)
        if len(trackhashes) == 1 and trackhashes[0] in PlaylistTable.get_trackhashes(
            playlist_id
        ):
            return {"msg": "Track already exists in playlist"}, 409
    elif itemtype == "folder":
        trackhashes = get_path_trackhashes(
            itemhash,
            sortoptions.get("tracksortby") or "default",
            sortoptions.get("tracksortreverse") or False,
            userid=userid,
        )
    elif itemtype == "album":
        trackhashes = get_album_trackhashes(itemhash, userid=userid)
    elif itemtype == "artist":
        trackhashes = get_artist_trackhashes(itemhash, userid=userid)
    else:
        trackhashes = []

    PlaylistTable.append_to_playlist(playlist_id, trackhashes)
    return {"msg": "Done"}, 200


class GetPlaylistQuery(GenericLimitSchema):
    no_tracks: bool = Field(False, description="Whether to include tracks")
    start: int = Field(0, description="The start index of the tracks")


@api.get("/<playlistid>")
def get_playlist(path: PlaylistIDPath, query: GetPlaylistQuery):
    """
    Get playlist by id
    """
    no_tracks = query.no_tracks
    playlistid = path.playlistid

    custom_playlists = [
        {"name": "recentlyadded", "handler": get_recently_added_playlist},
        {"name": "recentlyplayed", "handler": get_recently_played_playlist},
    ]
    is_custom = playlistid in {p["name"] for p in custom_playlists}

    if is_custom:
        if query.start != 0:
            return {
                "tracks": [],
            }

        handler = next(
            p["handler"] for p in custom_playlists if p["name"] == playlistid
        )
        playlist, tracks = handler()
        return format_custom_playlist(playlist, tracks)

    # Try DragonflyDB cache first for regular playlists
    cache = get_dragonfly_client()
    cache_key = f"playlists:{playlistid}:{query.start}:{query.limit}"

    if cache.is_available():
        try:
            cached = cache.get(cache_key)
            if cached:
                result = json.loads(cached)
                logger.debug(f"Cache hit for playlist {playlistid}")
                return result
        except Exception:
            pass  # Cache miss is fine

    playlist = PlaylistTable.get_by_id(int(playlistid))

    if playlist is None:
        return {"msg": "Playlist not found"}, 404

    if query.limit == -1:
        query.limit = len(playlist.trackhashes) - 1

    available_trackhashes = get_available_trackhashes(get_current_userid())
    scoped_trackhashes = [
        trackhash
        for trackhash in playlist.trackhashes
        if trackhash in available_trackhashes
    ]

    tracks = TrackStore.get_tracks_by_trackhashes(
        scoped_trackhashes[query.start : query.start + query.limit]
    )
    duration = sum(t.duration for t in tracks)
    playlist._last_updated = date_string_to_time_passed(playlist.last_updated)
    playlist.duration = duration
    playlist.images = playlistlib.get_first_4_images(tracks)
    playlist.clear_lists()

    result = {
        "info": playlist,
        "tracks": serialize_tracks(tracks) if not no_tracks else [],
    }

    # Cache the result for 5 minutes
    if cache.is_available():
        import contextlib

        with contextlib.suppress(Exception):
            cache.set(cache_key, json.dumps(result, default=str), ex=300)

    return result


class FileStorage(_FileStorage):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.with_info_plain_validator_function(cls.validate)


class UpdatePlaylistForm(BaseModel):
    image: FileStorage = Field(description="The image file")
    name: str = Field(..., description="The name of the playlist")
    settings: str = Field(
        ...,
        description="The settings of the playlist",
        json_schema_extra={
            "example": '{"has_gif": false, "banner_pos": 50, "square_img": false, "pinned": false}'
        },
    )


@api.put("/<playlistid>/update", methods=["PUT"])
def update_playlist_info(path: PlaylistIDPath, form: UpdatePlaylistForm):
    """
    Update playlist
    """
    playlistid = path.playlistid
    db_playlist = PlaylistTable.get_by_id(playlistid)

    if db_playlist is None:
        return {"error": "Playlist not found"}, 404

    image = form.image

    if form.image:
        image = form.image

    settings = json.loads(form.settings)
    settings["has_gif"] = False

    playlist = {
        "id": int(playlistid),
        "image": db_playlist.image,
        "last_updated": create_new_date(),
        "name": str(form.name).strip(),
        "settings": settings,
    }

    if image:
        try:
            pil_image = Image.open(image)
            content_type = image.content_type

            playlist["image"] = playlistlib.save_p_image(
                pil_image, playlistid, content_type
            )

            if image.content_type == "image/gif":
                playlist["settings"]["has_gif"] = True

        except UnidentifiedImageError:
            return {"error": "Failed: Invalid image"}, 400

    p_tuple = (*playlist.values(),)

    PlaylistTable.update_one(playlistid, playlist)
    playlistlib.cleanup_playlist_images()

    playlist = models.Playlist(*p_tuple)
    playlist.last_updated = date_string_to_time_passed(playlist.last_updated)

    return {
        "data": playlist,
    }


@api.post("/<playlistid>/pin_unpin")
def pin_unpin_playlist(path: PlaylistIDPath):
    """
    Pin playlist.
    """
    playlist = PlaylistTable.get_by_id(path.playlistid)

    if playlist is None:
        return {"error": "Playlist not found"}, 404

    settings = playlist.settings

    try:
        settings["pinned"] = not settings["pinned"]
    except KeyError:
        settings["pinned"] = True

    PlaylistTable.update_settings(path.playlistid, settings)
    return {"msg": "Done"}, 200


@api.delete("/<playlistid>/remove-img")
def remove_playlist_image(path: PlaylistIDPath):
    """
    Clear playlist image.
    """
    playlist = PlaylistTable.get_by_id(path.playlistid)

    if playlist is None:
        return {"error": "Playlist not found"}, 404

    PlaylistTable.remove_image(path.playlistid)

    playlist.image = None
    playlist.thumb = None
    playlist.settings["has_gif"] = False
    playlist.has_image = False

    available_trackhashes = get_available_trackhashes(get_current_userid())
    visible_trackhashes = [
        trackhash
        for trackhash in playlist.trackhashes
        if trackhash in available_trackhashes
    ]
    playlist.images = playlistlib.get_first_4_images(trackhashes=visible_trackhashes)
    playlist.last_updated = date_string_to_time_passed(playlist.last_updated)

    return {"playlist": playlist}, 200


@api.delete("/<playlistid>/delete", methods=["DELETE"])
def remove_playlist(path: PlaylistIDPath):
    """
    Delete playlist
    """
    PlaylistTable.remove_one(path.playlistid)
    playlistlib.cleanup_playlist_images()
    return {"msg": "Done"}, 200


class RemoveTracksFromPlaylistBody(BaseModel):
    tracks: list[dict] = Field(..., description="A list of trackhashes to remove")


@api.post("/<playlistid>/remove-tracks")
def remove_tracks_from_playlist(
    path: PlaylistIDPath, body: RemoveTracksFromPlaylistBody
):
    """
    Remove track from playlist
    """
    # A track looks like this:
    # {
    #    trackhash: str;
    #    index: int;
    # }

    PlaylistTable.remove_from_playlist(path.playlistid, body.tracks)

    return {"msg": "Done"}, 200


class SavePlaylistAsItemBody(BaseModel):
    itemtype: str = Field(..., description="The type of item", example="tracks")
    playlist_name: str = Field(..., description="The name of the playlist")
    itemhash: str = Field(..., description="The hash of the item to save")
    sortoptions: dict = Field(
        default={},
        description="The sort options for the tracks",
    )


@api.post("/save-item")
def save_item_as_playlist(body: SavePlaylistAsItemBody):
    """
    Save as playlist

    Saves a track, album, artist or folder as a playlist
    """
    itemtype = body.itemtype
    playlist_name = body.playlist_name
    itemhash = body.itemhash
    sortoptions = body.sortoptions
    userid = get_current_userid()

    if PlaylistTable.check_exists_by_name(playlist_name):
        return {"error": "Playlist already exists"}, 409

    if itemtype == "tracks":
        trackhashes = filter_trackhashes_for_user(itemhash.split(","), userid=userid)
    elif itemtype == "folder":
        trackhashes = get_path_trackhashes(
            itemhash,
            sortoptions.get("tracksortby") or "default",
            sortoptions.get("tracksortreverse") or False,
            userid=userid,
        )
    elif itemtype == "album":
        trackhashes = get_album_trackhashes(itemhash, userid=userid)
    elif itemtype == "artist":
        trackhashes = get_artist_trackhashes(itemhash, userid=userid)
    else:
        trackhashes = []

    if len(trackhashes) == 0:
        return {"error": "No tracks founds"}, 404

    image = (
        itemhash + ".webp" if itemtype != "folder" and itemtype != "tracks" else None
    )

    playlist = insert_playlist(playlist_name, image)

    if playlist is None:
        return {"error": "Playlist could not be created"}, 500

    # save image
    if itemtype != "folder" and itemtype != "tracks":
        filename = itemhash + ".webp"

        base_path = (
            Paths().lg_artist_img_path
            if itemtype == "artist"
            else Paths().lg_thumb_path()
        )
        img_path = pathlib.Path(base_path + "/" + filename)

        if img_path.exists():
            img = Image.open(img_path)
            playlistlib.save_p_image(
                img, str(playlist.id), "image/webp", filename=filename
            )

    PlaylistTable.append_to_playlist(playlist.id, trackhashes)
    playlist.count = len(trackhashes)

    images = playlistlib.get_first_4_images(trackhashes=trackhashes)
    playlist.images = [img["image"] for img in images]

    return {"playlist": playlist}, 201

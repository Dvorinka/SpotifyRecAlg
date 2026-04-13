"""
Recently Played API endpoints.
"""

from flask_openapi3 import APIBlueprint, Tag

from swingmusic.api.apischemas import GenericLimitSchema
from swingmusic.services.recently_played_buffer import get_recently_played_buffer
from swingmusic.utils.auth import get_current_userid

tag = Tag(name="Recently Played", description="Recently played tracks")
api = APIBlueprint(
    "recently_played", __name__, url_prefix="/recently-played", abp_tags=[tag]
)


class RecentlyPlayedQuery(GenericLimitSchema):
    pass


@api.get("")
def get_recently_played(query: RecentlyPlayedQuery):
    """
    Get recently played tracks for the current user.

    Returns tracks from the DragonflyDB buffer for instant access.
    """
    userid = get_current_userid()
    limit = query.limit if query.limit > 0 else 20

    buffer = get_recently_played_buffer()
    tracks = buffer.get_recent_tracks(userid, limit=limit)

    return {"tracks": tracks}


@api.delete("")
def clear_recently_played():
    """
    Clear the recently played buffer for the current user.
    """
    userid = get_current_userid()

    buffer = get_recently_played_buffer()
    success = buffer.clear_buffer(userid)

    if success:
        return {"success": True, "message": "Recently played history cleared"}
    else:
        return {"success": False, "message": "Failed to clear history"}, 500

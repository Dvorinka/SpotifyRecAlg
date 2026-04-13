import logging

from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.api.apischemas import GenericLimitSchema

# DragonflyDB integration for homepage caching
from swingmusic.db.dragonfly_client import DragonflyCache
from swingmusic.lib.home.get_recently_played import get_recently_played
from swingmusic.lib.home.recentlyadded import get_recently_added_items
from swingmusic.store.homepage import HomepageStore
from swingmusic.utils.auth import get_current_userid

logger = logging.getLogger(__name__)

bp_tag = Tag(name="Home", description="Homepage items")
api = APIBlueprint("home", __name__, url_prefix="/nothome", abp_tags=[bp_tag])

# Homepage cache with 5-minute TTL (homepage content changes frequently)
homepage_cache = DragonflyCache("homepage")


def _get_homepage_cache_key(userid: int, limit: int) -> str:
    """Generate cache key for homepage items"""
    return f"items:user:{userid}:limit:{limit}"


def _try_get_cached_homepage(userid: int, limit: int) -> list | None:
    """Try to get cached homepage items"""
    if not homepage_cache.client.is_available():
        return None

    cache_key = _get_homepage_cache_key(userid, limit)
    cached = homepage_cache.get(cache_key)

    if cached:
        logger.debug(f"Homepage cache hit for user {userid}")
        return cached

    return None


def _cache_homepage_items(userid: int, limit: int, items: list, ttl_minutes: int = 5):
    """Cache homepage items with short TTL"""
    if not homepage_cache.client.is_available():
        return

    cache_key = _get_homepage_cache_key(userid, limit)
    ttl_seconds = ttl_minutes * 60
    homepage_cache.client.set(cache_key, items, ttl_seconds)
    logger.debug(f"Cached homepage for user {userid} for {ttl_minutes} minutes")


@api.get("/recents/added")
def get_recently_added(query: GenericLimitSchema):
    """
    Get recently added
    """
    return {"items": get_recently_added_items(query.limit)}


@api.get("/recents/played")
def get_recent_plays(query: GenericLimitSchema):
    """
    Get recently played
    """
    return {"items": get_recently_played(query.limit)}


class HomepageItem(BaseModel):
    limit: int = Field(
        default=9, description="The max number of items per group to return"
    )


@api.get("/")
def homepage_items(query: HomepageItem):
    userid = get_current_userid()

    # Try to get cached homepage first
    cached = _try_get_cached_homepage(userid, query.limit)
    if cached:
        return cached

    # Generate fresh homepage items
    items = HomepageStore.get_homepage_items(limit=query.limit)

    # Cache for 5 minutes (short TTL since homepage changes with plays)
    _cache_homepage_items(userid, query.limit, items, ttl_minutes=5)

    return items

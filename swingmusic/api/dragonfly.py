"""
DragonflyDB health check and monitoring endpoints.
"""

from flask_openapi3 import APIBlueprint, Tag

from swingmusic.db.dragonfly_client import get_dragonfly_client
from swingmusic.db.dragonfly_extended_client import (
    get_all_dragonfly_services,
    get_job_queue_service,
    get_realtime_service,
    get_search_cache_service,
    get_track_cache_service,
    get_user_session_service,
)

tag = Tag(name="DragonflyDB", description="DragonflyDB cache monitoring")
api = APIBlueprint("dragonfly", __name__, url_prefix="/dragonfly", abp_tags=[tag])


@api.get("/health")
def health_check():
    """
    Check DragonflyDB connection health.

    Returns basic connectivity status and response time.
    """
    client = get_dragonfly_client()

    if not client.is_available():
        return {
            "status": "unavailable",
            "connected": False,
            "message": "DragonflyDB is not available or not configured",
        }, 503

    try:
        # Measure ping response time
        import time

        start = time.time()
        pong = client.ping()
        latency_ms = round((time.time() - start) * 1000, 2)

        return {
            "status": "healthy",
            "connected": True,
            "latency_ms": latency_ms,
            "ping": pong,
        }
    except Exception as e:
        return {
            "status": "error",
            "connected": False,
            "message": str(e),
        }, 503


@api.get("/stats")
def get_stats():
    """
    Get DragonflyDB statistics and memory usage.

    Returns detailed information about cache usage, memory, and performance.
    """
    client = get_dragonfly_client()

    if not client.is_available():
        return {"error": "DragonflyDB is not available"}, 503

    try:
        info = client.info()

        # Extract relevant stats
        stats = {
            "memory": {
                "used_memory": info.get("used_memory_human", "Unknown"),
                "used_memory_peak": info.get("used_memory_peak_human", "Unknown"),
                "used_memory_rss": info.get("used_memory_rss_human", "Unknown"),
                "memory_fragmentation_ratio": info.get("mem_fragmentation_ratio", 0),
            },
            "clients": {
                "connected_clients": info.get("connected_clients", 0),
                "blocked_clients": info.get("blocked_clients", 0),
            },
            "stats": {
                "total_connections_received": info.get("total_connections_received", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "instantaneous_ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "hit_rate": _calculate_hit_rate(
                    info.get("keyspace_hits", 0), info.get("keyspace_misses", 0)
                ),
            },
            "cpu": {
                "used_cpu_sys": info.get("used_cpu_sys", 0),
                "used_cpu_user": info.get("used_cpu_user", 0),
            },
            "uptime_seconds": info.get("uptime_in_seconds", 0),
            "version": info.get(
                "dragonfly_version", info.get("redis_version", "Unknown")
            ),
        }

        return stats
    except Exception as e:
        return {"error": str(e)}, 500


@api.get("/services")
def get_services_status():
    """
    Get status of all DragonflyDB cache services.

    Returns information about each cache namespace and its usage.
    """
    client = get_dragonfly_client()

    if not client.is_available():
        return {"error": "DragonflyDB is not available"}, 503

    get_all_dragonfly_services()

    service_stats = {}

    # Track cache stats
    track_service = get_track_cache_service()
    track_keys = client.keys("tracks:*")
    service_stats["track_cache"] = {
        "available": track_service.cache.client.is_available(),
        "cached_tracks": len(track_keys),
    }

    # Search cache stats
    search_service = get_search_cache_service()
    search_keys = client.keys("search:*")
    service_stats["search_cache"] = {
        "available": search_service.cache.client.is_available(),
        "cached_searches": len(search_keys),
    }

    # Session cache stats
    session_service = get_user_session_service()
    session_keys = client.keys("sessions:*")
    service_stats["session_cache"] = {
        "available": session_service.cache.client.is_available(),
        "active_sessions": len(session_keys),
    }

    # Realtime features stats
    realtime_service = get_realtime_service()
    playcount_keys = client.keys("playcounts:*")
    recent_keys = client.keys("recent:*")
    favorite_keys = client.keys("favorites:*")
    service_stats["realtime_features"] = {
        "available": realtime_service.playcount_cache.client.is_available(),
        "playcount_entries": len(playcount_keys),
        "recent_lists": len(recent_keys),
        "favorite_entries": len(favorite_keys),
    }

    # Job queue stats
    job_service = get_job_queue_service()
    download_queue_size = job_service.get_queue_size("downloads")
    service_stats["job_queue"] = {
        "available": job_service.cache.client.is_available(),
        "download_queue_size": download_queue_size,
    }

    return {
        "services": service_stats,
        "total_keys": len(client.keys("*")),
    }


@api.get("/keys")
def get_key_stats():
    """
    Get statistics about cached keys by namespace.

    Returns count of keys in each cache namespace.
    """
    client = get_dragonfly_client()

    if not client.is_available():
        return {"error": "DragonflyDB is not available"}, 503

    namespaces = [
        "tracks",
        "artists",
        "albums",
        "sessions",
        "users",
        "search",
        "homepage",
        "mobile",
        "sync",
        "progress",
        "playlists",
        "playcounts",
        "recent",
        "favorites",
        "recommendations",
        "jobs",
        "lyrics",
        "index",
        "temp",
    ]

    key_stats = {}
    total = 0

    for namespace in namespaces:
        keys = client.keys(f"{namespace}:*")
        count = len(keys)
        key_stats[namespace] = count
        total += count

    key_stats["total"] = total

    return key_stats


@api.post("/clear/<namespace>")
def clear_namespace(namespace: str):
    """
    Clear all keys in a specific cache namespace.

    Use with caution - this will remove all cached data for the namespace.
    """
    client = get_dragonfly_client()

    if not client.is_available():
        return {"error": "DragonflyDB is not available"}, 503

    # Validate namespace to prevent accidental data loss
    allowed_namespaces = [
        "search",
        "homepage",
        "temp",
        "recommendations",
        "index",
    ]

    if namespace not in allowed_namespaces:
        return {
            "error": f"Cannot clear namespace '{namespace}'. Allowed namespaces: {allowed_namespaces}"
        }, 400

    try:
        keys = client.keys(f"{namespace}:*")
        if keys:
            deleted = client.delete(*keys)
            return {
                "success": True,
                "namespace": namespace,
                "keys_deleted": deleted,
            }
        return {
            "success": True,
            "namespace": namespace,
            "keys_deleted": 0,
            "message": "No keys found in namespace",
        }
    except Exception as e:
        return {"error": str(e)}, 500


def _calculate_hit_rate(hits: int, misses: int) -> float:
    """Calculate cache hit rate percentage"""
    total = hits + misses
    if total == 0:
        return 0.0
    return round((hits / total) * 100, 2)

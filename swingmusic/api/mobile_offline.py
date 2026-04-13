"""Mobile offline sync API."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from swingmusic.services.mobile_offline_service import mobile_offline_service
from swingmusic.utils.auth import get_current_userid

mobile_offline_bp = Blueprint(
    "mobile_offline", __name__, url_prefix="/api/mobile-offline"
)


def _ok(payload: dict[str, Any], status: int = 200):
    return payload, status


def _fail(message: str, status: int = 400):
    return {"error": message}, status


@mobile_offline_bp.post("/devices/register")
@jwt_required()
def register_device():
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    try:
        device = mobile_offline_service.register_device(userid, body)
    except Exception as error:
        return _fail(f"Failed to register device: {error}", 500)

    return _ok({"device": device}, 201)


@mobile_offline_bp.get("/devices")
@jwt_required()
def get_devices():
    userid = get_current_userid()
    devices = mobile_offline_service.list_devices(userid)
    return _ok({"devices": devices, "total_count": len(devices)})


@mobile_offline_bp.get("/devices/<device_id>")
@jwt_required()
def get_device(device_id: str):
    userid = get_current_userid()
    device = mobile_offline_service.get_device(userid, device_id)
    if not device:
        return _fail("Device not found", 404)
    return _ok({"device": device})


@mobile_offline_bp.put("/devices/<device_id>/settings")
@jwt_required()
def update_device_settings(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    success = mobile_offline_service.update_device_settings(userid, device_id, body)
    if not success:
        return _fail("Device not found", 404)

    return _ok({"success": True})


@mobile_offline_bp.get("/devices/<device_id>/offline-library")
@jwt_required()
def get_offline_library(device_id: str):
    userid = get_current_userid()
    try:
        payload = mobile_offline_service.get_offline_library(userid, device_id)
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to get offline library: {error}", 500)

    return _ok({"offline_library": payload})


@mobile_offline_bp.post("/devices/<device_id>/add-tracks")
@jwt_required()
def add_tracks_to_offline(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    track_items = body.get("tracks") or body.get("track_ids") or []
    if not isinstance(track_items, list) or not track_items:
        return _fail("tracks or track_ids must be a non-empty list", 400)

    quality = body.get("quality")
    collection = body.get("collection")

    try:
        queue_items = mobile_offline_service.add_to_offline_library(
            userid,
            device_id,
            track_items,
            quality=quality,
            collection=collection,
        )
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to add tracks: {error}", 500)

    return _ok(
        {
            "success": True,
            "queue_items": queue_items,
            "added_count": len(queue_items),
        }
    )


@mobile_offline_bp.post("/devices/<device_id>/sync-playlist/<playlist_id>")
@jwt_required()
def sync_playlist_offline(device_id: str, playlist_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    try:
        queue_items = mobile_offline_service.sync_playlist_offline(
            userid,
            device_id,
            playlist_id,
            quality=body.get("quality"),
        )
    except ValueError as error:
        return _fail(str(error), 400)
    except Exception as error:
        return _fail(f"Failed to sync playlist: {error}", 500)

    return _ok(
        {"success": True, "queue_items": queue_items, "added_count": len(queue_items)}
    )


@mobile_offline_bp.post("/devices/<device_id>/sync-collection")
@jwt_required()
def sync_collection_offline(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    collection_type = str(body.get("collection_type") or "").strip().lower()
    collection_id = str(body.get("collection_id") or "").strip()
    quality = body.get("quality")

    if collection_type not in {"album", "artist", "playlist"}:
        return _fail("collection_type must be one of: album, artist, playlist", 400)
    if not collection_id:
        return _fail("collection_id is required", 400)

    trackhashes = mobile_offline_service.tracks_for_collection(
        collection_type=collection_type,
        collection_id=collection_id,
    )
    if not trackhashes:
        return _fail("No tracks found for collection", 404)

    try:
        queue_items = mobile_offline_service.add_to_offline_library(
            userid,
            device_id,
            trackhashes,
            quality=quality,
            collection=f"{collection_type}:{collection_id}",
        )
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to sync collection: {error}", 500)

    return _ok(
        {"success": True, "queue_items": queue_items, "added_count": len(queue_items)}
    )


@mobile_offline_bp.post("/devices/<device_id>/remove-tracks")
@jwt_required()
def remove_tracks_from_offline(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    trackhashes = body.get("trackhashes") or body.get("track_ids") or []
    if not isinstance(trackhashes, list) or not trackhashes:
        return _fail("trackhashes or track_ids must be a non-empty list", 400)

    success = mobile_offline_service.remove_from_offline_library(
        userid, device_id, trackhashes
    )
    if not success:
        return _fail("Device not found", 404)

    return _ok({"success": True, "removed_count": len(trackhashes)})


@mobile_offline_bp.get("/devices/<device_id>/sync-progress")
@jwt_required()
def get_sync_progress(device_id: str):
    userid = get_current_userid()

    try:
        progress = mobile_offline_service.get_sync_progress(userid, device_id)
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to fetch sync progress: {error}", 500)

    return _ok({"sync_progress": progress})


@mobile_offline_bp.post("/devices/<device_id>/force-sync")
@jwt_required()
def force_sync_now(device_id: str):
    userid = get_current_userid()
    success = mobile_offline_service.force_sync_now(userid, device_id)
    if not success:
        return _fail("Device not found", 404)
    return _ok({"success": True})


@mobile_offline_bp.get("/devices/<device_id>/storage-info")
@jwt_required()
def get_storage_info(device_id: str):
    userid = get_current_userid()
    try:
        usage = mobile_offline_service.get_storage_usage(userid, device_id)
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to get storage info: {error}", 500)

    usage_percentage = 0.0
    if usage.total_capacity > 0:
        usage_percentage = round((usage.used_space / usage.total_capacity) * 100.0, 2)

    return _ok(
        {
            "storage_info": {
                "total_capacity": usage.total_capacity,
                "used_space": usage.used_space,
                "available_space": usage.available_space,
                "usage_percentage": usage_percentage,
                "offline_tracks_count": usage.offline_tracks_count,
                "offline_tracks_size": usage.offline_tracks_size,
                "other_data_size": usage.other_data_size,
                "quality_breakdown": usage.quality_breakdown,
                "needs_cleanup": usage_percentage >= 90.0,
            }
        }
    )


@mobile_offline_bp.post("/devices/<device_id>/cleanup")
@jwt_required()
def cleanup_storage(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    strategy = str(body.get("strategy") or "least_played")
    if strategy not in {"least_played", "oldest", "all"}:
        return _fail("strategy must be one of: least_played, oldest, all", 400)

    free_space_bytes = int(body.get("free_space_bytes") or 0)

    freed = mobile_offline_service.cleanup_device_content(
        userid,
        device_id,
        strategy=strategy,
        free_space_bytes=free_space_bytes,
    )

    return _ok({"success": True, "freed_space": freed, "strategy": strategy})


@mobile_offline_bp.post("/devices/<device_id>/events/batch")
@jwt_required()
def append_events(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    events = body.get("events")
    if not isinstance(events, list):
        return _fail("events must be a list", 400)

    try:
        result = mobile_offline_service.append_events(userid, device_id, events)
    except ValueError as error:
        return _fail(str(error), 404)
    except Exception as error:
        return _fail(f"Failed to append events: {error}", 500)

    mark_synced = body.get("mark_synced")
    if isinstance(mark_synced, list):
        mobile_offline_service.mark_events_synced(userid, device_id, mark_synced)

    return _ok({"success": True, **result})


@mobile_offline_bp.post("/devices/<device_id>/events/mark-synced")
@jwt_required()
def mark_events_synced(device_id: str):
    body = request.get_json(silent=True) or {}
    userid = get_current_userid()

    event_ids = body.get("event_ids")
    if event_ids is not None and not isinstance(event_ids, list):
        return _fail("event_ids must be a list", 400)

    updated = mobile_offline_service.mark_events_synced(userid, device_id, event_ids)
    return _ok({"success": True, "updated": updated})


@mobile_offline_bp.get("/quality-presets")
@jwt_required()
def get_quality_presets():
    return _ok({"quality_presets": mobile_offline_service.quality_presets()})
